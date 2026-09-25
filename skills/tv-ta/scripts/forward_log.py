"""Forward test of the daily "Up or Down" answers: log each day's predictions, settle them, report.

  python scripts/forward_log.py run          # the daily job: settle finished questions, log today's, print a summary
  python scripts/forward_log.py lean BTC     # today's two answers for one symbol, nothing is written
  python scripts/forward_log.py report       # the running scoreboard (--json for machines)

Three answers are recorded for every question and scored against the settled result:
  orig     the shipped strategy: sign of the average composite over the [updown] timeframes
  learned  the frozen model in config/forward_model_v1.json (weights may be negative)
  up       always answering Up, the reference any real skill has to beat

A prediction is made from candles closed before the question starts, so running late does not leak
the future; a row logged more than LATE_HOURS after the start is marked late and left out of the
scoreboard. Rows are never rewritten after they settle. Records live in ~/.tv-ta/forward/forward.csv
(override with $TV_TA_FORWARD_DIR).

Exit codes: 0 ok, 2 bad input or model file, 3 market data unavailable for some symbol.
"""

import argparse
import csv
import datetime as dt
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_ohlcv  # noqa: E402
import forward_model  # noqa: E402
import run  # noqa: E402
import updown  # noqa: E402

UTC = dt.timezone.utc
TIME_FMT = "%Y-%m-%dT%H:%MZ"
LATE_HOURS = 3
SETTLE_GRACE = dt.timedelta(minutes=10)
MIN_FOR_VERDICT = 200
FIELDS = ("question_date", "symbol", "start_utc", "settle_utc", "logged_at", "late", "model", "orig_avg",
          "orig_pred", "learned_score", "learned_pred", "target", "settle_price", "result", "contributions",
          "tf_composites")


def data_path() -> str:
    folder = os.environ.get("TV_TA_FORWARD_DIR") or os.path.join(run.user_home(), "forward")
    return os.path.join(folder, "forward.csv")


def read_rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_rows(path: str, rows: list[dict]) -> None:
    """Whole-file rewrite through a temp file, so an interrupted run never leaves half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in FIELDS} for r in rows)
    os.replace(tmp, path)


def parse_time(text: str) -> dt.datetime:
    return dt.datetime.strptime(text, TIME_FMT).replace(tzinfo=UTC)


def fnum(x, digits=4) -> str:
    return "" if x is None else str(round(x, digits))


# ---- predicting ------------------------------------------------------------------------------------

def make_row(symbol: str, start: dt.datetime, settle: dt.datetime, now: dt.datetime, model: dict, ev: dict) -> dict:
    late = (now - start) > dt.timedelta(hours=LATE_HOURS)
    return {**dict.fromkeys(FIELDS, ""), "question_date": f"{(settle - dt.timedelta(hours=12)).date()}", "symbol": symbol,
            "start_utc": start.strftime(TIME_FMT), "settle_utc": settle.strftime(TIME_FMT),
            "logged_at": now.strftime(TIME_FMT), "late": "1" if late else "0", "model": model["id"],
            "orig_avg": fnum(ev["orig_avg"]), "orig_pred": ev["orig_pred"],
            "learned_score": fnum(ev["learned_score"]), "learned_pred": ev["learned_pred"],
            "contributions": json.dumps({k: round(v, 3) for k, v in ev["contributions"].items()}, separators=(",", ":")),
            "tf_composites": json.dumps(ev["tf_composites"], separators=(",", ":"))}


def predict(symbols: list[str], now: dt.datetime, model: dict, rows: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """Add today's row for each symbol that has none. Returns (rows, symbols added, failure messages)."""
    start, settle = updown.question_window(now)
    key = f"{(settle - dt.timedelta(hours=12)).date()}"
    have = {(r["symbol"], r["question_date"]) for r in rows}
    added, failed = [], []
    for sym in symbols:
        if (sym, key) in have:
            continue
        try:
            ev = forward_model.evaluate_question(sym, int(start.timestamp() * 1000), model)
        except (fetch_ohlcv.FetchError, forward_model.ModelError) as e:
            failed.append(f"{sym}: {e}")
            continue
        rows = rows + [make_row(sym, start, settle, now, model, ev)]
        added.append(sym)
    return rows, added, failed


# ---- settling --------------------------------------------------------------------------------------

