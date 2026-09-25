"""Answer a daily "Up or Down" prediction-market question (Polymarket / Binance Wallet style).

  python scripts/updown.py BTC                          # the next question to settle
  python scripts/updown.py BTC --as-of 2026-09-22T16:00 # replay: what it said back then

Question rules: Up if the Binance {SYM}/USDT 1-minute close at 12:00 ET on the settle day is
higher than the 1-minute close at 12:00 ET the day before. The market named "on <date>"
settles at noon ET on <date>.

P(Up) = base + tilt
  base  chance of finishing above the target from where price is now, given the remaining
        time and recent 1h volatility (random walk). 50% at the window's start.
  tilt  technical lean: average weighted composite of the tv-ta checklist on the
        timeframes in profile.toml [updown], times `scale` percentage points per point.
The scale is not calibrated yet; treat the result as a lean, not a price.
"""

import argparse
import datetime as dt
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_ohlcv  # noqa: E402
import run  # noqa: E402
from nodes import fmt  # noqa: E402

UTC = dt.timezone.utc


def _nth_sunday(year, month, n):
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def noon_et(day):
    """12:00 America/New_York on `day`, in UTC. US DST: 2nd Sunday of March to 1st Sunday of November."""
    dst = _nth_sunday(day.year, 3, 2) <= day < _nth_sunday(day.year, 11, 1)
    return dt.datetime(day.year, day.month, day.day, 16 if dst else 17, tzinfo=UTC)


def question_window(now):
    """The next question to settle: (start, settle) as UTC datetimes."""
    et_today = (now - dt.timedelta(hours=4)).date()  # ET date is within a day either way
    for d in (et_today - dt.timedelta(days=1), et_today, et_today + dt.timedelta(days=1)):
        if noon_et(d) > now:
            return noon_et(d - dt.timedelta(days=1)), noon_et(d)
    raise RuntimeError("no settle time found")


def minute_close(pair, at):
    """Close of the Binance 1m candle that opens at `at`."""
    ms = int(at.timestamp() * 1000)
    for host in fetch_ohlcv.HOSTS:
        try:
            k = fetch_ohlcv._get(f"{host}/api/v3/klines?symbol={pair}&interval=1m&startTime={ms}&limit=1")
            if k and k[0][0] == ms:
                return float(k[0][4])
        except OSError:
            continue
    raise fetch_ohlcv.FetchError(f"no 1m candle for {pair} at {at:%Y-%m-%d %H:%M} UTC")


# Where each checklist node sits in the crypto-watch TradingView widget pages.
# Section A = {sym}.html (indicators.js column1..4), Section B = o/{sym}.html (analysisGroups group1..4).
WIDGET_LAYOUT = {
    "A": [("第 1 欄：MTP · Fractals · Alligator · PSAR", ["mtp", "fractals", "alligator", "alligator_x_fractal", "psar"]),
          ("第 2 欄：BB · KC · MA Cross · Vol Stop", ["bb", "kc", "squeeze", "ma_cross", "vol_stop"]),
          ("第 3 欄：SMA 20/50 · EMA 20/50 · Donchian", ["ma_20_50", "ema_20_50", "donchian", "ma_x_ema"]),
          ("第 4 欄：Zig Zag · Supertrend · LinReg · VWMA", ["zigzag", "supertrend", "linreg", "sar_x_linreg", "vwma"])],
    "B": [("第 1 組：Supertrend · MACD · DMI · CCI", ["supertrend_b", "macd", "dmi", "cci", "supertrend_x_macd"]),
          ("第 2 組：HMA · RSI · Stoch RSI · UO", ["hma", "rsi", "stoch_rsi", "uo"]),
          ("第 3 組：BB · ATR · CI · HV", ["bb_b", "atr", "choppiness", "hv"]),
          ("第 4 組：VWMA · OBV · MFI · CMF", ["vwma_b", "obv", "mfi", "cmf"])],
}
CELL = {"BUY": "▲", "WAIT": "─", "SELL": "▼", "UNCHECKED": "·"}


def widget_rows(summary):
    """Rows of one timeframe keyed by node id, grouped the way the widget pages show them."""
    return {r["id"]: r for sec in ("A", "B") for r in summary["sections"][sec]["rows"]}


def cell(r):
    if r is None:
        return "停用"
    return CELL[r["signal"]] if r["score"] is None else f"{CELL[r['signal']]} {r['score']:+g}"


