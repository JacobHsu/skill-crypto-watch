"""Decision nodes: turn indicator values into typed judgments.

Each node in config/nodes.toml names a rule below. A rule returns a Result:
  score    -2..+2 (strong sell .. strong buy), None = not checked
  choice   optional category label (Choice-style nodes, e.g. alligator state)
  noul     optional 0..1 probability (gate nodes)
  evidence one line of concrete numbers, in the checklist's wording
  data     the numbers the judgment used (also the state sent to Jev)

Rules only encode the checklist; weights, gates and thresholds live in config.
"""

from dataclasses import dataclass, field

import indicators as ta


@dataclass
class Result:
    score: float = None
    evidence: str = ""
    choice: str = None
    noul: float = None
    data: dict = field(default_factory=dict)
    source: str = "rule"


def fmt(x):
    if x is None:
        return "n/a"
    a = abs(x)
    if a >= 1000:
        return f"{x:,.1f}"
    if a >= 1:
        return f"{x:,.2f}"
    return f"{x:.4g}"


def clamp01(x):
    return max(0.0, min(1.0, x))


def rising(s, bars=2):
    w = s[-bars - 1:]
    return len(w) == bars + 1 and all(v is not None for v in w) and all(b > a for a, b in zip(w, w[1:]))


def falling(s, bars=2):
    w = s[-bars - 1:]
    return len(w) == bars + 1 and all(v is not None for v in w) and all(b < a for a, b in zip(w, w[1:]))


# ------------------------------------------------------------------ context

class Context:
    """All indicator series for one symbol and timeframe, computed once."""

    def __init__(self, bars, htf=None, settings=None):
        s = settings or {}
        g = lambda name, default: {**default, **s.get(name, {})}  # noqa: E731
        self.bars, self.htf, self.tf = bars, htf, bars["interval"]
        o, h, l, c, v = bars["open"], bars["high"], bars["low"], bars["close"], bars["volume"]
        self.o, self.h, self.l, self.c, self.v = o, h, l, c, v
        self.price = c[-1]
        self.n = len(c)

        p = g("ma", {"fast": 20, "slow": 50})
        self.sma_f, self.sma_s = ta.sma(c, p["fast"]), ta.sma(c, p["slow"])
        self.ema_f, self.ema_s = ta.ema(c, p["fast"]), ta.ema(c, p["slow"])
        self.ma_len = (p["fast"], p["slow"])
        p = g("ma_cross", {"short": 9, "long": 21})
        self.cross_s, self.cross_l = ta.sma(c, p["short"]), ta.sma(c, p["long"])
        self.cross_len = (p["short"], p["long"])
        p = g("bb", {"length": 20, "mult": 2.0})
        self.bb_mid, self.bb_up, self.bb_lo, self.bb_w = ta.bollinger(c, p["length"], p["mult"])
        p = g("kc", {"length": 20, "mult": 2.0, "atr_length": 10})
        self.kc_mid, self.kc_up, self.kc_lo = ta.keltner(h, l, c, p["length"], p["mult"], p["atr_length"])
        p = g("donchian", {"length": 20})
        self.dc_up, self.dc_lo, self.dc_mid = ta.donchian(h, l, p["length"])
        self.atr = ta.atr(h, l, c, g("atr", {"length": 14})["length"])
        self.rsi = ta.rsi(c, g("rsi", {"length": 14})["length"])
        p = g("macd", {"fast": 12, "slow": 26, "signal": 9})
        self.macd, self.macd_sig, self.macd_hist = ta.macd(c, p["fast"], p["slow"], p["signal"])
        p = g("dmi", {"di_length": 14, "adx_length": 14})
        self.pdi, self.mdi, self.adx = ta.dmi(h, l, c, p["di_length"], p["adx_length"])
        self.aroon_up, self.aroon_dn = ta.aroon(h, l, g("aroon", {"length": 14})["length"])
        self.cci = ta.cci(h, l, c, g("cci", {"length": 20})["length"])
        p = g("uo", {"fast": 7, "mid": 14, "slow": 28})
        self.uo = ta.ultimate_oscillator(h, l, c, p["fast"], p["mid"], p["slow"])
        p = g("stoch_rsi", {"k": 3, "d": 3, "rsi_length": 14, "stoch_length": 14})
        self.srsi_k, self.srsi_d = ta.stoch_rsi(c, p["k"], p["d"], p["rsi_length"], p["stoch_length"])
        self.roc = ta.roc(c, g("roc", {"length": 9})["length"])
        self.hma = ta.hma(c, g("hma", {"length": 9})["length"])
        self.vwma = ta.vwma(c, v, g("vwma", {"length": 20})["length"])
        self.obv = ta.obv(c, v)
        self.mfi = ta.mfi(h, l, c, v, g("mfi", {"length": 14})["length"])
        self.cmf = ta.cmf(h, l, c, v, g("cmf", {"length": 20})["length"])
        self.ci = ta.choppiness(h, l, c, g("choppiness", {"length": 14})["length"])
        per = 7 if self.tf in ("1w", "1M") else 1
        self.hv = ta.historical_volatility(c, g("hv", {"length": 10})["length"], 365, per)
        p = g("supertrend", {"atr_length": 10, "factor": 3.0})
        self.st, self.st_dir = ta.supertrend(h, l, c, p["atr_length"], p["factor"])
        p = g("psar", {"start": 0.02, "increment": 0.02, "maximum": 0.2})
        self.sar, self.sar_dir = ta.psar(h, l, c, p["start"], p["increment"], p["maximum"])
        self.jaw, self.teeth, self.lips = ta.alligator(h, l)
        self.fr_up, self.fr_dn = ta.fractals(h, l, g("fractals", {"periods": 2})["periods"])
        p = g("zigzag", {"deviation": 5.0, "depth": 10})
        self.zz = ta.zigzag(h, l, p["deviation"], p["depth"])
        p = g("volatility_stop", {"length": 20, "mult": 2.0})
        self.vstop, self.vstop_up = ta.volatility_stop(c, h, l, p["length"], p["mult"])
        p = g("linreg", {"length": 100})
        self.lr_val, self.lr_slope, self.lr_dev = ta.linreg(c, p["length"])
        self.lr_len = p["length"]


