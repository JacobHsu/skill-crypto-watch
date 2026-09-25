"""Fetch OHLCV candles from Binance public klines (no API key).

The crypto-watch TradingView widgets chart BINANCE:{SYM}USDT, so Binance
klines give the same bars the charts are drawn from.
"""

import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HOSTS = [
    "https://api.binance.com",
    "https://data-api.binance.vision",  # public mirror, used when the main host is blocked
]

INTERVALS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800, "1M": 2592000}

CACHE_DIR = os.path.join(tempfile.gettempdir(), "tv-ta-cache")


class FetchError(RuntimeError):
    pass


def to_pair(symbol):
    """BTC -> BTCUSDT; already-paired symbols pass through."""
    s = symbol.upper().replace("BINANCE:", "").replace("/", "")
    return s if s.endswith(("USDT", "USDC", "FDUSD")) else s + "USDT"


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "tv-ta/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch(symbol, interval, limit=500, live=False, cache=False, as_of=None):
    """Return candles as a dict of lists: time, open, high, low, close, volume.

    live=False drops the still-forming last candle so every signal is based on
    confirmed closes. live=True keeps it, matching what the chart shows right now.
    as_of (epoch ms) replays the past: only candles already closed at that moment are
    returned, whatever `live` says, because a candle that was forming then is complete
    in today's data and would leak the future.
    """
    if interval not in INTERVALS:
        raise FetchError(f"unsupported interval {interval!r}; use one of {', '.join(INTERVALS)}")
    pair = to_pair(symbol)
    tag = f"_asof{as_of}" if as_of else ""
    cache_path = os.path.join(CACHE_DIR, f"{pair}_{interval}_{limit}{tag}.json")

    raw = None
    if cache and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            raw = json.load(f)
    if raw is None:
        errors = []
        for host in HOSTS:
            url = f"{host}/api/v3/klines?symbol={pair}&interval={interval}&limit={limit + 1}"
            if as_of:
                url += f"&endTime={as_of - 1}"
            try:
                raw = _get(url)
                break
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    raise FetchError(f"Binance does not list {pair}") from e
                errors.append(f"{host}: HTTP {e.code}")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                errors.append(f"{host}: {e}")
        if raw is None:
            raise FetchError("all Binance hosts failed: " + "; ".join(errors))
        if cache:
            os.makedirs(CACHE_DIR, exist_ok=True)
            # write then rename: parallel fetches of one interval (live and closed) share this file
            tmp = f"{cache_path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(raw, f)
            os.replace(tmp, cache_path)

    if as_of:
        raw = [k for k in raw if k[6] < as_of]  # closed before the replay moment
    elif not live and raw and raw[-1][6] > time.time() * 1000:
        raw = raw[:-1]  # close time in the future = candle still forming
    raw = raw[-limit:]
    return {
        "pair": pair,
        "interval": interval,
        "time": [int(k[0]) for k in raw],
        "close_time": [int(k[6]) for k in raw],
        "open": [float(k[1]) for k in raw],
        "high": [float(k[2]) for k in raw],
        "low": [float(k[3]) for k in raw],
        "close": [float(k[4]) for k in raw],
        "volume": [float(k[5]) for k in raw],
    }


def fetch_many(symbol, requests, cache=False, as_of=None):
    """Fetch several (interval, limit, live) sets at once. Returns {request: candles}.

    Each fetch is mostly waiting on the network, so threads overlap them.
    Raises the first FetchError, like a single fetch would.
    """
    requests = list(dict.fromkeys(requests))
    if not requests:
        return {}
    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        futures = {r: pool.submit(fetch, symbol, r[0], r[1], r[2], cache, as_of) for r in requests}
    return {r: f.result() for r, f in futures.items()}
