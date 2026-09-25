"""Replay past bars to calibrate confidence routing (profile.toml [decision.routing]).

  python scripts/replay.py BTC --tf 4h                       # last 400 bars, 24h ahead
  python scripts/replay.py ETH --tf 1h --bars 2000 --horizon 24
  python scripts/replay.py BTC --tf 1d --thresholds 0.5,0.6,0.7 --json
  python scripts/replay.py BTC --tf 4h --compare-node macd_cross   # A/B: without vs with a node
  python scripts/replay.py BTC --updown 700 --compare-node macd_cross  # A/B on daily Up/Down questions

Every past bar is analysed as if it were the latest closed bar: the same window length as a
live run (profile.toml [timeframes] bars), and only higher-timeframe candles already closed at
that moment, so nothing from the future leaks in. The live Multi-Time Period blocks also show
the still-forming higher candle; the replay cannot know its colour then, so MTP can differ.

For each BUY / SELL the replay records the weighted agreement and the return `horizon` bars
later, then shows what each min_agreement threshold would keep and downgrade to WAIT.
Neighbouring bars share most of their forward window, so the effective sample is smaller than
the count shown; compare the two halves before trusting a threshold.

--compare-node ID replays the same bars twice, with the node off and on (other settings as
configured), and also scores the node on its own: every bar where it voted, did price move its way.

--updown DAYS replays the last DAYS daily "Up or Down" questions instead (see updown.py): at each
question's start (12:00 ET) the timeframes in profile.toml [updown] are analysed on candles closed
by then, and the sign of their average composite is the lean. Price at start and settle is the open
of the 1h candle starting at that minute, a close stand-in for the 1-minute close the market uses.
"""

import argparse
import bisect
import copy
import datetime as dt
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import decide  # noqa: E402
import fetch_ohlcv  # noqa: E402
import run  # noqa: E402
import updown  # noqa: E402

BAR_HOURS = {"15m": 0.25, "1h": 1, "4h": 4, "1d": 24}
SERIES = ("time", "close_time", "open", "high", "low", "close", "volume")


def history(symbol, interval, count):
    """The latest `count` closed candles, paged backwards 999 at a time."""
    out, as_of = None, None
    while out is None or len(out["time"]) < count:
        chunk = fetch_ohlcv.fetch(symbol, interval, 999, False, False, as_of)
        if not chunk["time"]:
            if out is None:
                raise fetch_ohlcv.FetchError(f"no {interval} candles for {symbol}")
            break
        out = chunk if out is None else {k: chunk[k] + out[k] if k in SERIES else out[k] for k in out}
        as_of = chunk["time"][0]
    return {k: v[-count:] if k in SERIES else v for k, v in out.items()}


def window(bars, start, end):
    return {k: v[start:end] if k in SERIES else v for k, v in bars.items()}


def stats(rows):
    if not rows:
        return {"n": 0, "hit": None, "mean_ret": None}
    signed = [r["ret"] if r["verdict"] == decide.BUY else -r["ret"] for r in rows]
    return {"n": len(rows), "hit": sum(x > 0 for x in signed) / len(rows), "mean_ret": statistics.mean(signed)}


def load(symbol, tf, n_bars, horizon, profile):
    limit = profile["timeframes"].get("bars", 500)
    main = history(symbol, tf, n_bars + limit + horizon)
    htf = history(symbol, profile["timeframes"]["htf"][tf], 1500)  # MTP reads only the last few
    return main, htf


def replay(main, htf, symbol, tf, n_bars, horizon, nodes, profile, watch=None):
    """BUY / SELL signals over the replayed bars; with `watch`, also every bar that node voted on."""
    limit = profile["timeframes"].get("bars", 500)
    signals, votes, n = [], [], len(main["time"])
    ends = range(max(limit, n - horizon - n_bars + 1), n - horizon + 1)
    for end in ends:
        bars = window(main, end - limit, end)
        closed = sum(1 for t in htf["close_time"] if t < bars["close_time"][-1])
        _, res, s, _, _ = run.analyse(symbol, tf, nodes, profile, bars, window(htf, max(0, closed - 500), closed), False)
        ret = main["close"][end - 1 + horizon] / main["close"][end - 1] - 1
        if s["raw_composite_verdict"] in (decide.BUY, decide.SELL):
            signals.append({"bar_time": run.iso(bars["time"][-1]), "verdict": s["raw_composite_verdict"],
                            "composite": s["composite"], "agreement": s["agreement"], "ret": ret})
        if watch and watch in res and res[watch].score:
            votes.append({"bar_time": run.iso(bars["time"][-1]),
                          "verdict": decide.BUY if res[watch].score > 0 else decide.SELL, "ret": ret})
    return signals, votes, len(ends)


