"""Combine node answers into a verdict and a trade plan.

Composite scoring (Jev style): every vote node keeps its raw score; weights and
gates are applied here, so tuning config never changes what a node observed.
"""

from nodes import fmt

BUY, WAIT, SELL, UNCHECKED, IDLE = "BUY", "WAIT", "SELL", "UNCHECKED", "IDLE"
SECTIONS = ("A", "B", "E")  # E = event nodes: they vote only when their event fires


def signal_of(score, threshold):
    if score is None:
        return UNCHECKED
    if score >= threshold:
        return BUY
    if score <= -threshold:
        return SELL
    return WAIT


def gate_multipliers(results, gates):
    """{group: multiplier} from the gate nodes' noul values."""
    mult, applied = {}, []
    for g in gates:
        r = results.get(g["node"])
        if r is None or r.noul is None:
            continue
        p = 1 - r.noul if g.get("invert") else r.noul
        m = 1 - p * (1 - g.get("floor", 1.0))
        for grp in g.get("groups", []):
            mult[grp] = mult.get(grp, 1.0) * m
        applied.append({"node": g["node"], "noul": round(r.noul, 3), "multiplier": round(m, 3),
                        "groups": g.get("groups", []), "evidence": r.evidence})
    return mult, applied


def compose(nodes, results, profile):
    d = profile["decision"]
    thr = d.get("signal_threshold", 0.5)
    mult, gates = gate_multipliers(results, profile.get("gate", []))

    rows = []
    for node in nodes:
        if node.get("section") not in SECTIONS or not node.get("enabled", True):
            continue
        r = results[node["id"]]
        w = float(node.get("weight", 1.0))
        eff = w * mult.get(node.get("group"), 1.0)
        # an event that has not fired is not a neutral vote: it would pull the composite toward 0
        idle = node["section"] == "E" and r.score == 0
        rows.append({
            "id": node["id"], "section": node["section"], "name": node["name"], "zh": node.get("zh", ""),
            "group": node.get("group"), "type": node.get("type"), "score": r.score,
            "signal": IDLE if idle else signal_of(r.score, thr), "choice": r.choice, "weight": w,
            "effective_weight": 0.0 if idle else round(eff, 3), "evidence": r.evidence, "source": r.source,
        })

    # the check.html tally covers only its own 36 items; events join the weighted composite
    checked = [r for r in rows if r["score"] is not None and r["section"] != "E"]
    voting = [r for r in rows if r["score"] is not None and r["effective_weight"] > 0]
    wsum = sum(r["effective_weight"] for r in voting)
    composite = sum(r["score"] * r["effective_weight"] for r in voting) / wsum if wsum else None

    agree = split = None
    if composite not in (None, 0):
        same = sum(r["effective_weight"] for r in voting if r["score"] * composite > 0)
        oppose = sum(r["effective_weight"] for r in voting if r["score"] * composite < 0)
        agree = same / wsum
        split = {"agree": round(agree, 3), "oppose": round(oppose / wsum, 3),
                 "neutral": round((wsum - same - oppose) / wsum, 3)}

    tally = {s: sum(1 for r in checked if r["signal"] == s) for s in (BUY, WAIT, SELL)}
    n = len(checked)
    maj = d.get("checklist_majority", 0.6)
    if not n:
        checklist = UNCHECKED
    elif tally[BUY] / n >= maj:
        checklist = BUY
    elif tally[SELL] / n >= maj:
        checklist = SELL
    else:
        checklist = WAIT

    if composite is None:
        comp_verdict = UNCHECKED
    elif composite >= d.get("buy_above", 0.5):
        comp_verdict = BUY
    elif composite <= d.get("sell_below", -0.5):
        comp_verdict = SELL
    else:
        comp_verdict = WAIT

    # Confidence routing: agreement plays the role of Jev's confidence. A composite past the
    # threshold with little weight behind it is downgraded to WAIT (trigger levels only).
    raw_verdict, routed = comp_verdict, None
    routing = d.get("routing", {})
    min_agree = routing.get("min_agreement", 0.6)
    if routing.get("enabled") and comp_verdict in (BUY, SELL) and agree < min_agree:
        routed = {"from": comp_verdict, "min_agreement": min_agree,
                  "reason": "split" if split["oppose"] > split["neutral"] else "neutral"}
        comp_verdict = WAIT

    primary = comp_verdict if d.get("primary", "composite") == "composite" else checklist
    sections = {}
    for sec in SECTIONS:
        sr = [r for r in rows if r["section"] == sec]
        sections[sec] = {
            "rows": sr,
            "tally": {s: sum(1 for r in sr if r["signal"] == s) for s in (BUY, WAIT, SELL, UNCHECKED, IDLE)},
        }
    return {
        "sections": sections,
        "gates": gates,
        "composite": None if composite is None else round(composite, 3),
        "agreement": None if agree is None else round(agree, 3),
        "weight_split": split,
        "composite_verdict": comp_verdict,
        "raw_composite_verdict": raw_verdict,
        "routed": routed,
        "checklist": {"verdict": checklist, "tally": tally, "checked": n,
                      "total": sum(1 for r in rows if r["section"] != "E"),
                      "buy_pct": round(tally[BUY] / n * 100) if n else 0,
                      "sell_pct": round(tally[SELL] / n * 100) if n else 0},
        "verdict": primary,
        "methods_disagree": raw_verdict != checklist,  # weights vs tally; routing is reported apart
    }


