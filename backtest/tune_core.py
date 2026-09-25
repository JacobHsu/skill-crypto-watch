"""Logic behind the local tuning page (backtest/tune_server.py): describe the nodes, check and apply a
tuning patch, and backtest it on the Binance daily Up/Down questions.

What can be tuned (everything else is fixed code):
  nodes   weight, enabled and the values in `params` (thresholds). Keys and value types are fixed.
  gates   the `floor` of each [[gate]] in profile.toml.
Fixed: a node's id, section, group, type and rule (the Python function), and the indicator formulas.

A patch is plain JSON:
  {"nodes": {"rsi": {"weight": 1.5, "enabled": true, "params": {"neutral_band": [40, 60]}}},
   "gates": {"choppy": {"floor": 0.4}}}
Nothing here writes to disk: the baseline is the shipped config (bundled symbol calibration
included, ~/.tv-ta ignored), and every run works on deep copies.
"""

import copy
import datetime as dt
import json
import math
import os
import re
import time

import skill_path  # noqa: F401  (puts skills/tv-ta/scripts on sys.path)
import backtest_updown
import replay
import run
import updown_dataset

UTC = dt.timezone.utc
MAX_WEIGHT = 10.0
MIN_DAYS, MAX_DAYS = 30, 730
Z95 = 1.96
SECTION_TITLES = {"A": "主圖指標頁（btc.html）", "B": "副圖指標頁（o/btc.html）",
                  "E": "事件節點（不在 check.html，預設停用）", "gate": "Gate（不投票，縮放整個群組的權重）"}


class PatchError(ValueError):
    """The tuning patch is not something we can apply."""


def is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def baseline(symbol: str):
    """Shipped defaults plus the bundled symbol calibration; the user's ~/.tv-ta layer is left out."""
    nodes, profile, _ = run.load_config(symbol, None, None)
    return nodes, profile


def comment_text(lines) -> str:
    """Comment lines as one sentence run, without the divider lines that only label a section."""
    keep = [ln.lstrip("# ").rstrip() for ln in lines if ln.startswith("#") and not re.match(r"#\s*[-=]{5,}", ln)]
    return " ".join(x for x in keep if x)


def node_docs(path: str | None = None) -> dict[str, str]:
    """The comments that describe a node in nodes.toml, by node id: those between its `id =` and
    `params =` lines, and those just above its [[node]] header."""
    path = path or os.path.join(run.ROOT, "config", "nodes.toml")
    with open(path, encoding="utf-8") as f:
        blocks = f.read().split("[[node]]")
    docs, above = {}, ""
    for block in blocks[1:] + [""]:
        m = re.search(r'^id = "([^"]+)"', block, re.M)
        lines = block.splitlines()
        if m:
            inside = block[m.end():]
            stop = re.search(r"^params\s*=", inside, re.M)
            inside_lines = (inside[:stop.start()] if stop else inside).splitlines()
            docs[m.group(1)] = " ".join(x for x in (above, comment_text(inside_lines)) if x)
            tail = block[m.end() + (stop.end() if stop else 0):]
        else:
            tail = ""
        # comments after the params line (until the next header) describe the next node
        tail_lines = tail.splitlines()[1:] if tail else []
        above = comment_text(tail_lines)
    return docs


FLOW_DOCS = ("04-nodes-section-a.md", "05-nodes-section-b.md")
ID_LINE = re.compile(r"^`([a-z][a-z_0-9]*)`\s*·")
MERMAID = re.compile(r"```mermaid\n(.*?)```", re.S)


