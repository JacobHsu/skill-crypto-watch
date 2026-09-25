"""Optional: answer selected nodes with TypeSafe Jev instead of the code rule.

A node opts in with a `jev = { instructions = ..., criteria = ... }` table in
config/nodes.toml. All such nodes go out in one request (Jev evaluates questions
in parallel). The state is every node's evidence and numbers, keyed by node id,
so instructions can point at them with backticks, e.g. `zigzag.pivots`.

Mapping answers back to the node's scale:
  score  -> criteria are ordered strong sell .. strong buy; position maps to -2..+2
  choice -> `choice_scores = { option = score }` maps the chosen option
  noul   -> the probability is used as the gate's noul
Any failure keeps the code rule result and records a warning.
"""

import json
import os
import urllib.error
import urllib.request


def question_type(jev):
    """Explicit `type`, else inferred from criteria: ordered list = score, map = choice, none = noul.
    Independent of the node's own type: a choice node can be asked as a Jev score."""
    if "type" in jev:
        return jev["type"]
    crit = jev.get("criteria")
    return "score" if isinstance(crit, list) else "choice" if isinstance(crit, dict) else "noul"


def apply(nodes, results, cfg):
    """Replace rule answers with Jev answers where configured. Returns a list of warnings."""
    if not cfg.get("enabled"):
        return []
    targets = [n for n in nodes if n.get("jev") and n.get("enabled", True) and n["id"] in results]
    if not targets:
        return []
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        return ["jev.enabled = true 但沒有設定 TYPESAFE_API_KEY，Jev 節點改用 code 規則"]

    state = {nid: {"evidence": r.evidence, **r.data} for nid, r in results.items() if r.evidence}
    questions = {}
    for n in targets:
        j = n["jev"]
        qtype = question_type(j)
        q = {"type": qtype, "instructions": j["instructions"]}
        if "criteria" in j:
            q["criteria"] = j["criteria"]
        questions[n["id"]] = q

    body = json.dumps({"state": state, "model": cfg.get("model", "jev-latest"), "questions": questions}).encode()
    req = urllib.request.Request(cfg.get("endpoint", "https://api.typesafe.ai/v1/systemone"), data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.get("timeout", 20)) as resp:
            answers = json.loads(resp.read().decode("utf-8")).get("answers", {})
    except urllib.error.HTTPError as e:
        return [f"Jev 請求失敗（HTTP {e.code}），Jev 節點改用 code 規則"]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return [f"Jev 請求失敗（{e}），Jev 節點改用 code 規則"]

    warnings = []
    for n in targets:
        ans, r, j = answers.get(n["id"]), results[n["id"]], n["jev"]
        if not ans:
            warnings.append(f"Jev 沒有回答 {n['id']}，保留 code 規則")
            continue
        qtype = question_type(j)
        if qtype == "score" and ans.get("score") is not None:
            levels = len(j["criteria"])
            new = -2 + 4 * ans["score"] / (levels - 1)
            detail = f"level {ans['score']:.2f}/{levels - 1}, confidence {ans.get('confidence', 0):.2f}"
        elif qtype == "choice" and ans.get("choice") is not None:
            new = j.get("choice_scores", {}).get(ans["choice"])
            if new is None:
                warnings.append(f"{n['id']}: choice_scores 沒有 {ans['choice']!r}，保留 code 規則")
                continue
            r.choice = ans["choice"]
            detail = f"choice {ans['choice']}, confidence {ans.get('confidence', 0):.2f}"
        elif qtype == "noul" and ans.get("noul") is not None:
            r.noul = ans["noul"]
            r.evidence += f"；Jev noul {ans['noul']:.2f}"
            r.source = "jev"
            continue
        else:
            warnings.append(f"Jev 對 {n['id']} 的回答格式不符，保留 code 規則")
            continue
        rule_score = r.score
        r.score = max(-2.0, min(2.0, new))
        r.evidence += f"；Jev {r.score:+.2f}（{detail}；規則原判 {rule_score if rule_score is not None else 'n/a'}）"
        r.source = "jev"
    return warnings
