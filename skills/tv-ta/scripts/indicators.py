"""Technical indicators in pure Python, following TradingView built-in formulas.

Every function takes plain lists and returns lists aligned to the input bars,
with None where the indicator has not warmed up yet. Defaults match the
TradingView built-in studies used by the crypto-watch widgets.
"""

import math


# ---------------------------------------------------------------- primitives

def _valid(window):
    return all(v is not None for v in window)


def sma(src, n):
    out = [None] * len(src)
    for i in range(n - 1, len(src)):
        w = src[i - n + 1:i + 1]
        if _valid(w):
            out[i] = sum(w) / n
    return out


def _seeded(src, n, alpha):
    """Recursive average seeded with the SMA of the first n valid values (Pine ta.ema / ta.rma)."""
    out = [None] * len(src)
    prev = None
    run = []
    for i, v in enumerate(src):
        if v is None:
            continue
        if prev is None:
            run.append(v)
            if len(run) == n:
                prev = sum(run) / n
                out[i] = prev
            continue
        prev = alpha * v + (1 - alpha) * prev
        out[i] = prev
    return out


def ema(src, n):
    return _seeded(src, n, 2 / (n + 1))


def rma(src, n):
    return _seeded(src, n, 1 / n)


def wma(src, n):
    out = [None] * len(src)
    denom = n * (n + 1) / 2
    for i in range(n - 1, len(src)):
        w = src[i - n + 1:i + 1]
        if _valid(w):
            out[i] = sum(v * (k + 1) for k, v in enumerate(w)) / denom
    return out


def stdev(src, n):
    """Population standard deviation, as Pine ta.stdev."""
    out = [None] * len(src)
    for i in range(n - 1, len(src)):
        w = src[i - n + 1:i + 1]
        if _valid(w):
            m = sum(w) / n
            out[i] = math.sqrt(sum((v - m) ** 2 for v in w) / n)
    return out


def highest(src, n):
    out = [None] * len(src)
    for i in range(n - 1, len(src)):
        w = src[i - n + 1:i + 1]
        if _valid(w):
            out[i] = max(w)
    return out


def lowest(src, n):
    out = [None] * len(src)
    for i in range(n - 1, len(src)):
        w = src[i - n + 1:i + 1]
        if _valid(w):
            out[i] = min(w)
    return out


def change(src, n=1):
    return [None if i < n or src[i] is None or src[i - n] is None else src[i] - src[i - n]
            for i in range(len(src))]


def true_range(high, low, close):
    out = [high[0] - low[0]]
    for i in range(1, len(close)):
        out.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    return out


def hl2(high, low):
    return [(h + l) / 2 for h, l in zip(high, low)]


def hlc3(high, low, close):
    return [(h + l + c) / 3 for h, l, c in zip(high, low, close)]


# ---------------------------------------------------------------- indicators

def atr(high, low, close, n=14):
    return rma(true_range(high, low, close), n)


def rsi(close, n=14):
    d = change(close)
    up = rma([None if x is None else max(x, 0.0) for x in d], n)
    dn = rma([None if x is None else max(-x, 0.0) for x in d], n)
    out = []
    for u, v in zip(up, dn):
        if u is None or v is None:
            out.append(None)
        elif v == 0:
            out.append(100.0)
        elif u == 0:
            out.append(0.0)
        else:
            out.append(100 - 100 / (1 + u / v))
    return out


def macd(close, fast=12, slow=26, signal=9):
    f, s = ema(close, fast), ema(close, slow)
    line = [None if a is None or b is None else a - b for a, b in zip(f, s)]
    sig = ema(line, signal)
    hist = [None if a is None or b is None else a - b for a, b in zip(line, sig)]
    return line, sig, hist


def bollinger(close, n=20, mult=2.0):
    basis, dev = sma(close, n), stdev(close, n)
    upper = [None if b is None else b + mult * d for b, d in zip(basis, dev)]
    lower = [None if b is None else b - mult * d for b, d in zip(basis, dev)]
    width = [None if b is None or b == 0 else (u - l) / b for u, l, b in zip(upper, lower, basis)]
    return basis, upper, lower, width