def trade_plan(ctx, verdict, cfg):
    """Entry / stop / targets from the chart's own levels. Long for BUY, short for SELL,
    trigger levels only for WAIT."""
    price, a = ctx.price, ctx.atr[-1]
    if a is None:
        return {"type": "none", "note": "ATR 資料不足，無法計算價位"}
    st, st_dir = ctx.st[-1], ctx.st_dir[-1]
    vs, vs_up = ctx.vstop[-1], ctx.vstop_up[-1]
    levels = {
        "EMA20": ctx.ema_f[-1], "BB 中軌": ctx.bb_mid[-1], "VWMA": ctx.vwma[-1], "Supertrend": st,
        "BB 上軌": ctx.bb_up[-1], "BB 下軌": ctx.bb_lo[-1],
        "Donchian 上軌": ctx.dc_up[-2], "Donchian 下軌": ctx.dc_lo[-2],
        "KC 上軌": ctx.kc_up[-1], "KC 下軌": ctx.kc_lo[-1],
    }
    levels = {k: v for k, v in levels.items() if v is not None}
    min_r, max_r = cfg.get("stop_atr_min", 1.5), cfg.get("stop_atr_max", 4.0)
    rr = cfg.get("target_rr", 2.0)
    near = cfg.get("entry_support_atr", 1.5) * a

    if verdict == WAIT:
        # only levels whose break flips several checklist items count as triggers
        struct = {k: v for k, v in levels.items()
                  if k in ("Supertrend", "BB 上軌", "BB 下軌", "Donchian 上軌", "Donchian 下軌")}
        if vs is not None:
            struct["Volatility Stop"] = vs
        res = [(k, v) for k, v in struct.items() if v > price]
        sup = [(k, v) for k, v in struct.items() if v < price]
        bull = min(res, key=lambda kv: kv[1]) if res else None
        bear = max(sup, key=lambda kv: kv[1]) if sup else None
        return {
            "type": "wait", "price": price, "atr": a,
            "bull_trigger": bull and {"level": bull[1], "ref": bull[0]},
            "bear_trigger": bear and {"level": bear[1], "ref": bear[0]},
        }

    long = verdict == BUY
    side = 1 if long else -1
    # entry zone: from the nearest support (resistance for shorts) within reach, to the current price
    zone_refs = [(k, v) for k, v in levels.items()
                 if k in ("EMA20", "BB 中軌", "VWMA", "Supertrend") and 0 < side * (price - v) <= near]
    if zone_refs:
        ref = max(zone_refs, key=lambda kv: side * kv[1])
        edge = ref[1]
    else:
        ref, edge = ("0.5 ATR", None), price - side * 0.5 * a
    entry = (edge + price) / 2

    structural = []
    if st is not None and st_dir == side and side * (entry - st) > 0:
        structural.append(("Supertrend", st))
    if vs is not None and vs_up == long and side * (entry - vs) > 0:
        structural.append(("Volatility Stop", vs))
    if structural:
        stop_ref, stop = max(structural, key=lambda kv: side * kv[1])  # the nearer structural stop
    else:
        stop_ref, stop = "ATR", entry - side * min_r * a
    notes = []
    if side * (entry - stop) < min_r * a:
        stop, stop_ref = entry - side * min_r * a, f"{stop_ref}，放寬至 {min_r} ATR"
    if side * (entry - stop) > max_r * a:
        stop, stop_ref = entry - side * max_r * a, f"{stop_ref}過遠，收窄至 {max_r} ATR"
        notes.append("結構停損距離過遠，已用 ATR 上限收窄；此停損可能落在雜訊範圍內")
    risk = abs(entry - stop)

    tgt_names = ("BB 上軌", "Donchian 上軌", "KC 上軌") if long else ("BB 下軌", "Donchian 下軌", "KC 下軌")
    tgts = [(k, levels[k]) for k in tgt_names if k in levels and side * (levels[k] - entry) > 0]
    struct_t = min(tgts, key=lambda kv: side * kv[1]) if tgts else ("1R（已在通道外）", entry + side * risk)
    r_t = (f"{rr:g}R", entry + side * rr * risk)
    (t1_ref, t1), (t2_ref, t2) = sorted([struct_t, r_t], key=lambda kv: side * kv[1])
    rr1 = abs(t1 - entry) / risk if risk else None
    rr2 = abs(t2 - entry) / risk if risk else None
    if rr1 is not None and rr1 < 1:
        notes.append(f"T1 風報比僅 {rr1:.2f}，建議等回檔到進場區下緣再進")
    return {
        "type": "long" if long else "short", "price": price, "atr": a,
        "entry_zone": sorted([edge, price]), "entry_ref": ref[0], "entry": entry,
        "stop": stop, "stop_ref": stop_ref, "risk_pct": risk / entry * 100,
        "t1": t1, "t1_ref": t1_ref, "t2": t2, "t2_ref": t2_ref, "rr1": rr1, "rr2": rr2,
        "notes": notes,
    }


