"""Backtest the daily "Up or Down" answer at several points inside each question window.

  python scripts/backtest_updown.py BTC --days 700
  python scripts/backtest_updown.py BTC --days 300 --hours-left 24,16,8,2 --json

replay.py --updown only judges at the question's start, where the volatility base is 50% and the
answer is the technical lean alone. In real use the question is often asked mid-window, when the
distance between price and target already carries most of P(Up). This script asks the same
question at each `--hours-left` point and scores three answers against the settle result:

  base      volatility base alone (random walk from the current distance to the target)
  tech      sign of the average composite of the [updown] timeframes
  combined  base + tilt, exactly as updown.py computes P(Up)

Only candles closed before the decision time are used. Target and settle prices are the open of
the 1h candle starting at that minute, a stand-in for the 1-minute close the market uses.
"""

import argparse
import bisect
import datetime as dt
import json
import math
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_ohlcv  # noqa: E402
import replay  # noqa: E402
import run  # noqa: E402
import updown  # noqa: E402

HOUR_MS = 3_600_000
SIGMA_BARS = 100  # same 1h-return window updown.py uses
Z95 = 1.96


@dataclass(frozen=True)
class Sample:
    day: str
    hours_left: int
    base_up: float | None  # base P(Up) in percent
    tech_avg: float | None
    p_up: float | None
    up: bool


def hourly_sigma(closes: list[float]) -> float:
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    mean = sum(rets) / len(rets)
    return math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))


def base_probability(price: float, target: float, sigma_h: float, hours_left: float) -> float:
    """P(settle > target) in percent, random walk without drift (same formula as updown.py)."""
    z = math.log(price / target) / (sigma_h * math.sqrt(max(hours_left, 1e-6)))
    return updown.normal_cdf(z) * 100


def tilt(avg: float, scale: float, max_tilt: float) -> float:
    return max(-max_tilt, min(max_tilt, scale * avg))


def combine(base: float, avg: float, scale: float, max_tilt: float) -> float:
    return max(1.0, min(99.0, base + tilt(avg, scale, max_tilt)))


def hit_rate(pairs: list[tuple[bool, bool]]) -> dict:
    """pairs of (predicted_up, actual_up) -> n, hit rate and the 95% half-width."""
    n = len(pairs)
    if not n:
        return {"n": 0, "hit": None, "ci": None}
    p = sum(a == b for a, b in pairs) / n
    return {"n": n, "hit": p, "ci": Z95 * math.sqrt(p * (1 - p) / n)}


def score(samples: list[Sample]) -> dict:
    """Hit rate of each answer; a tie at exactly 50% / 0 is not an answer and is left out."""
    def rate(pick):
        return hit_rate([(pick(s), s.up) for s in samples if pick(s) is not None])
    return {
        "base": rate(lambda s: None if s.base_up is None or s.base_up == 50 else s.base_up > 50),
        "tech": rate(lambda s: None if not s.tech_avg else s.tech_avg > 0),
        "combined": rate(lambda s: None if s.p_up is None or s.p_up == 50 else s.p_up > 50),
    }


def build_samples(data, profile, nodes, symbol, days, hours_left) -> list[Sample]:
    tfs, main, htf = data
    limit = profile["timeframes"].get("bars", 500)
    cfg = {"scale": 5.0, "max_tilt": 10.0, **profile.get("updown", {})}
    h1 = main["1h"]
    opens = dict(zip(h1["time"], h1["open"]))
    last = dt.datetime.fromtimestamp(h1["time"][-1] / 1000, dt.timezone.utc).date()
    out = []
    for back in range(days, 0, -1):
        day = last - dt.timedelta(days=back)
        start, settle = (int(updown.noon_et(d).timestamp() * 1000) for d in (day, day + dt.timedelta(days=1)))
        if start not in opens or settle not in opens:
            continue
        target, up = opens[start], opens[settle] > opens[start]
        for h in hours_left:
            # A window is 23 or 25 hours across a US DST change; 24 always means the question's start.
            at = start if h >= 24 else max(start, settle - h * HOUR_MS)
            price = opens.get(at)
            end1 = bisect.bisect_left(h1["close_time"], at)
            if price is None or end1 < SIGMA_BARS + 1:
                continue
            left = (settle - at) / HOUR_MS
            base = base_probability(price, target, hourly_sigma(h1["close"][end1 - SIGMA_BARS - 1:end1]), left)
            comps = _composites(tfs, main, htf, nodes, profile, symbol, at, limit)
            avg = sum(comps) / len(comps) if comps else None
            p_up = combine(base, avg, cfg["scale"], cfg["max_tilt"]) if avg is not None else None
            out.append(Sample(str(day + dt.timedelta(days=1)), h, base, avg, p_up, up))
    return out