# ------------------------------------------------------------------ rules
# signature: rule(ctx, params, results) -> Result
# `results` holds earlier nodes' Results by id, for combination nodes.

def r_mtp(x, p, _):
    if not x.htf:
        return Result(None, "沒有較高級別資料")
    k = p.get("count", 11)
    o, c = x.htf["open"][-k:], x.htf["close"][-k:]
    green = sum(1 for a, b in zip(o, c) if b > a)
    red = len(c) - green
    ratio = green / len(c)
    maj = p.get("majority", 0.6)
    score = 1 if ratio >= maj else -1 if ratio <= 1 - maj else 0
    last = "綠" if c[-1] > o[-1] else "紅"
    return Result(score, f"近 {len(c)} 根 {x.htf['interval']} K：{green} 綠 {red} 紅，最新一根{last}",
                  data={"green": green, "red": red, "htf": x.htf["interval"]})


def r_fractals(x, p, _):
    if not x.fr_up and not x.fr_dn:
        return Result(None, "沒有已確認的碎形")
    c = x.c

    def first_break(fr, above):
        if not fr:
            return None, None
        i, lvl = fr[-1]
        for j in range(i + 1, len(c)):
            if (c[j] > lvl) if above else (c[j] < lvl):
                return (i, lvl), j
        return (i, lvl), None

    up, up_j = first_break(x.fr_up, True)
    dn, dn_j = first_break(x.fr_dn, False)
    state = None
    if up_j is not None and (dn_j is None or up_j > dn_j):
        state = "up"
    elif dn_j is not None:
        state = "down"
    parts = []
    if up:
        parts.append(f"最近 △ {fmt(up[1])}（{len(c) - 1 - up[0]} 根前）" + ("已被收盤突破" if up_j is not None else "未突破"))
    if dn:
        parts.append(f"最近 ▽ {fmt(dn[1])}（{len(c) - 1 - dn[0]} 根前）" + ("已被收盤跌破" if dn_j is not None else "未跌破"))
    score = 1 if state == "up" else -1 if state == "down" else 0
    return Result(score, "；".join(parts), choice=state or "none",
                  data={"state": state, "up": up and up[1], "down": dn and dn[1]})


def r_alligator(x, p, _):
    jaw, teeth, lips, a = x.jaw[-1], x.teeth[-1], x.lips[-1], x.atr[-1]
    if None in (jaw, teeth, lips, a):
        return Result(None, "鱷魚線資料不足")
    spread = (max(jaw, teeth, lips) - min(jaw, teeth, lips)) / a
    c = x.price
    if spread < p.get("min_spread_atr", 0.3):
        state = "sleeping"
    elif lips > teeth > jaw and c > lips:
        state = "up"
    elif jaw > teeth > lips and c < lips:
        state = "down"
    else:
        state = "mixed"
    score = {"up": 1, "down": -1}.get(state, 0)
    label = {"sleeping": "三線糾纏（睡覺）", "up": "張口向上", "down": "張口向下", "mixed": "三線分開但價格未在外側"}[state]
    return Result(score, f"綠 {fmt(lips)} / 紅 {fmt(teeth)} / 藍 {fmt(jaw)}，收盤 {fmt(c)}，開口 {spread:.2f}×ATR → {label}",
                  choice=state, data={"lips": lips, "teeth": teeth, "jaw": jaw, "spread_atr": round(spread, 3)})


def r_alligator_x_fractal(x, p, res):
    a, f = res.get("alligator"), res.get("fractals")
    if not a or not f or a.score is None or f.score is None:
        return Result(None, "鱷魚或碎形未核對")
    fs = f.data.get("state")
    if a.choice == "up" and fs == "up":
        return Result(2, "鱷魚張口向上 + 收盤突破 △（強力訊號）")
    if a.choice == "down" and fs == "down":
        return Result(-2, "鱷魚張口向下 + 收盤跌破 ▽（強力訊號）")
    if a.choice == "sleeping":
        return Result(0, "鱷魚睡覺，碎形訊號無效")
    return Result(0, f"鱷魚 {a.choice}、碎形 {fs or '未突破'}，方向不一致或未確認")


def r_psar(x, p, _):
    d, s = x.sar_dir, x.sar[-1]
    if d[-1] is None:
        return Result(None, "SAR 資料不足")
    since = next((k for k in range(1, len(d)) if d[-1 - k] != d[-1]), len(d))
    where = "下方" if d[-1] == 1 else "上方"
    fresh = "剛翻到" if since <= p.get("fresh_bars", 3) else "持續在"
    return Result(d[-1], f"SAR {fmt(s)} {fresh} K 線{where}（已 {since} 根）", data={"sar": s, "bars_since_flip": since})