def describe_plan(plan):
    """Plain-text lines for the report."""
    t = plan.get("type")
    if t == "none":
        return [plan["note"]]
    if t == "wait":
        out = [f"結論為 WAIT，不給進場價。現價 {fmt(plan['price'])}，ATR {fmt(plan['atr'])}。"]
        if plan["bull_trigger"]:
            b = plan["bull_trigger"]
            out.append(f"轉多觸發：收盤站上 {fmt(b['level'])}（{b['ref']}）")
        if plan["bear_trigger"]:
            b = plan["bear_trigger"]
            out.append(f"轉空觸發：收盤跌破 {fmt(b['level'])}（{b['ref']}）")
        return out
    lo, hi = plan["entry_zone"]
    side = "做多" if t == "long" else "做空（現貨持有者：減碼 / 不開新多單）"
    out = [
        f"方向：{side}",
        f"進場區：{fmt(lo)} – {fmt(hi)}（參考 {plan['entry_ref']}），均價 {fmt(plan['entry'])}",
        f"停損：{fmt(plan['stop'])}（{plan['stop_ref']}），風險 {plan['risk_pct']:.2f}%",
        f"目標 T1：{fmt(plan['t1'])}（{plan['t1_ref']}，風報比 {plan['rr1']:.2f}）",
        f"目標 T2：{fmt(plan['t2'])}（{plan['t2_ref']}，風報比 {plan['rr2']:.2f}）",
    ]
    out += [f"注意：{n}" for n in plan["notes"]]
    return out
