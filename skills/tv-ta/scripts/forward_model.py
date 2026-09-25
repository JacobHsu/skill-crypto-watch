"""The frozen "learned" Up/Down model and the shipped strategy's lean, computed the same way the
backtest did, from candles closed before a question starts.

The shipped strategy answers with the sign of the average composite over the [updown] timeframes.
The learned model (config/forward_model_v1.json) answers with the sign of a weighted sum of the
gated node scores whose weights may be negative. Both are read from one evaluation with every node
enabled, so a node's score never depends on whether it votes.

The model file carries a digest of its coefficients; a file that was edited or retrained by hand
is refused, so every logged row can be traced to one fixed model.
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import decide  # noqa: E402
import fetch_ohlcv  # noqa: E402
import nodes as nodes_mod  # noqa: E402
import run  # noqa: E402

MODEL_PATH = os.path.join(run.ROOT, "config", "forward_model_v1.json")
UPDOWN_TFS = ("1h", "4h", "1d")


class ModelError(RuntimeError):
    """The model file is missing, edited or does not match the node config."""


def digest(features: list[str], coef: list[float]) -> str:
    body = json.dumps({"features": features, "coef": coef}, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def load_model(path: str | None = None) -> dict:
    path = path or MODEL_PATH
    try:
        with open(path, encoding="utf-8") as f:
            model = json.load(f)
    except (OSError, ValueError) as e:
        raise ModelError(f"cannot read model file {path}: {e}") from e
    features, coef = model.get("features_order"), model.get("coef")
    if not features or not coef or len(features) != len(coef):
        raise ModelError("model file has no coefficients")
    if digest(features, coef) != model.get("sha256"):
        raise ModelError("model file was changed: its coefficients no longer match the recorded digest")
    return model


def sign_call(x: float | None) -> str:
    """Up / Down, or empty when there is no lean (a tie is not an answer)."""
    return "" if not x else "Up" if x > 0 else "Down"


def gated_scores(results: dict, gates: list[dict], nodes: list[dict]) -> dict[str, float]:
    """Node id -> score times the multiplier the gates apply to that node's group."""
    mult, _ = decide.gate_multipliers(results, gates)
    return {n["id"]: results[n["id"]].score * mult.get(n.get("group"), 1.0)
            for n in nodes if n["id"] in results and results[n["id"]].score is not None}


def mean_features(per_tf: list[dict[str, float]], ids: list[str]) -> dict[str, float]:
    """x[i]: the mean over timeframes where node i has a score; 0 when it has none."""
    out = {}
    for nid in ids:
        vals = [s[nid] for s in per_tf if nid in s]
        out[nid] = sum(vals) / len(vals) if vals else 0.0
    return out


def learned_score(feats: dict[str, float], model: dict) -> tuple[float, dict[str, float]]:
    """(score, per-node contribution) for a feature vector."""
    parts = {nid: c * feats[nid] for nid, c in zip(model["features_order"], model["coef"])}
    return sum(parts.values()), parts


def evaluate_question(symbol: str, at_ms: int, model: dict) -> dict:
    """Both answers for one question, using only candles closed before `at_ms` (its start).
    Raises fetch_ohlcv.FetchError when market data is unavailable and ModelError on a config mismatch."""
    nodes, profile, _ = run.load_config(symbol, None, None)       # shipped config only, no user patches
    ids = model["features_order"]
    known = {n["id"] for n in nodes}
    missing = [i for i in ids if i not in known]
    if missing:
        raise ModelError(f"model uses nodes that no longer exist: {', '.join(missing)}")
    tfp = profile["timeframes"]
    limit, htf_map = tfp.get("bars", 500), tfp["htf"]
    tfs = profile.get("updown", {}).get("timeframes", list(UPDOWN_TFS))
    wanted = {tf: ((tf, limit, False), (htf_map[tf], 500, False)) for tf in tfs}
    bars = fetch_ohlcv.fetch_many(symbol, [r for pair in wanted.values() for r in pair], as_of=at_ms)
    everything = [dict(n, enabled=True) for n in nodes]
    comps, per_tf = {}, []
    for tf, (main, higher) in wanted.items():
        ctx = nodes_mod.Context(bars[main], bars[higher], profile.get("indicators", {}))
        results = nodes_mod.evaluate(ctx, everything)
        composite = decide.compose(nodes, results, profile)["composite"]
        if composite is not None:
            comps[tf] = composite
        per_tf.append(gated_scores(results, profile.get("gate", []), nodes))
    orig_avg = sum(comps.values()) / len(comps) if comps else None
    score, parts = learned_score(mean_features(per_tf, ids), model)
    return {"orig_avg": orig_avg, "orig_pred": sign_call(orig_avg), "tf_composites": comps,
            "learned_score": score, "learned_pred": sign_call(score), "contributions": parts}