def r_bollinger(x, p, _):
    up, lo, mid, w = x.bb_up[-1], x.bb_lo[-1], x.bb_mid[-1], x.bb_w
    k = p.get("width_lookback", 3)
    if None in (up, lo, mid) or w[-1 - k] is None:
        return Result(None, "布林資料不足")
    expanding = w[-1] > w[-1 - k]
    trend = "擴大" if expanding else "收窄"
    c = x.price
    base = f"上軌 {fmt(up)} / 中軌 {fmt(mid)} / 下軌 {fmt(lo)}，收盤 {fmt(c)}，帶寬{trend}（{w[-1 - k] * 100:.2f}% → {w[-1] * 100:.2f}%）"
    data = {"upper": up, "mid": mid, "lower": lo, "width_now": w[-1], "width_prev": w[-1 - k]}
    if x.h[-1] >= up and c >= mid:
        return Result(1 if expanding else 0, base + (" → 觸上軌且張開" if expanding else " → 觸上軌但收窄，注意假突破"), data=data)
    if x.l[-1] <= lo and c <= mid:
        return Result(-1 if expanding else 0, base + (" → 觸下軌且張開" if expanding else " → 觸下軌但收窄，可能反彈"), data=data)
    return Result(0, base + " → 在軌道內游走", data=data)


def r_keltner(x, p, _):
    hold = p.get("hold_bars", 2)
    up, lo = x.kc_up, x.kc_lo
    if any(v is None for v in up[-hold:] + lo[-hold:]):
        return Result(None, "肯特納資料不足")
    c = x.c
    base = f"上軌 {fmt(up[-1])} / 下軌 {fmt(lo[-1])}，收盤 {fmt(c[-1])}"
    if all(c[-1 - i] > up[-1 - i] for i in range(hold)):
        return Result(1, base + f" → 連續 {hold} 根收在上軌外，站穩")
    if all(c[-1 - i] < lo[-1 - i] for i in range(hold)):
        return Result(-1, base + f" → 連續 {hold} 根收在下軌外，站穩")
    return Result(0, base + " → 在通道內")


def r_squeeze(x, p, _):
    bu, bl, ku, kl = x.bb_up[-1], x.bb_lo[-1], x.kc_up[-1], x.kc_lo[-1]
    if None in (bu, bl, ku, kl):
        return Result(None, "BB/KC 資料不足")
    on = bu < ku and bl > kl
    c = x.price
    base = f"BB {fmt(bl)}–{fmt(bu)}，KC {fmt(kl)}–{fmt(ku)}"
    if on:
        return Result(0, base + " → BB 收在 KC 內，Squeeze ON 蓄勢", choice="on", noul=1.0)
    if c > ku:
        return Result(1, base + f" → Squeeze OFF，收盤 {fmt(c)} 向上突破 KC", choice="off_up", noul=0.0)
    if c < kl:
        return Result(-1, base + f" → Squeeze OFF，收盤 {fmt(c)} 向下跌破 KC", choice="off_down", noul=0.0)
    return Result(0, base + " → Squeeze OFF，但尚未突破 KC", choice="off", noul=0.0)


def r_ma_cross(x, p, _):
    s, l = x.cross_s, x.cross_l
    lb = p.get("lookback", 3)
    if any(v is None for v in s[-lb - 1:] + l[-lb - 1:]):
        return Result(None, "均線資料不足")
    a, b = x.cross_len
    for k in range(lb):
        i = -1 - k
        prev, now = s[i - 1] - l[i - 1], s[i] - l[i]
        if prev <= 0 < now:
            return Result(1, f"MA{a} 於 {k} 根前上穿 MA{b}（金叉）")
        if prev >= 0 > now:
            return Result(-1, f"MA{a} 於 {k} 根前下穿 MA{b}（死叉）")
    rel = "上方" if s[-1] > l[-1] else "下方"
    return Result(0, f"近 {lb} 根無交叉，MA{a} {fmt(s[-1])} 在 MA{b} {fmt(l[-1])} {rel}")


def r_vol_stop(x, p, _):
    st, up = x.vstop[-1], x.vstop_up[-1]
    if st is None:
        return Result(None, "波動停損資料不足")
    return Result(1 if up else -1, f"停損線 {fmt(st)}（{'綠，在 K 線下方' if up else '紅，在 K 線上方'}），收盤 {fmt(x.price)}",
                  data={"stop": st, "up": up})


def r_ma_align(x, p, _):
    kind = p.get("kind", "sma")
    f, s = (x.sma_f, x.sma_s) if kind == "sma" else (x.ema_f, x.ema_s)
    name = "MA" if kind == "sma" else "EMA"
    a, b = x.ma_len
    if f[-1] is None or s[-1] is None:
        return Result(None, f"{name} 資料不足")
    c = x.price
    base = f"收盤 {fmt(c)} / {name}{a} {fmt(f[-1])} / {name}{b} {fmt(s[-1])}"
    if c > f[-1] > s[-1]:
        return Result(1, base + " → 多頭排列", choice="bull")
    if s[-1] > f[-1] > c:
        return Result(-1, base + " → 空頭排列", choice="bear")
    return Result(0, base + " → 排列未完成", choice="mixed")


def r_donchian(x, p, _):
    up, lo = x.dc_up[-2], x.dc_lo[-2]
    if up is None or lo is None:
        return Result(None, "唐奇安資料不足")
    c = x.price
    pos = (c - lo) / (up - lo) * 100 if up > lo else 50
    base = f"前根通道 {fmt(lo)}–{fmt(up)}，收盤 {fmt(c)}"
    if c > up:
        return Result(1, base + " → 收盤突破上軌（創新高）")
    if c < lo:
        return Result(-1, base + " → 收盤跌破下軌（創新低）")
    return Result(0, base + f" → 在通道內（{pos:.0f}% 位置），無突破")