def flow_sources(docs_dir: str | None = None) -> dict[str, dict]:
    """Decision-flow diagrams from the node docs, by node id: {"mermaid": source, "look": text}.
    A section's id line may name a second node that follows the same rule (e.g. `bb` and `bb_b`)."""
    docs_dir = docs_dir or os.path.join(updown_dataset.repo_root(), "docs", "tv-ta")
    out = {}
    for name in FLOW_DOCS:
        path = os.path.join(docs_dir, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            sections = re.split(r"^## ", f.read(), flags=re.M)[1:]
        waiting: list[str] = []  # ids of a section that shares the diagram of the section after it
        for sec in sections:
            lines = sec.splitlines()
            id_line = next((ln for ln in lines[1:6] if ID_LINE.match(ln)), None)
            if not id_line:
                waiting = []
                continue
            first = ID_LINE.match(id_line).group(1)
            ids = [first] + [i for i in re.findall(r"`([a-z][a-z_0-9]*)`", id_line) if i != first]
            diagram = MERMAID.search(sec)
            if not diagram:
                waiting = ids
                continue
            look = re.search(r"\*\*看什麼\*\*：(.+)", sec)
            entry = {"mermaid": diagram.group(1).strip(), "look": look.group(1).strip() if look else ""}
            for nid in ids + waiting:
                out.setdefault(nid, entry)
            waiting = []
    return out


def describe(symbol: str) -> dict:
    """Everything the page needs: nodes with their shipped values, gates and the dataset range."""
    nodes, profile = baseline(symbol)
    docs = node_docs()
    out_nodes = [{"id": n["id"], "section": n["section"], "name": n["name"], "zh": n.get("zh", ""),
                  "group": n["group"], "type": n["type"], "rule": n["rule"],
                  "weight": n.get("weight", 1.0), "enabled": n.get("enabled", True),
                  "params": n.get("params", {}), "doc": docs.get(n["id"], "")} for n in nodes]
    gates = [{"node": g["node"], "groups": g["groups"], "floor": g["floor"], "invert": g.get("invert", False)}
             for g in profile.get("gate", [])]
    rows = updown_dataset.read_rows(dataset_path(symbol))
    return {"symbol": symbol, "sections": SECTION_TITLES, "nodes": out_nodes, "gates": gates,
            "dataset": {"n": len(rows), "first": rows[0]["question_date"] if rows else None,
                        "last": rows[-1]["question_date"] if rows else None}}


def available_symbols() -> list[str]:
    folder = updown_dataset.default_out_dir()
    if not os.path.isdir(folder):
        return []
    return sorted(f[:-4].upper() for f in os.listdir(folder) if f.endswith(".csv"))


def dataset_path(symbol: str) -> str:
    return os.path.join(updown_dataset.default_out_dir(), f"{symbol.lower()}.csv")


def same_kind(default, value) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if is_number(default):
        return is_number(value)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, list):
        return (isinstance(value, list) and len(value) == len(default)
                and all(same_kind(d, v) for d, v in zip(default, value)))
    return False


def apply_patch(nodes: list[dict], profile: dict, patch: dict):
    """Validated copies of nodes and profile with the patch applied. Raises PatchError."""
    if not isinstance(patch, dict) or set(patch) - {"nodes", "gates"}:
        raise PatchError("patch must be an object with only 'nodes' and 'gates'")
    nodes, profile = copy.deepcopy(nodes), copy.deepcopy(profile)
    by_id = {n["id"]: n for n in nodes}
    for nid, fields in (patch.get("nodes") or {}).items():
        if nid not in by_id:
            raise PatchError(f"unknown node {nid!r}")
        node = by_id[nid]
        if not isinstance(fields, dict) or set(fields) - {"weight", "enabled", "params"}:
            raise PatchError(f"{nid}: only weight, enabled and params can be changed")
        if "weight" in fields:
            w = fields["weight"]
            if not is_number(w) or not 0 <= w <= MAX_WEIGHT:
                raise PatchError(f"{nid}: weight must be a number between 0 and {MAX_WEIGHT:g}")
            node["weight"] = w
        if "enabled" in fields:
            if not isinstance(fields["enabled"], bool):
                raise PatchError(f"{nid}: enabled must be true or false")
            node["enabled"] = fields["enabled"]
        for key, value in (fields.get("params") or {}).items():
            defaults = node.get("params", {})
            if key not in defaults:
                raise PatchError(f"{nid}: unknown parameter {key!r}")
            if not same_kind(defaults[key], value):
                raise PatchError(f"{nid}.{key}: expected the same kind of value as the default {defaults[key]!r}")
            node["params"][key] = value
    gates = {g["node"]: g for g in profile.get("gate", [])}
    for gid, fields in (patch.get("gates") or {}).items():
        if gid not in gates:
            raise PatchError(f"unknown gate {gid!r}")
        floor = (fields or {}).get("floor")
        if set(fields or {}) - {"floor"} or not is_number(floor) or not 0 <= floor <= 1:
            raise PatchError(f"{gid}: only floor can be changed, a number between 0 and 1")
        gates[gid]["floor"] = floor
    return nodes, profile


def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if is_number(v):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    raise PatchError(f"cannot write {v!r} as TOML")