def with_node(nodes, node_id):
    out = copy.deepcopy(nodes)
    hit = [n for n in out if n["id"] == node_id]
    if not hit:
        raise KeyError(f"no node {node_id!r} in nodes.toml")
    hit[0]["enabled"] = True
    return out


def compare(off, on, votes):
    def block(sig):
        half = len(sig) // 2
        return {"all": stats(sig), "first_half": stats(sig[:half]), "second_half": stats(sig[half:])}
    a = {r["bar_time"]: r["verdict"] for r in off}
    b = {r["bar_time"]: r["verdict"] for r in on}
    return {"without": block(off), "with": block(on), "node_alone": block(votes),
            "changed": {"added": sum(1 for t in b if t not in a), "removed": sum(1 for t in a if t not in b),
                        "flipped": sum(1 for t in a if t in b and a[t] != b[t])}}


def load_updown(symbol, days, profile):
    limit, htf_map = profile["timeframes"].get("bars", 500), profile["timeframes"]["htf"]
    tfs = profile.get("updown", {}).get("timeframes", ["1h", "4h", "1d"])
    need = {tf: int(days * 24 / BAR_HOURS[tf]) + limit + 60 for tf in set(tfs) | {"1h"}}
    main = {tf: history(symbol, tf, n) for tf, n in need.items()}
    htf = {tf: history(symbol, htf_map[tf], 1500) for tf in tfs}
    return tfs, main, htf


def replay_updown(data, symbol, days, nodes, profile, watch=None):
    """One record per question: the lean at its start, the outcome, and `watch`'s vote per timeframe."""
    tfs, main, htf = data
    limit = profile["timeframes"].get("bars", 500)
    opens = dict(zip(main["1h"]["time"], main["1h"]["open"]))
    last = dt.datetime.fromtimestamp(main["1h"]["time"][-1] / 1000, dt.timezone.utc).date()
    records = []
    for back in range(days, 0, -1):
        day = last - dt.timedelta(days=back)
        start, settle = (int(updown.noon_et(d).timestamp() * 1000) for d in (day, day + dt.timedelta(days=1)))
        if start not in opens or settle not in opens:
            continue
        comps, votes = [], {}
        for tf in tfs:
            b = main[tf]
            end = bisect.bisect_left(b["close_time"], start)  # candles closed before the question opens
            if end < limit:
                break
            h = htf[tf]
            closed = bisect.bisect_left(h["close_time"], start)
            _, res, s, _, _ = run.analyse(symbol, tf, nodes, profile, window(b, end - limit, end),
                                          window(h, max(0, closed - 500), closed), False)
            if s["composite"] is not None:
                comps.append(s["composite"])
            if watch and watch in res:
                votes[tf] = res[watch].score
        else:
            avg = sum(comps) / len(comps) if comps else 0.0
            records.append({"day": str(day + dt.timedelta(days=1)), "avg": avg, "up": opens[settle] > opens[start],
                            "lean": decide.BUY if avg > 0 else decide.SELL if avg < 0 else None, "votes": votes})
    return records


def updown_stats(records):
    half = len(records) // 2

    def hit(rs):
        leaned = [r for r in rs if r["lean"]]
        return {"n": len(leaned), "hit": sum((r["lean"] == decide.BUY) == r["up"] for r in leaned) / len(leaned)
                if leaned else None}
    return {"questions": len(records), "p_up": sum(r["up"] for r in records) / len(records) if records else None,
            "all": hit(records), "first_half": hit(records[:half]), "second_half": hit(records[half:])}


def node_updown(records, tfs):
    out = {}
    for tf in tfs:
        rows = [(r["votes"].get(tf), r["up"]) for r in records]
        up = [u for v, u in rows if v and v > 0]
        dn = [u for v, u in rows if v and v < 0]
        both = [(v > 0) == u for v, u in rows if v]
        out[tf] = {"n_up_votes": len(up), "p_up_after_up": sum(up) / len(up) if up else None,
                   "n_down_votes": len(dn), "p_up_after_down": sum(dn) / len(dn) if dn else None,
                   "hit": sum(both) / len(both) if both else None}
    return out