def r_agree(x, p, res):
    """Two-node resonance: +1 when both are bullish, -1 when both are bearish, else 0."""
    a, b = res.get(p["a"]), res.get(p["b"])
    if not a or not b or a.score is None or b.score is None:
        return Result(None, f"{p['a']} 或 {p['b']} 未核對")
    s = p.get("score", 1)
    if a.score > 0 and b.score > 0:
        return Result(s, f"{p['a']} 與 {p['b']} 同為多方 → 共振")
    if a.score < 0 and b.score < 0:
        return Result(-s, f"{p['a']} 與 {p['b']} 同為空方 → 共振")
    return Result(0, f"{p['a']}（{a.score:+g}）與 {p['b']}（{b.score:+g}）方向不一致")


def r_zigzag(x, p, _):
    hs = [pv for pv in x.zz if pv[2] == "H"][-2:]
    ls = [pv for pv in x.zz if pv[2] == "L"][-2:]
    if len(hs) < 2 or len(ls) < 2:
        return Result(None, f"轉折點不足（只有 {len(x.zz)} 個），此級別波動未達 Zig Zag 偏差門檻")
    hh, hl = hs[1][1] > hs[0][1], ls[1][1] > ls[0][1]
    base = f"高點 {fmt(hs[0][1])} → {fmt(hs[1][1])}，低點 {fmt(ls[0][1])} → {fmt(ls[1][1])}"
    data = {"pivots": [{"bars_ago": x.n - 1 - i, "price": v, "kind": k} for i, v, k in x.zz[-6:]]}
    if hh and hl:
        return Result(1, base + " → 高低點遞升", choice="ascending", data=data)
    if not hh and not hl:
        return Result(-1, base + " → 高低點遞降", choice="descending", data=data)
    return Result(0, base + " → 高低點無規律", choice="mixed", data=data)


def r_supertrend(x, p, _):
    line = x.st[-1]
    if line is None:
        return Result(None, "Supertrend 資料不足")
    c = x.price
    above = c > line
    return Result(1 if above else -1, f"收盤 {fmt(c)} 在 Supertrend {fmt(line)} {'上方' if above else '下方'}",
                  data={"line": line})


def r_linreg(x, p, _):
    val, slope, dev = x.lr_val, x.lr_slope, x.lr_dev
    if val is None:
        return Result(None, f"不足 {x.lr_len} 根 K 線")
    c = x.price
    total = slope * x.lr_len / c * 100
    flat = abs(total) < p.get("flat_pct", 2.0)
    near = abs(c - val) < p.get("near_dev", 0.5) * dev
    slope_txt = "近乎水平" if flat else ("向上" if slope > 0 else "向下")
    base = f"迴歸線 {fmt(val)}，{x.lr_len} 根斜率 {total:+.1f}%（{slope_txt}），收盤 {fmt(c)}"
    data = {"value": val, "slope_pct": total, "flat": flat}
    if c > val and not near and slope > 0 and not flat:
        return Result(1, base + " → 線上方且向上", data=data)
    if c < val and not near and slope < 0 and not flat:
        return Result(-1, base + " → 線下方且向下", data=data)
    return Result(0, base + " → 貼近迴歸線或方向不一致", data=data)


def r_sar_x_linreg(x, p, res):
    s, lr = res.get("psar"), res.get("linreg")
    if not s or not lr or s.score is None or lr.score is None:
        return Result(None, "SAR 或迴歸線未核對")
    up = not lr.data["flat"] and lr.data["slope_pct"] > 0
    dn = not lr.data["flat"] and lr.data["slope_pct"] < 0
    if s.score > 0 and up:
        return Result(1, "SAR 在下方 + 迴歸線斜率向上 → 短中期一致")
    if s.score < 0 and dn:
        return Result(-1, "SAR 在上方 + 迴歸線斜率向下 → 短中期一致")
    return Result(0, "SAR 與迴歸線方向不一致（含迴歸線走平）")


def r_vwma(x, p, _):
    v, k = x.vwma, p.get("slope_bars", 3)
    if v[-1] is None or v[-1 - k] is None:
        return Result(None, "VWMA 資料不足")
    c, a = x.price, x.atr[-1] or 0
    near = abs(c - v[-1]) < p.get("near_atr", 0.25) * a
    up, dn = v[-1] > v[-1 - k], v[-1] < v[-1 - k]
    base = f"收盤 {fmt(c)} / VWMA {fmt(v[-1])}（{k} 根前 {fmt(v[-1 - k])}）"
    if not near and c > v[-1] and up:
        return Result(1, base + " → 價在上且線向上")
    if not near and c < v[-1] and dn:
        return Result(-1, base + " → 價在下且線向下")
    return Result(0, base + " → 貼近 VWMA 或方向不一致")


def r_macd(x, p, _):
    h = x.macd_hist
    k = p.get("rising_bars", 2)
    if any(v is None for v in h[-k - 1:]):
        return Result(None, "MACD 資料不足")
    seq = " → ".join(fmt(v) for v in h[-k - 1:])
    if h[-1] > 0 and rising(h, k):
        return Result(1, f"柱狀 {seq}：正值且持續增高")
    if h[-1] < 0 and falling(h, k):
        return Result(-1, f"柱狀 {seq}：負值且持續加深")
    return Result(0, f"柱狀 {seq}：動能減弱或貼近零軸")