def to_toml(symbol: str, patch: dict, profile: dict) -> str:
    """The patch as a file for ~/.tv-ta/config/symbols/<sym>.toml (deep-merged over the defaults)."""
    lines = [f"# tv-ta tuning patch for {symbol}, generated by the tuning page.",
             f"# Save as ~/.tv-ta/config/symbols/{symbol.lower()}.toml", ""]
    for nid, f in (patch.get("nodes") or {}).items():
        lines.append(f"[nodes.{nid}]")
        if "weight" in f:
            lines.append(f"weight = {toml_value(float(f['weight']))}")
        if "enabled" in f:
            lines.append(f"enabled = {toml_value(f['enabled'])}")
        if f.get("params"):
            body = ", ".join(f"{k} = {toml_value(v)}" for k, v in f["params"].items())
            lines.append(f"params = {{ {body} }}")
        lines.append("")
    if patch.get("gates"):
        # arrays replace as a whole, so the complete gate list is written with the new floors
        for g in profile.get("gate", []):
            lines.append("[[profile.gate]]")
            lines.append(f'node = {toml_value(g["node"])}')
            lines.append(f'groups = {toml_value(g["groups"])}')
            lines.append(f'floor = {toml_value(float(g["floor"]))}')
            if g.get("invert"):
                lines.append("invert = true")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def prune_patch(patch: dict, nodes: list[dict], profile: dict) -> dict:
    """The parts of a patch that differ from the baseline, so 'no change' is detectable."""
    by_id = {n["id"]: n for n in nodes}
    out_nodes = {}
    for nid, f in (patch.get("nodes") or {}).items():
        base = by_id.get(nid)
        if base is None:
            out_nodes[nid] = f  # left for apply_patch to reject
            continue
        kept = {}
        if "weight" in f and f["weight"] != base.get("weight", 1.0):
            kept["weight"] = f["weight"]
        if "enabled" in f and f["enabled"] != base.get("enabled", True):
            kept["enabled"] = f["enabled"]
        params = {k: v for k, v in (f.get("params") or {}).items() if base.get("params", {}).get(k) != v}
        if params:
            kept["params"] = params
        if kept:
            out_nodes[nid] = kept
    gates = {g["node"]: g for g in profile.get("gate", [])}
    out_gates = {gid: f for gid, f in (patch.get("gates") or {}).items()
                 if gid not in gates or f.get("floor") != gates[gid]["floor"]}
    return {k: v for k, v in (("nodes", out_nodes), ("gates", out_gates)) if v}


def decision_ms(row: dict) -> int:
    return int(dt.datetime.strptime(row["start_utc"], updown_dataset.UTC_FMT).replace(tzinfo=UTC).timestamp() * 1000)


def leans(data, rows: list[dict], nodes: list[dict], profile: dict, symbol: str) -> list[dict]:
    """One record per question: the lean at its start (sign of the average composite) and the result."""
    tfs, main, htf = data
    limit = profile["timeframes"].get("bars", 500)
    out = []
    for row in rows:
        comps = backtest_updown.composites(tfs, main, htf, nodes, profile, symbol, decision_ms(row), limit)
        if not comps:
            continue
        avg = sum(comps) / len(comps)
        out.append({"day": row["question_date"], "avg": avg, "up": row["result"] == "Up"})
    return out


def summarise(records: list[dict]) -> dict:
    def hit(rs):
        leaned = [r for r in rs if r["avg"] != 0]
        if not leaned:
            return {"n": 0, "hit": None, "ci": None}
        p = sum((r["avg"] > 0) == r["up"] for r in leaned) / len(leaned)
        return {"n": len(leaned), "hit": p, "ci": Z95 * math.sqrt(p * (1 - p) / len(leaned))}
    half = len(records) // 2
    n = len(records)
    return {"questions": n, "actual_up": sum(r["up"] for r in records) / n if n else None,
            "lean_up": sum(r["avg"] > 0 for r in records) / n if n else None,
            "all": hit(records), "first_half": hit(records[:half]), "second_half": hit(records[half:])}


def count_flips(before: list[dict], after: list[dict]) -> int:
    """Questions whose lean (sign of the composite) differs between two runs over the same questions."""
    return sum((a["avg"] > 0) != (b["avg"] > 0) or (a["avg"] == 0) != (b["avg"] == 0) for a, b in zip(before, after))


class Backtester:
    """Keeps downloaded candles and the baseline result in memory between runs."""

    def __init__(self):
        self._data, self._base = {}, {}

    def _market(self, symbol: str, days: int, profile: dict):
        key = (symbol, days)
        if key not in self._data:
            self._data[key] = replay.load_updown(symbol, days + 3, profile)
        return self._data[key]

    def run(self, symbol: str, days: int, patch: dict) -> dict:
        if not MIN_DAYS <= days <= MAX_DAYS:
            raise PatchError(f"days must be between {MIN_DAYS} and {MAX_DAYS}")
        rows = updown_dataset.read_rows(dataset_path(symbol))[-days:]
        if not rows:
            raise PatchError(f"no dataset for {symbol}; run backtest/updown_dataset.py {symbol}")
        started = time.time()
        nodes, profile = baseline(symbol)
        data = self._market(symbol, days, profile)
        key = (symbol, days, rows[-1]["question_date"])
        if key not in self._base:
            self._base[key] = leans(data, rows, nodes, profile, symbol)
        base_records = self._base[key]
        changes = prune_patch(patch, nodes, profile)
        tuned, flips = None, 0
        if changes:
            t_nodes, t_profile = apply_patch(nodes, profile, changes)
            records = leans(data, rows, t_nodes, t_profile, symbol)
            tuned, flips = summarise(records), count_flips(base_records, records)
        return {"symbol": symbol, "days": len(rows), "from": rows[0]["question_date"], "to": rows[-1]["question_date"],
                "baseline": summarise(base_records), "tuned": tuned, "flips": flips, "changes": changes,
                "seconds": round(time.time() - started, 1)}