def settle(rows: list[dict], now: dt.datetime) -> tuple[list[dict], int, list[str]]:
    """Fill in the result of every finished question. Up means the settle close is above the target."""
    out, done, failed = [], 0, []
    for r in rows:
        if r["result"] or now < parse_time(r["settle_utc"]) + SETTLE_GRACE:
            out.append(r)
            continue
        pair = fetch_ohlcv.to_pair(r["symbol"])
        try:
            target = updown.minute_close(pair, parse_time(r["start_utc"]))
            final = updown.minute_close(pair, parse_time(r["settle_utc"]))
        except fetch_ohlcv.FetchError as e:
            failed.append(f"{r['symbol']} {r['question_date']}: {e}")
            out.append(r)
            continue
        out.append({**r, "target": str(target), "settle_price": str(final), "result": "Up" if final > target else "Down"})
        done += 1
    return out, done, failed


# ---- scoreboard ------------------------------------------------------------------------------------

def hit_stats(pairs: list[tuple[bool, bool]]) -> dict:
    n = len(pairs)
    if not n:
        return {"n": 0, "hit": None, "ci": None}
    p = sum(a == b for a, b in pairs) / n
    return {"n": n, "hit": p, "ci": 1.96 * math.sqrt(p * (1 - p) / n)}


def diff_stats(a: list[bool], b: list[bool]) -> dict:
    """Paired difference in hit rate: per question, correct_a minus correct_b."""
    n = len(a)
    if n < 2:
        return {"diff": None, "ci": None}
    d = [int(x) - int(y) for x, y in zip(a, b)]
    mean = sum(d) / n
    var = sum((x - mean) ** 2 for x in d) / (n - 1)
    return {"diff": mean, "ci": 1.96 * math.sqrt(var / n)}


def score_group(rows: list[dict]) -> dict:
    """Hit rates on the settled, on-time rows where both strategies gave an answer."""
    use = [r for r in rows if r["result"] and r["late"] != "1" and r["orig_pred"] and r["learned_pred"]]
    up = [r["result"] == "Up" for r in use]
    ok = {name: [r[f"{name}_pred"] == r["result"] for r in use] for name in ("orig", "learned")}
    ok["up"] = up
    return {"n": len(use), "up_rate": sum(up) / len(up) if up else None,
            "hit": {k: hit_stats([(v, True) for v in ok[k]]) for k in ok},
            "learned_vs_orig": diff_stats(ok["learned"], ok["orig"]),
            "learned_vs_up": diff_stats(ok["learned"], ok["up"])}


def scoreboard(rows: list[dict], model: dict) -> dict:
    trained = set(model["symbols"]["trained"])
    groups = {"BTC+ETH（模型訓練用的幣種）": [r for r in rows if r["symbol"] in trained],
              "其他幣種（沒參與訓練）": [r for r in rows if r["symbol"] not in trained],
              "全部": rows}
    per_symbol = {}
    for sym in sorted({r["symbol"] for r in rows}):
        g = score_group([r for r in rows if r["symbol"] == sym])
        per_symbol[sym] = {k: g["hit"][k]["hit"] for k in g["hit"]} | {"n": g["n"]}
    days = sorted({r["question_date"] for r in rows})
    return {"model": model["id"], "first_day": days[0] if days else None, "last_day": days[-1] if days else None,
            "logged": len(rows), "late": sum(r["late"] == "1" for r in rows),
            "pending": sum(not r["result"] for r in rows),
            "groups": {name: score_group(g) for name, g in groups.items()}, "per_symbol": per_symbol,
            "review_rule": model.get("review_rule", "")}


def pct(x, signed=False) -> str:
    return "—" if x is None else f"{x * 100:+.1f}" if signed else f"{x * 100:.1f}%"


def render(board: dict) -> str:
    if not board["logged"]:
        return "還沒有任何紀錄。"
    out = [f"# tv-ta 前瞻紀錄（模型 {board['model']}）", "",
           f"期間 {board['first_day']} ～ {board['last_day']}；已記 {board['logged']} 筆，未結算 {board['pending']} 筆，補記（不計分）{board['late']} 筆。", "",
           "| 群組 | 題數 | 原本策略 | 學習版 | 永遠猜 Up | 學習版 − 原本 | 學習版 − 永遠猜 Up |", "|---|---|---|---|---|---|---|"]
    for name, g in board["groups"].items():
        h = g["hit"]
        cell = lambda k: "—" if h[k]["hit"] is None else f"{pct(h[k]['hit'])} ±{h[k]['ci'] * 100:.1f}"  # noqa: E731
        d = lambda x: "—" if x["diff"] is None else f"{pct(x['diff'], True)}pp ±{x['ci'] * 100:.1f}"  # noqa: E731
        out.append(f"| {name} | {g['n']} | {cell('orig')} | {cell('learned')} | {cell('up')} | {d(g['learned_vs_orig'])} | {d(g['learned_vs_up'])} |")
    out += ["", "各幣種（原本 / 學習版 / 永遠猜 Up）：" + "；".join(
        f"{s} {pct(v['orig'])}/{pct(v['learned'])}/{pct(v['up'])}（{v['n']} 題）" for s, v in board["per_symbol"].items())]
    n = board["groups"]["全部"]["n"]
    out += ["", (f"樣本 {n} 題，還不到 {MIN_FOR_VERDICT} 題，這些數字只是進度，不能當結論；誤差範圍（±）比任何差距都大。"
                 if n < MIN_FOR_VERDICT else f"樣本 {n} 題。幣種彼此高度連動，實際獨立的樣本比題數少。"),
            f"審查規則：{board['review_rule']}", "非投資建議。"]
    return "\n".join(out)