def r_macd_cross(x, p, _):
    """Event, not state: the MACD line crossed its signal line (histogram changed sign) recently."""
    h, m = x.macd_hist, x.macd
    lb = p.get("lookback", 3)
    if any(v is None for v in h[-lb - 1:]):
        return Result(None, "MACD 資料不足")
    for k in range(lb):
        i = -1 - k
        zone = "零軸上方" if m[i] > 0 else "零軸下方"
        if h[i - 1] <= 0 < h[i]:
            return Result(1, f"MACD 於 {k} 根前{zone}黃金交叉（柱 {fmt(h[i - 1])} → {fmt(h[i])}）",
                          data={"cross": "golden", "bars_ago": k, "macd": m[i]})
        if h[i - 1] >= 0 > h[i]:
            return Result(-1, f"MACD 於 {k} 根前{zone}死亡交叉（柱 {fmt(h[i - 1])} → {fmt(h[i])}）",
                          data={"cross": "death", "bars_ago": k, "macd": m[i]})
    return Result(0, f"近 {lb} 根 MACD 無交叉（柱 {fmt(h[-1])}）", data={"cross": None})


def r_dmi(x, p, _):
    a, pd, md = x.adx[-1], x.pdi[-1], x.mdi[-1]
    if None in (a, pd, md):
        return Result(None, "DMI 資料不足")
    thr = p.get("adx_min", 25)
    base = f"+DI {pd:.1f} / −DI {md:.1f} / ADX {a:.1f}"
    if a <= thr:
        return Result(0, base + f" → ADX 未站上 {thr}，盤整")
    if pd > md:
        return Result(p.get("bull_score", 1), base + " → 多方佔優且趨勢確立")
    return Result(p.get("bear_score", -1), base + " → 空方佔優且趨勢確立")


def r_cci(x, p, _):
    v = x.cci
    if v[-1] is None or v[-2] is None:
        return Result(None, "CCI 資料不足")
    lvl = p.get("level", 100)
    base = f"CCI {v[-2]:.0f} → {v[-1]:.0f}"
    if v[-1] > lvl:
        return Result(p.get("above_score", 1) if v[-1] >= v[-2] else 0, base + f" → +{lvl} 以上" + ("且走高" if v[-1] >= v[-2] else "但回落"))
    if v[-1] < -lvl:
        return Result(p.get("below_score", -1) if v[-1] <= v[-2] else 0, base + f" → −{lvl} 以下" + ("且走低" if v[-1] <= v[-2] else "但回升"))
    return Result(0, base + f" → ±{lvl} 之間")


def r_supertrend_x_macd(x, p, _):
    line, h = x.st[-1], x.macd_hist[-1]
    if line is None or h is None:
        return Result(None, "Supertrend 或 MACD 資料不足")
    above = x.price > line
    if above and h > 0:
        return Result(1, f"K 線在 Supertrend 上方 + MACD 正值柱 {fmt(h)} → 多頭共振")
    if not above and h < 0:
        return Result(-1, f"K 線在 Supertrend 下方 + MACD 負值柱 {fmt(h)} → 空頭共振")
    return Result(0, f"K 線在 Supertrend {'上' if above else '下'}方、MACD 柱 {fmt(h)} → 趨勢與動能矛盾")


def r_hma(x, p, _):
    m, k = x.hma, p.get("slope_bars", 2)
    if m[-1] is None or m[-1 - k] is None:
        return Result(None, "HMA 資料不足")
    c = x.price
    base = f"收盤 {fmt(c)} / HMA {fmt(m[-1])}（{k} 根前 {fmt(m[-1 - k])}）"
    if c > m[-1] and m[-1] > m[-1 - k]:
        return Result(1, base + " → 價在上且上揚")
    if c < m[-1] and m[-1] < m[-1 - k]:
        return Result(-1, base + " → 價在下且下彎")
    return Result(0, base + " → 貼線或走平")


def _new_high(s, lookback, recent=3):
    w = [v for v in s[-lookback:] if v is not None]
    return bool(w) and max(v for v in s[-recent:] if v is not None) >= max(w)


def _new_low(s, lookback, recent=3):
    w = [v for v in s[-lookback:] if v is not None]
    return bool(w) and min(v for v in s[-recent:] if v is not None) <= min(w)


def r_rsi(x, p, _):
    r = x.rsi[-1]
    if r is None:
        return Result(None, "RSI 資料不足")
    ob, os_ = p.get("overbought", 70), p.get("oversold", 30)
    lo_n, hi_n = p.get("neutral_band", [45, 55])
    lb = p.get("divergence_lookback", 20)
    base = f"RSI {r:.1f}（前值 {x.rsi[-2]:.1f}）"
    data = {"rsi": r, "rsi_prev": x.rsi[-2]}
    if r > ob:
        p_nh, r_nh = _new_high(x.h, lb), _new_high(x.rsi, lb)
        if p_nh and r_nh:
            return Result(1, base + f"，超買且與價格同步創 {lb} 根新高 → 強勢延續", data=data)
        if p_nh:
            return Result(-1, base + f"，價創 {lb} 根新高但 RSI 未創高 → 頂背離", data=data)
        return Result(p.get("overbought_score", 0), base + "，超買區，無新高可比對", data=data)
    if r < os_:
        p_nl, r_nl = _new_low(x.l, lb), _new_low(x.rsi, lb)
        if p_nl and r_nl:
            return Result(-1, base + f"，超賣且與價格同步創 {lb} 根新低 → 弱勢延續", data=data)
        if p_nl:
            return Result(1, base + f"，價創 {lb} 根新低但 RSI 未創低 → 底背離", data=data)
        return Result(p.get("oversold_score", 0), base + "，超賣區，無新低可比對", data=data)
    if lo_n <= r <= hi_n:
        return Result(0, base + f"，{lo_n}–{hi_n} 多空均衡", data=data)
    return Result(1 if r > hi_n else -1, base + ("，偏多未超買" if r > hi_n else "，偏空未超賣"), data=data)