def render_updown(symbol, node_id, off, on, per_tf):
    row = lambda name, s: (f"| {name} | {s['all']['n']} | {pct(s['all']['hit'])} "
                           f"| {pct(s['first_half']['hit'])} | {pct(s['second_half']['hit'])} |")
    out = [f"# {symbol} 一日漲跌題回放" + (f"：`{node_id}` A/B" if node_id else ""), "",
           f"{off['questions']} 題，實際 Up 比例 {pct(off['p_up'])}。偏向 = 開題當下 1h / 4h / 1d 加權綜合分數平均的正負號。", "",
           "| 版本 | 有偏向的題數 | 偏向命中 | 前半 | 後半 |", "|---|---|---|---|---|", row("沒導入" if node_id else "目前設定", off)]
    if on:
        out.append(row("有導入", on))
        out += ["", f"`{node_id}` 單獨：", "", "| 級別 | 投 +1 次數 | 之後 Up | 投 −1 次數 | 之後 Up | 方向命中 |",
                "|---|---|---|---|---|---|"]
        for tf, v in per_tf.items():
            out.append(f"| {tf} | {v['n_up_votes']} | {pct(v['p_up_after_up'])} | {v['n_down_votes']} "
                       f"| {pct(v['p_up_after_down'])} | {pct(v['hit'])} |")
    out += ["", "> 每題只在開題當下判斷一次；題數約 100 時，95% 信賴區間約 ±10pp。"]
    return "\n".join(out)


def summarise(signals, thresholds):
    half = len(signals) // 2
    table = []
    for thr in thresholds:
        keep = [r for r in signals if r["agreement"] >= thr]
        drop = [r for r in signals if r["agreement"] < thr]
        table.append({"min_agreement": thr, "keep": stats(keep), "drop": stats(drop),
                      "keep_first_half": stats([r for r in signals[:half] if r["agreement"] >= thr]),
                      "keep_second_half": stats([r for r in signals[half:] if r["agreement"] >= thr])})
    return {"baseline": stats(signals), "baseline_first_half": stats(signals[:half]),
            "baseline_second_half": stats(signals[half:]), "thresholds": table}


def pct(x, signed=False):
    return "—" if x is None else (f"{x * 100:+.2f}%" if signed else f"{x * 100:.0f}%")


def render(symbol, tf, horizon, replayed, signals, summ):
    hours = BAR_HOURS[tf] * horizon
    agrees = [r["agreement"] for r in signals]
    out = [f"# {symbol} · {tf.upper()} 信心度路由回放", "",
           f"回放 {replayed} 根 K 棒（{signals[0]['bar_time'] if signals else '—'} 起），"
           f"BUY/SELL {len(signals)} 次，報酬看 {horizon} 根後（{hours:g} 小時）。"]
    if agrees:
        out.append(f"BUY/SELL 時的加權一致度：{min(agrees):.2f}–{max(agrees):.2f}，中位數 {statistics.median(agrees):.2f}")
    b, b1, b2 = summ["baseline"], summ["baseline_first_half"], summ["baseline_second_half"]
    out += ["", "| 門檻 | 保留 | 命中 | 平均報酬 | 降為 WAIT | 命中 | 平均報酬 | 前半命中 | 後半命中 |",
            "|---|---|---|---|---|---|---|---|---|",
            f"| 不路由 | {b['n']} | {pct(b['hit'])} | {pct(b['mean_ret'], True)} | 0 | — | — | {pct(b1['hit'])} | {pct(b2['hit'])} |"]
    for t in summ["thresholds"]:
        k, d = t["keep"], t["drop"]
        out.append(f"| {t['min_agreement']:g} | {k['n']} | {pct(k['hit'])} | {pct(k['mean_ret'], True)} "
                   f"| {d['n']} | {pct(d['hit'])} | {pct(d['mean_ret'], True)} "
                   f"| {pct(t['keep_first_half']['hit'])} | {pct(t['keep_second_half']['hit'])} |")
    out += ["", "> 命中 = 報酬方向與結論相同；平均報酬以結論方向計（SELL 取負號）。",
            "> 相鄰 K 棒的報酬區間重疊，實際有效樣本比次數少；前後半結果差很多時，門檻不可信。"]
    return "\n".join(out)


