"""Decide whether a node earns its place: replay it on every symbol and timeframe, then apply fixed rules.

  python scripts/evaluate_node.py rsi_divergence
  python scripts/evaluate_node.py rsi_divergence supertrend_flip --json

Runs replay.py --compare-node on BTC / ETH x 1h / 4h / 1d (24h ahead) and replay.py --updown for
BTC / ETH, in parallel, and passes the node only if all of these hold. The rules are fixed before
looking at any result, so a node cannot pass by choosing the test that happens to look good.

  1. alone: hit rate >= 53% with both halves >= 50%, on at least 5 of the 6 symbol x timeframe runs
  2. no harm: with the node, the composite hit rate drops by no more than 1pp on any run
  3. up/down: with the node, the question lean hit rate is not lower on BTC or on ETH
"""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = [(s, tf, bars) for s in ("BTC", "ETH") for tf, bars in (("1h", 3000), ("4h", 2000), ("1d", 1000))]
UPDOWN_DAYS = 700
ALONE_MIN, HALF_MIN, ALONE_RUNS, MAX_DROP = 0.53, 0.50, 5, 0.01


def replay(*args):
    out = subprocess.run([sys.executable, os.path.join(HERE, "replay.py"), *args, "--json"],
                         capture_output=True, text=True, encoding="utf-8")
    if out.returncode:
        raise RuntimeError(f"replay.py {' '.join(args)} failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def evaluate(node, pool):
    bars = {r: pool.submit(replay, r[0], "--tf", r[1], "--bars", str(r[2]), "--compare-node", node) for r in RUNS}
    ud = {s: pool.submit(replay, s, "--updown", str(UPDOWN_DAYS), "--compare-node", node) for s in ("BTC", "ETH")}
    rows, alone_ok, harm = [], 0, []
    for (s, tf, _), f in bars.items():
        c = f.result()["compare"]
        a, w, o = c["node_alone"], c["without"]["all"], c["with"]["all"]
        ok = (a["all"]["hit"] or 0) >= ALONE_MIN and min(a["first_half"]["hit"] or 0, a["second_half"]["hit"] or 0) >= HALF_MIN
        alone_ok += ok
        drop = (w["hit"] or 0) - (o["hit"] or 0)
        if drop > MAX_DROP:
            harm.append(f"{s} {tf}")
        rows.append({"run": f"{s} {tf}", "alone": a, "without": w, "with": o, "alone_ok": ok})
    updown = {}
    for s, f in ud.items():
        r = f.result()
        updown[s] = {"without": r["without"]["all"]["hit"], "with": r["with"]["all"]["hit"], "per_tf": r["node_alone"]}
    ud_ok = all((v["with"] or 0) >= (v["without"] or 0) for v in updown.values())
    checks = {"alone": alone_ok >= ALONE_RUNS, "no_harm": not harm, "updown": ud_ok}
    return {"node": node, "pass": all(checks.values()), "checks": checks, "alone_runs_ok": alone_ok,
            "harm": harm, "runs": rows, "updown": updown}


def pct(x):
    return "—" if x is None else f"{x * 100:.0f}%"


def render(r):
    out = [f"## `{r['node']}` → {'✅ 通過' if r['pass'] else '❌ 不通過'}", "",
           "| 回放 | 單獨次數 | 單獨命中 | 前半 / 後半 | 沒導入命中 | 有導入命中 |", "|---|---|---|---|---|---|"]
    for x in r["runs"]:
        a = x["alone"]
        out.append(f"| {x['run']} | {a['all']['n']} | {pct(a['all']['hit'])}{' ✓' if x['alone_ok'] else ''} "
                   f"| {pct(a['first_half']['hit'])} / {pct(a['second_half']['hit'])} "
                   f"| {pct(x['without']['hit'])} | {pct(x['with']['hit'])} |")
    out += ["", "| 漲跌題 | 沒導入 | 有導入 | 節點單獨命中 1h / 4h / 1d |", "|---|---|---|---|"]
    for s, v in r["updown"].items():
        per = " / ".join(pct(v["per_tf"][tf]["hit"]) for tf in v["per_tf"])
        out.append(f"| {s} | {pct(v['without'])} | {pct(v['with'])} | {per} |")
    c = r["checks"]
    out += ["", f"- 單獨有效：{r['alone_runs_ok']} / 6 組達標（需 ≥ {ALONE_RUNS}）{'✅' if c['alone'] else '❌'}",
            f"- 不傷整體：{'✅' if c['no_harm'] else '❌ 下降超過 1pp：' + '、'.join(r['harm'])}",
            f"- 漲跌題不變差：{'✅' if c['updown'] else '❌'}", ""]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Replay a node everywhere and apply the fixed pass rules.")
    ap.add_argument("nodes", nargs="+")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    with ThreadPoolExecutor(max_workers=16) as pool, ThreadPoolExecutor(max_workers=len(args.nodes)) as outer:
        results = list(outer.map(lambda n: evaluate(n, pool), args.nodes))
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2) if args.json else "\n".join(render(r) for r in results))
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