# ---- commands --------------------------------------------------------------------------------------

def parse_now(text: str | None) -> dt.datetime:
    if not text:
        return dt.datetime.now(UTC)
    t = dt.datetime.fromisoformat(text.rstrip("Z"))
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def cmd_run(args, model) -> int:
    now, path = parse_now(args.now), data_path()
    rows, settled, settle_failed = settle(read_rows(path), now)
    rows, added, predict_failed = predict(args.symbols or all_symbols(model), now, model, rows)
    write_rows(path, rows)
    failures = settle_failed + predict_failed
    sys.stdout.reconfigure(encoding="utf-8")
    if args.json:
        print(json.dumps({"settled": settled, "logged": added, "failures": failures, "path": path,
                          "scoreboard": scoreboard(rows, model)}, ensure_ascii=False, indent=2))
    else:
        print(f"今天新記 {len(added)} 筆（{', '.join(added) or '無'}）；結算 {settled} 筆；資料檔 {path}")
        for msg in failures:
            print(f"失敗：{msg}")
        print()
        print(render(scoreboard(rows, model)))
    return 3 if failures else 0


def cmd_report(args, model) -> int:
    board = scoreboard(read_rows(data_path()), model)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(board, ensure_ascii=False, indent=2) if args.json else render(board))
    return 0


def cmd_lean(args, model) -> int:
    now = parse_now(args.now)
    start, _ = updown.question_window(now)
    sym = args.symbols[0]
    try:
        ev = forward_model.evaluate_question(sym, int(start.timestamp() * 1000), model)
    except (fetch_ohlcv.FetchError, forward_model.ModelError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 3 if isinstance(e, fetch_ohlcv.FetchError) else 2
    top = sorted(ev["contributions"].items(), key=lambda kv: -abs(kv[1]))[:5]
    sys.stdout.reconfigure(encoding="utf-8")
    if args.json:
        print(json.dumps({"symbol": sym, "start_utc": start.strftime(TIME_FMT), **ev}, ensure_ascii=False, indent=2))
        return 0
    print(f"{sym} 題目開始時間 {start:%Y-%m-%d %H:%M} UTC 之前收盤的資料\n"
          f"- 原本策略：加權分數平均 {ev['orig_avg']:+.2f} → 偏 {ev['orig_pred'] or '無偏向'}\n"
          f"- 學習版（實驗性、尚未驗證）：分數 {ev['learned_score']:+.2f} → 偏 {ev['learned_pred'] or '無偏向'}\n"
          f"  影響最大的節點：" + "、".join(f"{k} {v:+.2f}" for k, v in top))
    return 0


def all_symbols(model: dict) -> list[str]:
    return model["symbols"]["trained"] + model["symbols"]["untrained"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="tv-ta forward test of the daily Up or Down answers")
    ap.add_argument("command", choices=("run", "report", "lean"))
    ap.add_argument("symbols", nargs="*", help="run: symbols to log (default: all the model lists); lean: one symbol")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--now", help="pretend it is this UTC time (testing only), e.g. 2026-09-25T16:05")
    args = ap.parse_args(argv)
    args.symbols = [s.upper().replace("USDT", "") for s in args.symbols]
    if args.command == "lean" and len(args.symbols) != 1:
        print("lean needs exactly one symbol, e.g. lean BTC", file=sys.stderr)
        return 2
    try:
        model = forward_model.load_model()
    except forward_model.ModelError as e:
        print(f"model error: {e}", file=sys.stderr)
        return 2
    return {"run": cmd_run, "report": cmd_report, "lean": cmd_lean}[args.command](args, model)


if __name__ == "__main__":
    sys.exit(main())