def render_compare(symbol, tf, horizon, replayed, node_id, c):
    hours = BAR_HOURS[tf] * horizon
    row = lambda name, b: (f"| {name} | {b['all']['n']} | {pct(b['all']['hit'])} | {pct(b['all']['mean_ret'], True)} "
                           f"| {pct(b['first_half']['hit'])} | {pct(b['second_half']['hit'])} |")
    ch = c["changed"]
    return "\n".join([
        f"# {symbol} · {tf.upper()} 節點 A/B：`{node_id}`", "",
        f"同一批 {replayed} 根 K 棒各跑一次，報酬看 {horizon} 根後（{hours:g} 小時）。", "",
        "| 版本 | BUY/SELL 次數 | 命中 | 平均報酬 | 前半命中 | 後半命中 |", "|---|---|---|---|---|---|",
        row("沒導入", c["without"]), row("有導入", c["with"]), row(f"`{node_id}` 單獨", c["node_alone"]), "",
        f"導入後結論改變：新增 {ch['added']} 次 BUY/SELL、少了 {ch['removed']} 次、方向反轉 {ch['flipped']} 次。", "",
        "> 「單獨」= 這個節點每次投 +1 / −1 時，之後漲跌方向是否和它相同，不經過加權。",
        "> 相鄰 K 棒的報酬區間重疊，實際有效樣本比次數少；前後半結果差很多時，結論不可信。"])


def main(argv=None):
    ap = argparse.ArgumentParser(description="Replay past bars to calibrate confidence routing.")
    ap.add_argument("symbol")
    ap.add_argument("--tf", default="4h", choices=list(BAR_HOURS))
    ap.add_argument("--bars", type=int, default=400, help="how many past bars to replay")
    ap.add_argument("--horizon", type=int, help="bars ahead to score the return (default: 24 hours)")
    ap.add_argument("--thresholds", default="0.5,0.55,0.6,0.65,0.7")
    ap.add_argument("--updown", type=int, metavar="DAYS", help="replay the last DAYS daily Up/Down questions")
    ap.add_argument("--compare-node", metavar="ID", help="A/B the same bars without and with this node enabled")
    ap.add_argument("--no-user-config", action="store_true", help="ignore ~/.tv-ta/config")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    horizon = args.horizon or max(1, round(24 / BAR_HOURS[args.tf]))
    thresholds = [float(x) for x in args.thresholds.split(",")]
    symbol = args.symbol.upper()
    try:
        nodes, profile, _ = run.load_config(symbol, None, None if args.no_user_config else run.user_config_dir())
    except (OSError, ValueError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    try:
        nodes_on = with_node(nodes, args.compare_node) if args.compare_node else None
    except KeyError as e:
        print(f"config error: {e.args[0]}", file=sys.stderr)
        return 2
    if args.updown:
        try:
            data = load_updown(symbol, args.updown, profile)
        except fetch_ohlcv.FetchError as e:
            print(f"market data error: {e}", file=sys.stderr)
            return 3
        off = replay_updown(data, symbol, args.updown, nodes, profile)
        on = replay_updown(data, symbol, args.updown, nodes_on, profile, args.compare_node) if nodes_on else None
        result = {"symbol": symbol, "node": args.compare_node, "without": updown_stats(off),
                  "with": on and updown_stats(on), "node_alone": on and node_updown(on, data[0])}
        sys.stdout.reconfigure(encoding="utf-8")
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(render_updown(symbol, args.compare_node, result["without"], result["with"], result["node_alone"]))
        return 0
    try:
        main_bars, htf = load(symbol, args.tf, args.bars, horizon, profile)
    except fetch_ohlcv.FetchError as e:
        print(f"market data error: {e}", file=sys.stderr)
        return 3
    signals, _, replayed = replay(main_bars, htf, symbol, args.tf, args.bars, horizon, nodes, profile)
    sys.stdout.reconfigure(encoding="utf-8")

    if nodes_on:
        on, votes, _ = replay(main_bars, htf, symbol, args.tf, args.bars, horizon, nodes_on, profile, args.compare_node)
        c = compare(signals, on, votes)
        if args.json:
            print(json.dumps({"symbol": symbol, "tf": args.tf, "horizon": horizon, "replayed": replayed,
                              "node": args.compare_node, "compare": c}, ensure_ascii=False, indent=2))
        else:
            print(render_compare(symbol, args.tf, horizon, replayed, args.compare_node, c))
        return 0

    summ = summarise(signals, thresholds)
    if args.json:
        print(json.dumps({"symbol": symbol, "tf": args.tf, "horizon": horizon, "replayed": replayed,
                          "summary": summ, "signals": signals}, ensure_ascii=False, indent=2))
    else:
        print(render(symbol, args.tf, horizon, replayed, signals, summ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