def render_widget_detail(symbol, scores, with_evidence):
    s = symbol.lower()
    page_a = f"{s}.html" if s in ("btc", "eth") else f"altcoin.html?s={symbol}"
    page_b = f"o/{s}.html" if s in ("btc", "eth") else f"o/altcoin.html?s={symbol}"
    tfs = list(scores)
    by_tf = {tf: widget_rows(scores[tf]) for tf in tfs}
    out = ["## TradingView widget 逐項檢核", "",
           "▲ 偏多　─ 中性　▼ 偏空　· 資料不足；數字是該項分數（-2 到 +2）。", ""]
    titles = {"A": f"主頁 {page_a}（四欄 widget）", "B": f"分析頁 {page_b}（四組 widget）"}
    for sec in ("A", "B"):
        out += [f"### {titles[sec]}", "", "| 欄位 | 項目 | " + " | ".join(tf.upper() for tf in tfs) + " |",
                "|---|---|" + "---|" * len(tfs)]
        for col, ids in WIDGET_LAYOUT[sec]:
            for i, nid in enumerate(ids):
                row0 = next((by_tf[tf].get(nid) for tf in tfs if by_tf[tf].get(nid)), None)
                if row0 is None:
                    continue
                label = col if i == 0 else ""
                out.append(f"| {label} | {row0['name']} | " + " | ".join(cell(by_tf[tf].get(nid)) for tf in tfs) + " |")
        tally = {tf: {k: sum(1 for r in scores[tf]["sections"][sec]["rows"] if r["signal"] == k) for k in CELL} for tf in tfs}
        out.append("| | **小計 ▲/─/▼** | " + " | ".join(f"{t['BUY']}/{t['WAIT']}/{t['SELL']}" for t in tally.values()) + " |")
        out.append("")
    if with_evidence:
        out += ["## 各級別證據", ""]
        for tf in tfs:
            out += [f"### {tf.upper()}", "", "| 項目 | 判定 | 證據 |", "|---|---|---|"]
            for sec in ("A", "B"):
                for col, ids in WIDGET_LAYOUT[sec]:
                    for nid in ids:
                        r = by_tf[tf].get(nid)
                        if r:
                            out.append(f"| {sec}·{r['name']} | {cell(r)} | {r['evidence']} |")
            out.append("")
    return "\n".join(out)


def binance_url(symbol):
    """Binance prediction page for the daily question. The BTC slug is confirmed; other symbols follow the same pattern."""
    return f"https://www.binance.com/zh-TC/prediction/market-detail/{symbol.lower()}-up-or-down-1d"


