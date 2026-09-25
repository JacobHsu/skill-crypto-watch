"""Optional: compare tv-ta's own indicator values with TradingView's (via tvscreener).

  pip install tvscreener        # or: uv run --with tvscreener python scripts/crosscheck.py BTC --tf 4h
  python scripts/crosscheck.py BTC --tf 4h

Uses the live (still-forming) candle on both sides, because TradingView's screener does.
Small drifts are expected when a candle ticks between the two requests.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_ohlcv  # noqa: E402
import nodes as nodes_mod  # noqa: E402

TV_INTERVAL = {"15m": "15", "1h": "60", "4h": "240", "1d": "1D"}

# tvscreener api field -> function(ctx) giving our value on the last bar
PAIRS = {
    "close": lambda x: x.c[-1],
    "RSI": lambda x: x.rsi[-1],
    "MACD.macd": lambda x: x.macd[-1],
    "MACD.signal": lambda x: x.macd_sig[-1],
    "BB.upper": lambda x: x.bb_up[-1],
    "BB.lower": lambda x: x.bb_lo[-1],
    "KltChnl.upper": lambda x: x.kc_up[-1],
    "KltChnl.lower": lambda x: x.kc_lo[-1],
    "DonchCh20.Upper": lambda x: x.dc_up[-1],
    "DonchCh20.Lower": lambda x: x.dc_lo[-1],
    "ATR": lambda x: x.atr[-1],
    "ADX": lambda x: x.adx[-1],
    "ADX+DI": lambda x: x.pdi[-1],
    "ADX-DI": lambda x: x.mdi[-1],
    "SMA20": lambda x: x.sma_f[-1],
    "SMA50": lambda x: x.sma_s[-1],
    "EMA20": lambda x: x.ema_f[-1],
    "EMA50": lambda x: x.ema_s[-1],
    "HullMA9": lambda x: x.hma[-1],
    "VWMA": lambda x: x.vwma[-1],
    "P.SAR": lambda x: x.sar[-1],
    "Stoch.RSI.K": lambda x: x.srsi_k[-1],
    "CCI20": lambda x: x.cci[-1],
    "UO": lambda x: x.uo[-1],
    "MoneyFlow": lambda x: x.mfi[-1],
    "ChaikinMoneyFlow": lambda x: x.cmf[-1],
    "Aroon.Up": lambda x: x.aroon_up[-1],
    "Aroon.Down": lambda x: x.aroon_dn[-1],
    "ROC": lambda x: x.roc[-1],
}

OSCILLATORS = {"RSI", "ADX", "ADX+DI", "ADX-DI", "Stoch.RSI.K", "CCI20", "UO", "MoneyFlow",
               "ChaikinMoneyFlow", "Aroon.Up", "Aroon.Down", "ROC"}


def tv_values(pair, tf):
    try:
        from tvscreener import CryptoField, CryptoScreener
    except ModuleNotFoundError:
        sys.exit("crosscheck needs tvscreener: pip install tvscreener")
    # the enum already holds per-interval variants ("RSI|240"); with_interval() breaks select()
    by_api = {f.field_name: f for f in CryptoField}
    suffix = "" if TV_INTERVAL[tf] == "1D" else "|" + TV_INTERVAL[tf]
    fields = [by_api[k + suffix] for k in PAIRS if k + suffix in by_api]
    cs = CryptoScreener()
    cs.symbols = {"query": {"types": []}, "tickers": [f"BINANCE:{pair}"]}
    cs.select(*fields)
    df = cs.get()
    if df.empty:
        sys.exit(f"tvscreener returned nothing for BINANCE:{pair}")
    row = df.iloc[0]
    out = {}
    for f in fields:
        label = f.label
        for col in (label, f.field_name):
            if col in row.index:
                out[f.field_name.split("|")[0]] = row[col]
                break
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="BTC")
    ap.add_argument("--tf", default="4h", choices=list(TV_INTERVAL))
    ap.add_argument("--tolerance", type=float, default=1.0,
                    help="mismatch threshold: percent for prices, points for oscillators")
    args = ap.parse_args(argv)

    bars = fetch_ohlcv.fetch(args.symbol, args.tf, 500, live=True)
    ctx = nodes_mod.Context(bars)
    tv = tv_values(bars["pair"], args.tf)

    sys.stdout.reconfigure(encoding="utf-8")
    print(f"{bars['pair']} {args.tf}  (tolerance {args.tolerance}%)\n")
    print(f"{'field':<18}{'tv-ta':>16}{'TradingView':>16}{'diff':>10}")
    bad = 0
    for key, fn in PAIRS.items():
        ours, theirs = fn(ctx), tv.get(key)
        if ours is None or theirs is None or theirs != theirs:  # NaN check
            print(f"{key:<18}{'n/a' if ours is None else f'{ours:.4f}':>16}{'n/a':>16}")
            continue
        theirs = float(theirs)
        if key in OSCILLATORS:
            diff, unit = abs(ours - theirs), "pt"  # bounded scales: absolute points, not relative %
        else:
            diff, unit = abs(ours - theirs) / max(abs(theirs), 1e-9) * 100, "%"
        flag = "  <-- check" if diff > args.tolerance else ""
        bad += bool(flag)
        print(f"{key:<18}{ours:>16.4f}{theirs:>16.4f}{diff:>8.2f}{unit:<2}{flag}")
    print(f"\n{bad} field(s) outside tolerance")
    tv_close = tv.get("close")
    if bad and tv_close and abs(ctx.c[-1] - float(tv_close)) / float(tv_close) > 0.0001:
        print("note: the live close moved between the two requests; CCI, MACD and other fast\n"
              "      readings near zero drift with it. Re-run away from a candle boundary to confirm.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
