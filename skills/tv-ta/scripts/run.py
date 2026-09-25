"""tv-ta entry point.

  python scripts/run.py BTC                  # 4h checklist + 1h/4h/1d summary, markdown
  python scripts/run.py ETH --tf 1d --json   # machine-readable output
  python scripts/run.py SOL --tf 1h --log    # also append a JSONL record to ~/.tv-ta/logs/

Exit codes: 0 ok, 2 bad input or config, 3 market data unavailable.
"""

import argparse
import copy
import datetime as dt
import json
import os
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        sys.exit("tv-ta needs Python 3.11+ (or `pip install tomli` on 3.9/3.10)")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import decide  # noqa: E402
import fetch_ohlcv  # noqa: E402
import jev_client  # noqa: E402
import nodes as nodes_mod  # noqa: E402
from nodes import fmt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON = {"BUY": "▲ BUY", "WAIT": "─ WAIT", "SELL": "▼ SELL", "UNCHECKED": "· 未核對", "IDLE": "· 未觸發"}
FINAL_ICON = {"BUY": "▲ BUY", "WAIT": "◈ WAIT", "SELL": "▼ SELL", "UNCHECKED": "— NOT CHECKED"}


def load_toml(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def user_home():
    """Where a user's own tuning and logs live, outside the skill folder, so reinstalling or
    updating the plugin never overwrites them."""
    return os.environ.get("TV_TA_HOME") or os.path.join(os.path.expanduser("~"), ".tv-ta")


def user_config_dir():
    return os.environ.get("TV_TA_CONFIG") or os.path.join(user_home(), "config")


def display_path(path):
    """Short path for reports: relative to the skill, or ~-relative, never a full machine path."""
    path = os.path.normpath(path)
    for base, label in ((ROOT, ""), (os.path.expanduser("~"), "~/")):
        base = os.path.normpath(base)
        if os.path.normcase(path).startswith(os.path.normcase(base) + os.sep):
            return label + path[len(base) + 1:].replace(os.sep, "/")
    return os.path.basename(path)


def deep_merge(base, patch):
    """Tables merge key by key; arrays and scalars replace (so a [[gate]] list replaces the whole list)."""
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def apply_patch(nodes, profile, patch, source):
    """A patch file holds [nodes.<id>] tables (weight, enabled, params...) and optional [profile.*]."""
    by_id = {n["id"]: n for n in nodes}
    for nid, fields in patch.get("nodes", {}).items():
        if nid not in by_id:
            raise ValueError(f"{display_path(source)}: unknown node {nid!r}")
        deep_merge(by_id[nid], fields)
    deep_merge(profile, patch.get("profile", {}))


def load_config(symbol, config_dir=None, user_dir=None):
    """Layers, later wins:
      1. config_dir/nodes.toml + profile.toml       full definitions (the skill's bundled config)
      2. config_dir/symbols/<sym>.toml              bundled per-symbol calibration
      3. user_dir/profile.toml                      user's profile values, deep-merged
      4. user_dir/nodes.toml                        user's [nodes.<id>] patches, all symbols
      5. user_dir/symbols/<sym>.toml                user's per-symbol patches
    Returns (nodes, profile, list of applied layer names)."""
    config_dir = config_dir or os.path.join(ROOT, "config")
    nodes = load_toml(os.path.join(config_dir, "nodes.toml"))["node"]
    profile = load_toml(os.path.join(config_dir, "profile.toml"))
    applied = [display_path(config_dir)]
    sym = f"{symbol.lower()}.toml"

    path = os.path.join(config_dir, "symbols", sym)
    if os.path.exists(path):
        apply_patch(nodes, profile, load_toml(path), path)
        applied.append(display_path(path))

    if user_dir and os.path.isdir(user_dir):
        path = os.path.join(user_dir, "profile.toml")
        if os.path.exists(path):
            deep_merge(profile, load_toml(path))
            applied.append(display_path(path))
        for path in (os.path.join(user_dir, "nodes.toml"), os.path.join(user_dir, "symbols", sym)):
            if os.path.exists(path):
                apply_patch(nodes, profile, load_toml(path), path)
                applied.append(display_path(path))
    return nodes, profile, applied


def analyse(symbol, tf, nodes, profile, bars, htf_bars, use_jev):
    ctx = nodes_mod.Context(bars, htf_bars, profile.get("indicators", {}))
    results = nodes_mod.evaluate(ctx, nodes)
    warnings = jev_client.apply(nodes, results, profile.get("jev", {})) if use_jev else []
    summary = decide.compose(nodes, results, profile)
    plan = decide.trade_plan(ctx, summary["verdict"], profile.get("plan", {}))
    return ctx, results, summary, plan, warnings


def page_names(symbol, tf):
    s = symbol.lower()
    if s in ("btc", "eth"):
        return f"{s}.html", f"o/{s}.html?t={tf}"
    return f"altcoin.html?s={symbol.upper()}", f"o/altcoin.html?s={symbol.upper()}&t={tf}"


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def render(report):
    sym, tf = report["symbol"], report["tf"]
    s = report["summary"]
    page_a, page_b = page_names(sym, tf)
    out = [f"# {sym} · {tf.upper()} 技術檢核（tv-ta）", ""]
    if report.get("as_of"):
        out.append(f"**回放模式**：只使用 {report['as_of']} 以前已收盤的 K 棒")
    out.append(f"資料：BINANCE:{report['pair']} {tf}，最新 K 棒開盤 {report['bar_time']}"
               f"（{'含未收盤 K 棒' if report['live'] else '只用已收盤 K 棒'}），收盤 {fmt(report['price'])}")
    out.append("設定：" + " → ".join(f"`{c}`" for c in report["config_layers"]))
    out.append("")
    titles = {"A": f"SECTION A — PAGE I（{page_a} · {tf.upper()}）",
              "B": f"SECTION B — PAGE II ─ Pane（{page_b}）",
              "E": "SECTION E — 事件訊號（不在 check.html；未觸發時不投票）"}
    for sec in decide.SECTIONS:
        block = s["sections"][sec]
        if not block["rows"]:
            continue
        out += [f"## {titles[sec]}", "", "| # | 項目 | 判定 | 分數 | 權重 | 證據 |", "|---|---|---|---|---|---|"]
        for i, r in enumerate(block["rows"], 1):
            score = "—" if r["score"] is None else f"{r['score']:+g}"
            w = f"{r['effective_weight']:g}" if r["effective_weight"] == r["weight"] else f"{r['weight']:g}→{r['effective_weight']:g}"
            src = "（Jev）" if r["source"] == "jev" else ""
            out.append(f"| {i} | {r['name']} | {ICON[r['signal']]} | {score} | {w} | {r['evidence']}{src} |")
        t = block["tally"]
        extra = (f" · 未核對 {t['UNCHECKED']}" if t["UNCHECKED"] else "") + (f" · 未觸發 {t['IDLE']}" if t["IDLE"] else "")
        out += ["", f"**小計**　▲ BUY {t['BUY']} · ─ WAIT {t['WAIT']} · ▼ SELL {t['SELL']}{extra}（共 {len(block['rows'])} 項）", ""]

    if s["gates"]:
        out += ["## GATES（Noul）", "", "| Gate | Noul | 權重係數 | 影響群組 | 證據 |", "|---|---|---|---|---|"]
        for g in s["gates"]:
            out.append(f"| {g['node']} | {g['noul']:.2f} | ×{g['multiplier']:.2f} | {', '.join(g['groups'])} | {g['evidence']} |")
        out.append("")

    c = s["checklist"]
    t = c["tally"]
    comp = "n/a" if s["composite"] is None else f"{s['composite']:+.2f} / ±2"
    agree = "" if s["agreement"] is None else f"（加權一致度 {s['agreement'] * 100:.0f}%）"
    unchecked = c["total"] - c["checked"]
    out += ["## FINAL", "", "| 項目 | 值 |", "|---|---|",
            f"| 幣種 / 級別 | {sym} · {tf.upper()} |",
            f"| 已核對 | {c['checked']} / {c['total']}" + (f"（{unchecked} 項資料不足）" if unchecked else "") + " |",
            f"| BUY / WAIT / SELL | {t['BUY']} / {t['WAIT']} / {t['SELL']} |",
            f"| 檢核表判斷（check.html 規則） | {FINAL_ICON[c['verdict']]}（BUY {c['buy_pct']}% · SELL {c['sell_pct']}%） |",
            f"| 加權綜合分數 | {comp}{agree} → {FINAL_ICON[s['composite_verdict']]} |",
            f"| **最終判斷** | **{FINAL_ICON[s['verdict']]}**（依 {report['primary']}） |", ""]
    if s["routed"]:
        w, why = s["weight_split"], {"neutral": "多數項目中性，訊號不足", "split": "多空分歧"}[s["routed"]["reason"]]
        out += [f"> 信心度路由：加權一致度 {w['agree'] * 100:.0f}% 低於 {s['routed']['min_agreement'] * 100:.0f}%"
                f"（同向 {w['agree'] * 100:.0f}% · 中性 {w['neutral'] * 100:.0f}% · 反向 {w['oppose'] * 100:.0f}%），"
                f"{why}，{s['routed']['from']} 降為 WAIT，只給觸發價。", ""]
    if s["methods_disagree"]:
        out += ["> 檢核表計數與加權分數結論不同：權重或 gate 改變了結果，請看上方權重欄。", ""]

    if report["context"]:
        out += ["## 多級別對照", "", "| 級別 | 加權分數 | 加權判斷 | 檢核表 |", "|---|---|---|---|"]
        for k, v in report["context"].items():
            mark = " ←" if k == tf else ""
            comp = "n/a" if v["composite"] is None else f"{v['composite']:+.2f}"
            out.append(f"| {k.upper()}{mark} | {comp} | {FINAL_ICON[v['composite_verdict']]} | {FINAL_ICON[v['checklist']]} |")
        out.append("")

    out += ["## 操作建議", ""] + [f"- {line}" for line in decide.describe_plan(report["plan"])] + [""]
    for w in report["warnings"]:
        out.append(f"> ⚠️ {w}")
    out += ["> 此為依規則計算的技術面參考，非投資建議。進出場前請自行評估風險與部位大小。"]
    return "\n".join(out)


def to_jsonable(report):
    r = copy.deepcopy(report)
    r.pop("_ctx", None)
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(description="tv-ta: numeric TradingView checklist")
    ap.add_argument("symbol", nargs="?", default="BTC")
    ap.add_argument("--tf", help="15m | 1h | 4h | 1d (default from profile.toml)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    ap.add_argument("--no-context", action="store_true", help="skip the other timeframes")
    ap.add_argument("--live", action="store_true", help="include the still-forming candle")
    ap.add_argument("--cache", action="store_true", help="reuse downloaded candles (for config tuning)")
    ap.add_argument("--log", action="store_true",
                    help="append a JSONL record to $TV_TA_LOG_DIR (default ~/.tv-ta/logs)")
    ap.add_argument("--no-jev", action="store_true", help="never call Jev, even if enabled")
    ap.add_argument("--as-of", help="replay the past: analyse with only the candles closed before this "
                                    "UTC time, e.g. 2026-09-22T16:00")
    ap.add_argument("--config", help="base config directory (default: the skill's config/)")
    ap.add_argument("--no-user-config", action="store_true",
                    help="ignore ~/.tv-ta/config (or $TV_TA_CONFIG) patches")
    args = ap.parse_args(argv)

    symbol = args.symbol.upper().replace("USDT", "") or "BTC"
    try:
        nodes, profile, layers = load_config(symbol, args.config,
                                             None if args.no_user_config else user_config_dir())
    except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    tfp = profile.get("timeframes", {})
    tf = args.tf or tfp.get("default", "4h")
    htf_map = tfp.get("htf", {})
    if tf not in htf_map:
        print(f"unsupported timeframe {tf!r}; use one of {', '.join(htf_map)}", file=sys.stderr)
        return 2
    as_of = None
    if args.as_of:
        try:
            t = dt.datetime.fromisoformat(args.as_of.rstrip("Z"))
        except ValueError:
            print(f"bad --as-of {args.as_of!r}; use e.g. 2026-09-22T16:00 (UTC)", file=sys.stderr)
            return 2
        t = t.replace(tzinfo=dt.timezone.utc) if t.tzinfo is None else t.astimezone(dt.timezone.utc)
        as_of = int(t.timestamp() * 1000)
    live = (args.live or tfp.get("live", False)) and not as_of
    limit = tfp.get("bars", 500)
    tfs = [tf] if args.no_context else list(dict.fromkeys([tf] + [t for t in tfp.get("context", []) if t in htf_map]))

    # the Multi-Time Period blocks include the current higher-timeframe candle
    wanted = {t: ((t, limit, live), (htf_map[t], limit, True)) for t in tfs}

    try:
        bars = fetch_ohlcv.fetch_many(symbol, [r for pair in wanted.values() for r in pair], args.cache, as_of)
        runs = {}
        for t, (main, higher) in wanted.items():
            runs[t] = analyse(symbol, t, nodes, profile, bars[main], bars[higher],
                              use_jev=(t == tf and not args.no_jev))
    except fetch_ohlcv.FetchError as e:
        print(f"market data error: {e}", file=sys.stderr)
        return 3

    ctx, results, summary, plan, warnings = runs[tf]
    report = {
        "symbol": symbol, "pair": ctx.bars["pair"], "tf": tf, "live": live,
        "as_of": iso(as_of) if as_of else None,
        "bar_time": iso(ctx.bars["time"][-1]), "price": ctx.price,
        "primary": profile["decision"].get("primary", "composite"),
        "config_layers": layers, "summary": summary, "plan": plan, "warnings": warnings,
        "context": {} if args.no_context else {
            t: {"composite": r[2]["composite"], "composite_verdict": r[2]["composite_verdict"],
                "checklist": r[2]["checklist"]["verdict"]} for t, r in runs.items()},
    }

    if args.log:
        log_dir = os.environ.get("TV_TA_LOG_DIR") or os.path.join(user_home(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        rec = {
            "logged_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "symbol": symbol, "tf": tf, "bar_time": report["bar_time"], "price": ctx.price,
            "composite": summary["composite"], "agreement": summary["agreement"],
            "verdict": summary["verdict"], "raw_composite_verdict": summary["raw_composite_verdict"],
            "checklist": summary["checklist"]["verdict"],
            "scores": {nid: r.score for nid, r in results.items() if r.score is not None},
            "gates": {g["node"]: g["noul"] for g in summary["gates"]},
        }
        with open(os.path.join(log_dir, f"{dt.date.today():%Y-%m}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    text = json.dumps(to_jsonable(report), ensure_ascii=False, indent=2) if args.json else render(report)
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