def normal_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def main(argv=None):
    ap = argparse.ArgumentParser(description="tv-ta: daily Up or Down question")
    ap.add_argument("symbol", nargs="?", default="BTC")
    ap.add_argument("--as-of", help="replay a past moment (UTC), e.g. 2026-09-22T16:00")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--brief", action="store_true", help="omit the per-timeframe evidence tables")
    ap.add_argument("--no-user-config", action="store_true")
    args = ap.parse_args(argv)

    symbol = args.symbol.upper().replace("USDT", "") or "BTC"
    if args.as_of:
        try:
            now = dt.datetime.fromisoformat(args.as_of.rstrip("Z"))
        except ValueError:
            print(f"bad --as-of {args.as_of!r}; use e.g. 2026-09-22T16:00 (UTC)", file=sys.stderr)
            return 2
        now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    else:
        now = dt.datetime.now(UTC)
    as_of = int(now.timestamp() * 1000) if args.as_of else None

    try:
        nodes, profile, layers = run.load_config(symbol, None, None if args.no_user_config else run.user_config_dir())
    except (OSError, ValueError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    cfg = {"timeframes": ["1h", "4h", "1d"], "scale": 5.0, "max_tilt": 10.0,
           "display_utc_offset": 8, "display_tz_name": "台灣時間", **profile.get("updown", {})}
    local = dt.timezone(dt.timedelta(hours=cfg["display_utc_offset"]))
    htf_map = profile["timeframes"]["htf"]
    start, settle = question_window(now)
    pair = fetch_ohlcv.to_pair(symbol)

    try:
        target = minute_close(pair, start)
        h1 = fetch_ohlcv.fetch(symbol, "1h", 200, live=not as_of, as_of=as_of)
        price = minute_close(pair, now.replace(second=0, microsecond=0) - dt.timedelta(minutes=1)) \
            if as_of else h1["close"][-1]
        limit = profile["timeframes"].get("bars", 500)
        wanted = {tf: ((tf, limit, False), (htf_map[tf], 500, not as_of)) for tf in cfg["timeframes"]}
        bars = fetch_ohlcv.fetch_many(symbol, [r for pair in wanted.values() for r in pair], as_of=as_of)
        scores = {}
        for tf, (main, higher) in wanted.items():
            _, _, summary, _, _ = run.analyse(symbol, tf, nodes, profile, bars[main], bars[higher], use_jev=False)
            scores[tf] = summary
    except fetch_ohlcv.FetchError as e:
        print(f"market data error: {e}", file=sys.stderr)
        return 3

    hours_left = (settle - now).total_seconds() / 3600
    rets = [math.log(b / a) for a, b in zip(h1["close"][-101:-1], h1["close"][-100:])]
    mean = sum(rets) / len(rets)
    sigma_h = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
    dist = math.log(price / target)
    base = normal_cdf(dist / (sigma_h * math.sqrt(max(hours_left, 1e-6)))) * 100
    comps = [s["composite"] for s in scores.values() if s["composite"] is not None]
    avg = sum(comps) / len(comps) if comps else 0.0
    tilt = max(-cfg["max_tilt"], min(cfg["max_tilt"], cfg["scale"] * avg))
    p_up = max(1.0, min(99.0, base + tilt))
    lean = "Up" if p_up > 50 else "Down" if p_up < 50 else "無偏向"

    day = (settle - dt.timedelta(hours=12)).date()
    tz = cfg["display_tz_name"]
    start_l, settle_l = start.astimezone(local), settle.astimezone(local)
    result = {
        "question": f"{symbol} 1天內漲或跌",
        "url": binance_url(symbol),
        "polymarket_name": f"{symbol} Up or Down on {day:%B} {day.day}, {day.year}",
        "start_local": f"{start_l:%Y-%m-%d %H:%M}", "settle_local": f"{settle_l:%Y-%m-%d %H:%M}", "local_tz": tz,
        "start_utc": f"{start:%Y-%m-%d %H:%M}", "settle_utc": f"{settle:%Y-%m-%d %H:%M}",
        "as_of": f"{now:%Y-%m-%d %H:%M}", "replay": bool(as_of),
        "target": target, "price": price, "hours_left": round(hours_left, 2),
        "base_pct": round(base, 1), "tilt_pp": round(tilt, 1), "p_up_pct": round(p_up, 1), "lean": lean,
        "timeframes": {tf: {"composite": s["composite"], "verdict": s["verdict"],
                            "items": {r["id"]: {"name": r["name"], "signal": r["signal"], "score": r["score"],
                                                "evidence": r["evidence"]}
                                      for r in widget_rows(s).values()}}
                       for tf, s in scores.items()},
        "config_layers": layers,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    sys.stdout.reconfigure(encoding="utf-8")
    tf_rows = "\n".join(f"| {tf.upper()} | {s['composite']:+.2f} | {s['verdict']} |" for tf, s in scores.items())
    print(f"""# {result['question']}：結束時間 {result['settle_local']} {tz}

{'**回放模式**：以 ' + result['as_of'] + ' UTC 當下看得到的資料作答' + chr(10) if as_of else ''}
題目頁面：{result['url']}

| 項目 | 值 |
|---|---|
| 題目 | {result['question']}：結束時的價格是否高於目標價（開始時間的 1 分鐘收盤價） |
| 開始時間 | {result['start_local']} {tz}（{result['start_utc']} UTC） |
| 結束時間 | {result['settle_local']} {tz}（{result['settle_utc']} UTC） |
| 價格來源 | Binance {pair} 1 分鐘 K 線收盤價 |
| 目標價 | {fmt(target)} |
| 現價 | {fmt(price)}（{(price / target - 1) * 100:+.2f}%） |
| 距離結束 | {hours_left:.1f} 小時 |

| 級別 | 加權分數 | 判斷 |
|---|---|---|
{tf_rows}

{render_widget_detail(symbol, scores, not args.brief)}
| 機率拆解 | 值 |
|---|---|
| 基準（依現價與目標價的距離、剩餘時間、1h 波動度） | {base:.1f}% |
| 技術面修正（分數平均 {avg:+.2f} × {cfg['scale']:g}pp） | {tilt:+.1f}pp |
| **P(Up)** | **{p_up:.1f}% → 偏 {lean}** |

> 技術面修正的係數尚未校準，結果是偏向參考，不是可下注的價格；非投資建議。""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