def r_stoch_rsi(x, p, _):
    k, d = x.srsi_k, x.srsi_d
    lb, lo, hi = p.get("lookback", 2), p.get("low", 20), p.get("high", 80)
    if any(v is None for v in k[-lb - 1:] + d[-lb - 1:]):
        return Result(None, "Stoch RSI 資料不足")
    base = f"%K {k[-1]:.1f} / %D {d[-1]:.1f}"
    for j in range(lb):
        i = -1 - j
        if k[i - 1] <= d[i - 1] and k[i] > d[i] and min(k[i - 1], d[i - 1]) < lo:
            return Result(1, base + f"，{j} 根前於 {lo} 以下黃金交叉")
        if k[i - 1] >= d[i - 1] and k[i] < d[i] and max(k[i - 1], d[i - 1]) > hi:
            return Result(-1, base + f"，{j} 根前於 {hi} 以上死亡交叉")
    return Result(0, base + "，無低檔金叉或高檔死叉")


def r_uo(x, p, _):
    u = x.uo
    if u[-1] is None or u[-2] is None:
        return Result(None, "UO 資料不足")
    ob, os_ = p.get("overbought", 70), p.get("oversold", 30)
    base = f"UO {u[-2]:.1f} → {u[-1]:.1f}"
    if u[-1] > ob:
        return Result(p.get("overbought_score", -1), base + f"，> {ob} 超買")
    if u[-1] < os_:
        return Result(p.get("oversold_score", 1), base + f"，< {os_} 超賣")
    if u[-1] > 50 and u[-1] > u[-2]:
        return Result(1, base + "，50 以上且走高")
    if u[-1] < 50 and u[-1] < u[-2]:
        return Result(-1, base + "，50 以下且走低")
    return Result(0, base + "，中性")


def r_atr(x, p, _):
    a, k = x.atr, p.get("lookback", 3)
    if a[-1] is None or a[-1 - k] is None:
        return Result(None, "ATR 資料不足")
    chg = (a[-1] / a[-1 - k] - 1) * 100
    pc = x.c[-1] - x.c[-1 - k]
    base = f"ATR {fmt(a[-1 - k])} → {fmt(a[-1])}（{chg:+.1f}%），{k} 根價格 {pc / x.c[-1 - k] * 100:+.2f}%"
    if chg > p.get("min_change_pct", 3):
        return Result(1 if pc > 0 else -1, base + (" → 波動擴大配合上漲" if pc > 0 else " → 波動擴大配合下跌"))
    return Result(0, base + " → 波動收縮或持平")


def r_choppiness(x, p, _):
    ci = x.ci[-1]
    if ci is None or x.bb_mid[-1] is None:
        return Result(None, "CI 資料不足")
    lo, hi = p.get("trend_below", 38.2), p.get("chop_above", 61.8)
    c, mid, up, low = x.price, x.bb_mid[-1], x.bb_up[-1], x.bb_lo[-1]
    base = f"CI {ci:.1f}"
    if ci < lo:
        if c >= mid + 0.5 * (up - mid):
            return Result(1, base + f" < {lo} 趨勢明顯，收盤貼近布林上軌")
        if c <= mid - 0.5 * (mid - low):
            return Result(-1, base + f" < {lo} 趨勢明顯，收盤貼近布林下軌")
        return Result(0, base + f" < {lo} 趨勢明顯，但價格在布林中段")
    if ci > hi:
        return Result(0, base + f" > {hi} 盤整，別追勢")
    return Result(0, base + f"，{lo}–{hi} 中間區")


def r_hv(x, p, _):
    hv, win, k = x.hv, p.get("window", 100), p.get("lookback", 3)
    w = [v for v in hv[-win:] if v is not None]
    if len(w) < win // 2 or hv[-1 - k] is None:
        return Result(None, "歷史波動率資料不足")
    pct = sum(1 for v in w if v <= hv[-1]) / len(w) * 100
    base = f"HV {hv[-1]:.1f}（近 {len(w)} 根第 {pct:.0f} 百分位）"
    if pct > p.get("high_pct", 70):
        return Result(0, base + " → 相對高檔，常為行情末段")
    if pct < p.get("low_pct", 30):
        if hv[-1] > hv[-1 - k]:
            c = x.price
            if c > x.bb_up[-1] or c > x.dc_up[-2]:
                return Result(1, base + " → 低檔翻揚 + 向上突破")
            if c < x.bb_lo[-1] or c < x.dc_lo[-2]:
                return Result(-1, base + " → 低檔翻揚 + 向下突破")
            return Result(0, base + " → 低檔翻揚，尚未突破")
        return Result(0, base + " → 低檔持續低迷，蓄勢")
    return Result(0, base + " → 中段")


def r_obv(x, p, _):
    k = p.get("lookback", 10)
    if x.n <= k:
        return Result(None, "OBV 資料不足")
    ob_up = x.obv[-1] > x.obv[-1 - k]
    pr_up = x.c[-1] > x.c[-1 - k]
    base = f"{k} 根內 OBV {'上升' if ob_up else '下降'}、價格 {'上升' if pr_up else '下降'}"
    if ob_up and pr_up:
        return Result(1, base + " → 量價齊揚")
    if not ob_up and not pr_up:
        return Result(-1, base + " → 量價齊跌")
    return Result(0, base + " → 量價背離")