def _composites(tfs, main, htf, nodes, profile, symbol, at, limit) -> list[float]:
    comps = []
    for tf in tfs:
        b, h = main[tf], htf[tf]
        end = bisect.bisect_left(b["close_time"], at)  # candles closed before the decision time
        if end < limit:
            return []
        closed = bisect.bisect_left(h["close_time"], at)
        _, _, s, _, _ = run.analyse(symbol, tf, nodes, profile, replay.window(b, end - limit, end),
                                    replay.window(h, max(0, closed - 500), closed), False)
        if s["composite"] is not None:
            comps.append(s["composite"])
    return comps


def report(samples: list[Sample], hours_left: list[int]) -> dict:
    out = {}
    for h in hours_left:
        rows = [s for s in samples if s.hours_left == h]
        half = len(rows) // 2
        out[h] = {"questions": len(rows), "actual_up": sum(s.up for s in rows) / len(rows) if rows else None,
                  "all": score(rows), "first_half": score(rows[:half]), "second_half": score(rows[half:])}
    return out


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def render(symbol: str, result: dict) -> str:
    def cell(r):
        return "—" if r["hit"] is None else f"{pct(r['hit'])} ±{r['ci'] * 100:.1f}"
    out = [f"# {symbol} 一日漲跌題回測（依作答時點）", "",
           "命中 = 偏向方向和結算結果相同；± 為 95% 信賴區間。", "",
           "| 剩餘時間 | 題數 | 實際 Up | 只看價格距離 | 只看技術面 | 價格 + 技術面 | 技術面貢獻 | 前半 | 後半 |",
           "|---|---|---|---|---|---|---|---|---|"]
    for h, r in result.items():
        a = r["all"]
        gain = None if a["base"]["hit"] is None or a["combined"]["hit"] is None \
            else a["combined"]["hit"] - a["base"]["hit"]
        out.append(f"| {h} 小時 | {r['questions']} | {pct(r['actual_up'])} | {cell(a['base'])} | {cell(a['tech'])} "
                   f"| {cell(a['combined'])} | {'—' if gain is None else f'{gain * 100:+.1f}pp'} "
                   f"| {pct(r['first_half']['combined']['hit'])} | {pct(r['second_half']['combined']['hit'])} |")
    out += ["", "> 「技術面貢獻」= 價格 + 技術面 減 只看價格距離；接近 0 代表技術面沒有增加預測力。",
            "> 24 小時是題目剛開始，價格距離為 0，此時只有技術面在作答。"]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backtest daily Up/Down answers at several decision times.")
    ap.add_argument("symbol", nargs="?", default="BTC")
    ap.add_argument("--days", type=int, default=300)
    ap.add_argument("--hours-left", default="24,16,8,2", help="hours before settle, comma separated")
    ap.add_argument("--no-user-config", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    symbol = args.symbol.upper().replace("USDT", "") or "BTC"
    try:
        hours_left = sorted({int(x) for x in args.hours_left.split(",")}, reverse=True)
    except ValueError:
        print(f"bad --hours-left {args.hours_left!r}; use e.g. 24,16,8,2", file=sys.stderr)
        return 2
    if not hours_left or hours_left[-1] < 1 or hours_left[0] > 24:
        print("--hours-left values must be whole hours between 1 and 24", file=sys.stderr)
        return 2
    try:
        nodes, profile, _ = run.load_config(symbol, None, None if args.no_user_config else run.user_config_dir())
        data = replay.load_updown(symbol, args.days, profile)
    except (OSError, ValueError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    except fetch_ohlcv.FetchError as e:
        print(f"market data error: {e}", file=sys.stderr)
        return 3

    result = report(build_samples(data, profile, nodes, symbol, args.days, hours_left), hours_left)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(symbol, result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
