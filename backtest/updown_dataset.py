"""Build the historical dataset of daily "Up or Down" questions: one CSV row per settled question.

  python backtest/updown_dataset.py BTC ETH --days 730
  python backtest/updown_dataset.py BTC --out-dir data/updown

Each question compares the Binance 1-minute close at 12:00 ET on the settle day with 12:00 ET the day
before (see updown.py). Times are stored in UTC (for joins and statistics) and in Taiwan time (what
the Binance page shows), so a row can be checked against the question page by eye.

The file is incremental: rows already present are kept, only missing days are fetched. Re-running
daily appends the newest settled question.

Columns
  symbol, question_date (the "on <date>" in US Eastern), start_utc, settle_utc, start_tw, settle_tw,
  target (1m close at start), settle_price (1m close at settle), result (Up / Down),
  return_pct (settle / target - 1, in percent).
A settle price equal to the target counts as Down: the question asks for "higher than the target".
"""

import argparse
import csv
import datetime as dt
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import skill_path  # noqa: F401  (puts skills/tv-ta/scripts on sys.path)

import fetch_ohlcv  # noqa: E402
import updown  # noqa: E402

UTC = dt.timezone.utc
TAIWAN = dt.timezone(dt.timedelta(hours=8))
FIELDS = ("symbol", "question_date", "start_utc", "settle_utc", "start_tw", "settle_tw",
          "target", "settle_price", "result", "return_pct")
UTC_FMT = "%Y-%m-%dT%H:%MZ"
TW_FMT = "%Y-%m-%d %H:%M"
WORKERS = 8


@dataclass(frozen=True)
class Window:
    day: dt.date  # the settle day in US Eastern, the "on <date>" of the question name
    start: dt.datetime
    settle: dt.datetime


def repo_root() -> str:
    return skill_path.REPO


def default_out_dir() -> str:
    return os.path.join(repo_root(), "data", "updown")


def windows(first: dt.date, last: dt.date) -> list[Window]:
    """Question windows for settle days first..last inclusive. 23 or 25 hours across a US DST change."""
    out, day = [], first
    while day <= last:
        out.append(Window(day, updown.noon_et(day - dt.timedelta(days=1)), updown.noon_et(day)))
        day += dt.timedelta(days=1)
    return out


def make_row(symbol: str, w: Window, target: float, settle_price: float) -> dict:
    return {
        "symbol": symbol,
        "question_date": w.day.isoformat(),
        "start_utc": w.start.strftime(UTC_FMT), "settle_utc": w.settle.strftime(UTC_FMT),
        "start_tw": w.start.astimezone(TAIWAN).strftime(TW_FMT),
        "settle_tw": w.settle.astimezone(TAIWAN).strftime(TW_FMT),
        "target": f"{target:.2f}", "settle_price": f"{settle_price:.2f}",
        "result": "Up" if settle_price > target else "Down",
        "return_pct": f"{(settle_price / target - 1) * 100:.4f}",
    }


def read_rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def merge_rows(old: list[dict], new: list[dict]) -> list[dict]:
    """Union by question_date, the newer row wins, oldest first."""
    by_day = {r["question_date"]: r for r in old}
    by_day.update({r["question_date"]: r for r in new})
    return [by_day[d] for d in sorted(by_day)]


def write_rows(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def fetch_closes(pair: str, times: list[dt.datetime]) -> dict[dt.datetime, float]:
    """1-minute close at each time. A missing candle (exchange gap) is left out, not guessed."""
    def one(at):
        try:
            return at, updown.minute_close(pair, at)
        except fetch_ohlcv.FetchError:
            return at, None
    with ThreadPoolExecutor(WORKERS) as pool:
        return {at: c for at, c in pool.map(one, times) if c is not None}


def missing_windows(wins: list[Window], have: set[str]) -> list[Window]:
    return [w for w in wins if w.day.isoformat() not in have]


def build(symbol: str, days: int, out_dir: str, now: dt.datetime | None = None) -> tuple[int, int]:
    """Fetch and append the missing questions; returns (rows added, days skipped for missing candles)."""
    now = now or dt.datetime.now(UTC)
    # the last question whose settle minute has closed (allow 2 minutes after the settle time)
    last = updown.question_window(now - dt.timedelta(minutes=2))[1].date() - dt.timedelta(days=1)
    path = os.path.join(out_dir, f"{symbol.lower()}.csv")
    old = read_rows(path)
    todo = missing_windows(windows(last - dt.timedelta(days=days - 1), last), {r["question_date"] for r in old})
    if not todo:
        return 0, 0
    pair = fetch_ohlcv.to_pair(symbol)
    closes = fetch_closes(pair, sorted({t for w in todo for t in (w.start, w.settle)}))
    new = [make_row(symbol, w, closes[w.start], closes[w.settle])
           for w in todo if w.start in closes and w.settle in closes]
    write_rows(path, merge_rows(old, new))
    return len(new), len(todo) - len(new)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the daily Up/Down question dataset (CSV).")
    ap.add_argument("symbols", nargs="*", default=["BTC", "ETH"])
    ap.add_argument("--days", type=int, default=730, help="how many settled questions back to cover")
    ap.add_argument("--out-dir", default=None, help="default: <repo>/data/updown")
    args = ap.parse_args(argv)
    if args.days < 1:
        print("--days must be at least 1", file=sys.stderr)
        return 2
    out_dir = args.out_dir or default_out_dir()
    sys.stdout.reconfigure(encoding="utf-8")
    for raw in args.symbols:
        symbol = raw.upper().replace("USDT", "")
        try:
            added, skipped = build(symbol, args.days, out_dir)
        except fetch_ohlcv.FetchError as e:
            print(f"{symbol}: market data error: {e}", file=sys.stderr)
            return 3
        note = f"，{skipped} 天缺少 1 分鐘 K 線而略過" if skipped else ""
        print(f"{symbol}: 新增 {added} 題 → {os.path.join(out_dir, symbol.lower() + '.csv')}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