def r_mfi(x, p, _):
    m = x.mfi
    if m[-1] is None or m[-2] is None:
        return Result(None, "MFI 資料不足")
    ob = p.get("overbought", 80)
    lo_n, hi_n = p.get("neutral_band", [45, 55])
    base = f"MFI {m[-2]:.1f} → {m[-1]:.1f}"
    if m[-1] > ob:
        lb = p.get("divergence_lookback", 20)
        if _new_high(x.h, lb) and not _new_high(m, lb):
            return Result(-1, base + f"，> {ob} 且價創新高而 MFI 未同步 → 頂背離")
        if p.get("overbought_score") is not None:
            return Result(p["overbought_score"], base + f"，> {ob} 超買")
    if lo_n <= m[-1] <= hi_n:
        return Result(0, base + "，50 附近")
    if m[-1] > 50 and m[-1] > m[-2]:
        return Result(1, base + "，50 以上且走高")
    if m[-1] < 50 and m[-1] < m[-2]:
        return Result(-1, base + "，50 以下且走低")
    return Result(0, base + "，方向不一致")


def r_cmf(x, p, _):
    v, k = x.cmf, p.get("slope_bars", 2)
    if v[-1] is None or v[-1 - k] is None:
        return Result(None, "CMF 資料不足")
    z = p.get("zero_band", 0.05)
    base = f"CMF {v[-1 - k]:+.3f} → {v[-1]:+.3f}"
    if abs(v[-1]) < z:
        return Result(0, base + f"，貼近零軸（±{z}）")
    if v[-1] > 0 and v[-1] > v[-1 - k]:
        return Result(1, base + "，正值且上升")
    if v[-1] < 0 and v[-1] < v[-1 - k]:
        return Result(-1, base + "，負值且下降")
    return Result(0, base + "，方向不一致")


def r_aroon(x, p, _):
    u, d = x.aroon_up[-1], x.aroon_dn[-1]
    if u is None:
        return Result(None, "Aroon 資料不足")
    s, gap = p.get("strong", 70), p.get("gap", 30)
    base = f"Up {u:.0f} / Down {d:.0f}"
    if u >= s and u - d >= gap:
        return Result(1, base + " → 上升趨勢")
    if d >= s and d - u >= gap:
        return Result(-1, base + " → 下降趨勢")
    return Result(0, base + " → 趨勢未成形")


def r_roc(x, p, _):
    r = x.roc
    if r[-1] is None or r[-2] is None:
        return Result(None, "ROC 資料不足")
    z = p.get("zero_band", 0.5)
    base = f"ROC {r[-2]:+.2f}% → {r[-1]:+.2f}%"
    if r[-1] > z and r[-1] > r[-2]:
        return Result(1, base + "，零軸上且走高")
    if r[-1] < -z and r[-1] < r[-2]:
        return Result(-1, base + "，零軸下且走低")
    return Result(0, base + "，貼近零軸或方向不一致")


# ------------------------------------------------------------------ events (section E)
# An event node answers "did X just happen?". 0 means it did not fire; decide.py then leaves it
# out of the composite instead of counting it as a neutral vote.

def r_external_event(x, p, _):
    """IDLE placeholder replaced by a validated external event at runtime."""
    return Result(0, "未提供外部事件資料", choice="no_signal", source="external")

def _divergence(x, osc, lb, recent):
    """+1 bullish (price new low, oscillator higher low), -1 bearish, 0 none, None if short."""
    if len(x.c) < lb + recent or any(v is None for v in osc[-lb - recent:]):
        return None, ""
    lo_now, lo_before = min(x.l[-recent:]), min(x.l[-lb - recent:-recent])
    hi_now, hi_before = max(x.h[-recent:]), max(x.h[-lb - recent:-recent])
    o_now_lo, o_before_lo = min(osc[-recent:]), min(osc[-lb - recent:-recent])
    o_now_hi, o_before_hi = max(osc[-recent:]), max(osc[-lb - recent:-recent])
    bull = lo_now < lo_before and o_now_lo > o_before_lo
    bear = hi_now > hi_before and o_now_hi < o_before_hi
    if bull and not bear:
        return 1, f"價格創 {lb} 根新低 {fmt(lo_now)}（前低 {fmt(lo_before)}），指標低點墊高 {o_before_lo:.1f} → {o_now_lo:.1f}：底背離"
    if bear and not bull:
        return -1, f"價格創 {lb} 根新高 {fmt(hi_now)}（前高 {fmt(hi_before)}），指標高點下降 {o_before_hi:.1f} → {o_now_hi:.1f}：頂背離"
    return 0, "無背離"


def _div_rule(series, name):
    def rule(x, p, _):
        s, text = _divergence(x, getattr(x, series), p.get("lookback", 20), p.get("recent", 3))
        if s is None:
            return Result(None, f"{name} 資料不足")
        return Result(s, f"{name}：{text}")
    return rule


def _last_flip(seq, lb):
    """Bars ago that seq last changed sign within the last lb bars, else None."""
    for k in range(lb):
        a, b = seq[-2 - k], seq[-1 - k]
        if a is not None and b is not None and (a > 0) != (b > 0):
            return k
    return None


def r_supertrend_flip(x, p, _):
    d, lb = x.st_dir, p.get("lookback", 3)
    if any(v is None for v in d[-lb - 1:]):
        return Result(None, "Supertrend 資料不足")
    k = _last_flip(d, lb)
    if k is None:
        return Result(0, f"近 {lb} 根 Supertrend 未翻轉")
    return Result(d[-1], f"Supertrend {k} 根前翻{'多' if d[-1] > 0 else '空'}（線 {fmt(x.st[-1])}）")