def keltner(high, low, close, n=20, mult=2.0, atr_len=10):
    """TradingView Keltner Channels defaults: EMA 20, ATR 10, multiplier 2."""
    basis = ema(close, n)
    rng = atr(high, low, close, atr_len)
    upper = [None if b is None or r is None else b + mult * r for b, r in zip(basis, rng)]
    lower = [None if b is None or r is None else b - mult * r for b, r in zip(basis, rng)]
    return basis, upper, lower


def donchian(high, low, n=20):
    up, lo = highest(high, n), lowest(low, n)
    mid = [None if u is None else (u + l) / 2 for u, l in zip(up, lo)]
    return up, lo, mid


def dmi(high, low, close, di_len=14, adx_len=14):
    plus_dm, minus_dm = [None], [None]
    for i in range(1, len(close)):
        up, down = high[i] - high[i - 1], low[i - 1] - low[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    tr = rma(true_range(high, low, close), di_len)
    p, m = rma(plus_dm, di_len), rma(minus_dm, di_len)
    pdi = [None if a is None or t is None or t == 0 else 100 * a / t for a, t in zip(p, tr)]
    mdi = [None if a is None or t is None or t == 0 else 100 * a / t for a, t in zip(m, tr)]
    dx = []
    for a, b in zip(pdi, mdi):
        dx.append(None if a is None or b is None else (0.0 if a + b == 0 else 100 * abs(a - b) / (a + b)))
    return pdi, mdi, rma(dx, adx_len)


def aroon(high, low, n=14):
    up, down = [None] * len(high), [None] * len(high)
    for i in range(n, len(high)):
        wh, wl = high[i - n:i + 1], low[i - n:i + 1]
        # bars since the highest high / lowest low within the last n+1 bars
        since_h = n - max(range(n + 1), key=lambda k: (wh[k], k))
        since_l = n - max(range(n + 1), key=lambda k: (-wl[k], k))
        up[i] = 100 * (n - since_h) / n
        down[i] = 100 * (n - since_l) / n
    return up, down


def cci(high, low, close, n=20):
    tp = hlc3(high, low, close)
    ma = sma(tp, n)
    out = [None] * len(tp)
    for i in range(n - 1, len(tp)):
        md = sum(abs(v - ma[i]) for v in tp[i - n + 1:i + 1]) / n
        out[i] = 0.0 if md == 0 else (tp[i] - ma[i]) / (0.015 * md)
    return out


def ultimate_oscillator(high, low, close, f=7, m=14, s=28):
    bp, tr = [None], [None]
    for i in range(1, len(close)):
        lo = min(low[i], close[i - 1])
        bp.append(close[i] - lo)
        tr.append(max(high[i], close[i - 1]) - lo)
    out = [None] * len(close)
    for i in range(s, len(close)):
        def avg(n):
            t = sum(tr[i - n + 1:i + 1])
            return 0.0 if t == 0 else sum(bp[i - n + 1:i + 1]) / t
        out[i] = 100 * (4 * avg(f) + 2 * avg(m) + avg(s)) / 7
    return out


def stoch_rsi(close, k=3, d=3, rsi_len=14, stoch_len=14):
    r = rsi(close, rsi_len)
    hi, lo = highest(r, stoch_len), lowest(r, stoch_len)
    raw = [None if h is None or v is None else (0.0 if h == l else 100 * (v - l) / (h - l))
           for v, h, l in zip(r, hi, lo)]
    kk = sma(raw, k)
    return kk, sma(kk, d)


def roc(close, n=9):
    return [None if i < n or close[i - n] == 0 else 100 * (close[i] - close[i - n]) / close[i - n]
            for i in range(len(close))]


def hma(close, n=9):
    half, full = wma(close, n // 2), wma(close, n)
    diff = [None if a is None or b is None else 2 * a - b for a, b in zip(half, full)]
    return wma(diff, int(math.floor(math.sqrt(n))))


def vwma(close, volume, n=20):
    out = [None] * len(close)
    for i in range(n - 1, len(close)):
        v = sum(volume[i - n + 1:i + 1])
        if v:
            out[i] = sum(c * q for c, q in zip(close[i - n + 1:i + 1], volume[i - n + 1:i + 1])) / v
    return out


def obv(close, volume):
    out = [0.0]
    for i in range(1, len(close)):
        sign = (close[i] > close[i - 1]) - (close[i] < close[i - 1])
        out.append(out[-1] + sign * volume[i])
    return out


def mfi(high, low, close, volume, n=14):
    tp = hlc3(high, low, close)
    out = [None] * len(tp)
    for i in range(n, len(tp)):
        pos = neg = 0.0
        for j in range(i - n + 1, i + 1):
            flow = tp[j] * volume[j]
            if tp[j] > tp[j - 1]:
                pos += flow
            elif tp[j] < tp[j - 1]:
                neg += flow
        out[i] = 100.0 if neg == 0 else 100 - 100 / (1 + pos / neg)
    return out


def cmf(high, low, close, volume, n=20):
    mfv = []
    for h, l, c, v in zip(high, low, close, volume):
        mfv.append(0.0 if h == l else ((c - l) - (h - c)) / (h - l) * v)
    out = [None] * len(close)
    for i in range(n - 1, len(close)):
        v = sum(volume[i - n + 1:i + 1])
        out[i] = 0.0 if v == 0 else sum(mfv[i - n + 1:i + 1]) / v
    return out


def choppiness(high, low, close, n=14):
    tr = true_range(high, low, close)
    out = [None] * len(close)
    for i in range(n - 1, len(close)):
        rng = max(high[i - n + 1:i + 1]) - min(low[i - n + 1:i + 1])
        if rng > 0:
            out[i] = 100 * math.log10(sum(tr[i - n + 1:i + 1]) / rng) / math.log10(n)
    return out


def historical_volatility(close, n=10, annual=365, per=1):
    lr = [None] + [math.log(close[i] / close[i - 1]) for i in range(1, len(close))]
    sd = stdev(lr, n)
    return [None if v is None else 100 * v * math.sqrt(annual / per) for v in sd]


def supertrend(high, low, close, atr_len=10, factor=3.0):
    """Returns (line, direction) where direction is +1 for uptrend, -1 for downtrend."""
    a = atr(high, low, close, atr_len)
    mid = hl2(high, low)
    line, direction = [None] * len(close), [None] * len(close)
    up_prev = dn_prev = None
    dir_prev = 1
    for i in range(len(close)):
        if a[i] is None:
            continue
        up = mid[i] - factor * a[i]
        dn = mid[i] + factor * a[i]
        if up_prev is not None and close[i - 1] > up_prev:
            up = max(up, up_prev)
        if dn_prev is not None and close[i - 1] < dn_prev:
            dn = min(dn, dn_prev)
        if up_prev is None:
            d = 1
        elif dir_prev == -1 and close[i] > dn:
            d = 1
        elif dir_prev == 1 and close[i] < up:
            d = -1
        else:
            d = dir_prev
        line[i] = up if d == 1 else dn
        direction[i] = d
        up_prev, dn_prev, dir_prev = up, dn, d
    return line, direction


def psar(high, low, close, start=0.02, inc=0.02, maximum=0.2):
    """Parabolic SAR, a port of Pine's ta.sar. Returns (sar, direction), direction +1 when SAR is below price."""
    n = len(high)
    sar, direction = [None] * n, [None] * n
    if n < 3:
        return sar, direction
    below = close[1] > close[0]
    ep = high[1] if below else low[1]
    s = low[0] if below else high[0]
    af = start
    for i in range(1, n):
        first = i == 1
        s = s + af * (ep - s)
        if below and s > low[i]:
            first, below, s, ep, af = True, False, max(high[i], ep), low[i], start
        elif not below and s < high[i]:
            first, below, s, ep, af = True, True, min(low[i], ep), high[i], start
        if not first:
            if below and high[i] > ep:
                ep, af = high[i], min(af + inc, maximum)
            elif not below and low[i] < ep:
                ep, af = low[i], min(af + inc, maximum)
        if below:
            s = min(s, low[i - 1], low[i - 2]) if i > 1 else min(s, low[i - 1])
        else:
            s = max(s, high[i - 1], high[i - 2]) if i > 1 else max(s, high[i - 1])
        sar[i] = s
        direction[i] = 1 if below else -1
    return sar, direction


def alligator(high, low, jaw=(13, 8), teeth=(8, 5), lips=(5, 3)):
    """Williams Alligator on hl2 with SMMA (= RMA). Each line is shifted forward by its offset,
    so the value shown at bar i is the SMMA computed at bar i - offset."""
    src = hl2(high, low)

    def shifted(n, off):
        s = rma(src, n)
        return [None] * off + s[:len(s) - off]

    return shifted(*jaw), shifted(*teeth), shifted(*lips)


def fractals(high, low, n=2, max_equal=4):
    """Williams Fractals, as TradingView draws them: n strictly lower highs on the right; on the
    left, up to `max_equal` bars equal to the center high may be skipped before n strictly lower
    highs (mirror for lows). Returns lists of (bar_index, price); a fractal is confirmed n bars later."""

    def is_pivot(src, i, better):
        if any(not better(src[i], src[i + k]) for k in range(1, n + 1)):
            return False
        j, skipped = i - 1, 0
        while j >= 0 and src[j] == src[i] and skipped < max_equal:
            j, skipped = j - 1, skipped + 1
        return j - n + 1 >= 0 and all(better(src[i], src[j - k]) for k in range(n))

    ups, downs = [], []
    for i in range(n, len(high) - n):
        if is_pivot(high, i, lambda a, b: a > b):
            ups.append((i, high[i]))
        if is_pivot(low, i, lambda a, b: a < b):
            downs.append((i, low[i]))
    return ups, downs


def zigzag(high, low, deviation=5.0, depth=10):
    """Swing pivots: local extremes over `depth` bars (depth//2 each side), alternating high/low,
    keeping only swings that move at least `deviation` percent. Returns [(index, price, 'H'|'L')]."""
    half = max(1, depth // 2)
    cands = []
    for i in range(half, len(high) - half):
        if high[i] == max(high[i - half:i + half + 1]):
            cands.append((i, high[i], "H"))
        if low[i] == min(low[i - half:i + half + 1]):
            cands.append((i, low[i], "L"))
    pivots = []
    for p in cands:
        if not pivots:
            pivots.append(p)
            continue
        last = pivots[-1]
        if p[2] == last[2]:
            if (p[2] == "H" and p[1] > last[1]) or (p[2] == "L" and p[1] < last[1]):
                pivots[-1] = p
            continue
        if abs(p[1] - last[1]) / last[1] * 100 >= deviation:
            pivots.append(p)
    return pivots


def volatility_stop(close, high, low, n=20, mult=2.0):
    """TradingView Volatility Stop (close source). Returns (stop, uptrend_flags)."""
    a = atr(high, low, close, n)
    tr = true_range(high, low, close)
    stop, trend = [None] * len(close), [None] * len(close)
    mx = mn = close[0]
    st = None
    up = True
    for i, c in enumerate(close):
        m = (a[i] if a[i] is not None else tr[i]) * mult
        mx, mn = max(mx, c), min(mn, c)
        st = c if st is None else (max(st, mx - m) if up else min(st, mn + m))
        new_up = c - st >= 0
        if new_up != up:
            mx = mn = c
            st = mx - m if new_up else mn + m
        up = new_up
        stop[i], trend[i] = st, up
    return stop, trend


def linreg(close, n=100):
    """Least-squares line over the last n bars. Returns (value at last bar, slope per bar, residual stdev)."""
    if len(close) < n:
        return None, None, None
    y = close[-n:]
    xs = range(n)
    mx, my = (n - 1) / 2, sum(y) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (v - my) for x, v in zip(xs, y)) / sxx
    icpt = my - slope * mx
    resid = [v - (icpt + slope * x) for x, v in zip(xs, y)]
    dev = math.sqrt(sum(r * r for r in resid) / n)
    return icpt + slope * (n - 1), slope, dev