def r_squeeze_fire(x, p, _):
    lb = p.get("lookback", 3)
    bu, bl, ku, kl = x.bb_up, x.bb_lo, x.kc_up, x.kc_lo
    if any(v is None for v in bu[-lb - 1:] + ku[-lb - 1:]):
        return Result(None, "BB/KC 資料不足")
    on = [bu[i] < ku[i] and bl[i] > kl[i] for i in range(-lb - 1, 0)]
    if on[-1]:
        return Result(0, "Squeeze 仍在進行（BB 收在 KC 內）")
    fired = next((k for k in range(lb) if on[-2 - k] and not on[-1 - k]), None)
    if fired is None:
        return Result(0, f"近 {lb} 根沒有 Squeeze 釋放")
    up = x.price > x.bb_mid[-1]
    return Result(1 if up else -1, f"Squeeze {fired} 根前釋放，收盤 {fmt(x.price)} 在 BB 中軌 {fmt(x.bb_mid[-1])} {'上' if up else '下'}方")


def r_di_cross(x, p, _):
    lb, adx_min = p.get("lookback", 3), p.get("adx_min", 0)
    diff = [None if a is None or b is None else a - b for a, b in zip(x.pdi, x.mdi)]
    if any(v is None for v in diff[-lb - 1:]) or x.adx[-1] is None:
        return Result(None, "DMI 資料不足")
    k = _last_flip(diff, lb)
    if k is None:
        return Result(0, f"近 {lb} 根 +DI / −DI 未交叉")
    if x.adx[-1] < adx_min:
        return Result(0, f"+DI / −DI {k} 根前交叉，但 ADX {x.adx[-1]:.1f} < {adx_min}")
    up = diff[-1] > 0
    return Result(1 if up else -1, f"{'+DI 上穿 −DI' if up else '−DI 上穿 +DI'}（{k} 根前），ADX {x.adx[-1]:.1f}")


def r_structure_break(x, p, _):
    lb, half = p.get("lookback", 3), max(1, p.get("depth", 10) // 2)
    c, n = x.c, x.n
    for k in range(lb):
        j = n - 1 - k
        seen = [pv for pv in x.zz if pv[0] + half <= j - 1]  # swing points confirmed before bar j
        hs = [pv for pv in seen if pv[2] == "H"]
        ls = [pv for pv in seen if pv[2] == "L"]
        if hs and c[j - 1] <= hs[-1][1] < c[j]:
            return Result(1, f"{k} 根前收盤突破前波高點 {fmt(hs[-1][1])}（{n - 1 - hs[-1][0]} 根前的轉折）")
        if ls and c[j - 1] >= ls[-1][1] > c[j]:
            return Result(-1, f"{k} 根前收盤跌破前波低點 {fmt(ls[-1][1])}（{n - 1 - ls[-1][0]} 根前的轉折）")
    if len(x.zz) < 2:
        return Result(None, "Zig Zag 轉折點不足")
    return Result(0, f"近 {lb} 根沒有突破前波高低點")


# ------------------------------------------------------------------ gates (Noul)

def g_trend_established(x, p, _):
    a = x.adx[-1]
    if a is None:
        return Result(None, "ADX 資料不足")
    thr, band = p.get("threshold", 25), p.get("soft_band", 5)
    n = clamp01((a - (thr - band)) / (2 * band))
    return Result(None, f"ADX {a:.1f} → 趨勢確立機率 {n:.2f}", noul=n, data={"adx": a})


def g_choppy(x, p, _):
    ci = x.ci[-1]
    if ci is None:
        return Result(None, "CI 資料不足")
    thr, band = p.get("threshold", 61.8), p.get("soft_band", 3)
    n = clamp01((ci - (thr - band)) / (2 * band))
    return Result(None, f"CI {ci:.1f} → 盤整機率 {n:.2f}", noul=n, data={"ci": ci})


RULES = {
    "mtp": r_mtp, "fractals": r_fractals, "alligator": r_alligator,
    "alligator_x_fractal": r_alligator_x_fractal, "psar": r_psar, "bollinger": r_bollinger,
    "keltner": r_keltner, "squeeze": r_squeeze, "ma_cross": r_ma_cross, "vol_stop": r_vol_stop,
    "ma_align": r_ma_align, "donchian": r_donchian, "agree": r_agree, "zigzag": r_zigzag,
    "supertrend": r_supertrend, "linreg": r_linreg, "sar_x_linreg": r_sar_x_linreg, "vwma": r_vwma,
    "macd": r_macd, "macd_cross": r_macd_cross, "supertrend_flip": r_supertrend_flip,
    "external_event": r_external_event,
    "squeeze_fire": r_squeeze_fire, "di_cross": r_di_cross, "structure_break": r_structure_break,
    "rsi_divergence": _div_rule("rsi", "RSI"), "uo_divergence": _div_rule("uo", "UO"),
    "obv_divergence": _div_rule("obv", "OBV"), "dmi": r_dmi, "cci": r_cci, "supertrend_x_macd": r_supertrend_x_macd, "hma": r_hma, "rsi": r_rsi, "stoch_rsi": r_stoch_rsi,
    "uo": r_uo, "atr": r_atr, "choppiness": r_choppiness, "hv": r_hv, "obv": r_obv, "mfi": r_mfi,
    "cmf": r_cmf, "aroon": r_aroon, "roc": r_roc,
    "trend_established": g_trend_established, "choppy": g_choppy,
}


def evaluate(ctx, nodes):
    """Run every enabled node in config order. Returns {node_id: Result}."""
    results = {}
    for node in nodes:
        if not node.get("enabled", True):
            continue
        rule = RULES.get(node["rule"])
        if rule is None:
            raise KeyError(f"node {node['id']}: unknown rule {node['rule']!r}")
        try:
            res = rule(ctx, node.get("params", {}), results)
        except (IndexError, ZeroDivisionError, TypeError) as e:
            res = Result(None, f"計算失敗：{type(e).__name__}")
        if res.score is not None:
            res.score = max(-2, min(2, res.score))
        results[node["id"]] = res
    return results
