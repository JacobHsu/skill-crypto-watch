---
name: tv-ta
description: TradingView technical analyst for crypto. Runs the 28-indicator crypto-watch multi-timeframe checklist (check.html) on computed indicator values instead of chart screenshots, combines typed node judgments with tunable weights and gates (TypeSafe Jev composite-scoring style), and returns BUY / WAIT / SELL with per-item numeric evidence plus entry, stop and targets. Use when the user asks for technical analysis, a checklist verdict or a trade plan on a crypto symbol (BTC, ETH, SOL, XRP...) at 15m / 1h / 4h / 1d — e.g. "分析 BTC 4h", "跑一次檢核表", "ETH 現在能進場嗎", "SOL 技術面" — for daily "Up or Down" prediction-market questions (Polymarket, Binance Wallet) such as "用 tv-ta 回答 BTC 明日漲跌題" or "BTC 今天會漲還是跌", or when the user wants to tune checklist nodes, weights or thresholds.
---

# tv-ta — TradingView technical analyst

This skill reproduces the crypto-watch checklist (`check.html`, 28 indicators) from numbers.
It downloads Binance klines (the same `BINANCE:{SYM}USDT` bars the TradingView widgets
chart), computes each indicator with TradingView's built-in formulas and defaults, runs one
decision node per checklist item, and combines the answers into a verdict and a trade plan.
The scripts do all the judging; your job is to run them, present the report, and explain it.

## Requirements

- Python 3.11 or newer, standard library only (on Windows the launcher may be `py`).
- Network access to `api.binance.com` or `data-api.binance.vision`. No API key.
- Optional: `tvscreener` for `scripts/crosscheck.py`; a `TYPESAFE_API_KEY` for Jev nodes.

All paths below are relative to this skill's directory. Run commands from there, or prefix
the script path with the skill directory.

## Run an analysis

1. Work out the symbol and timeframe from the request. Defaults: `BTC`, `4h`.
   Timeframes: `15m`, `1h`, `4h`, `1d`. Symbols are Binance USDT pairs (`SOL` means `SOLUSDT`).
2. Run:

   ```
   python scripts/run.py <SYMBOL> --tf <TF>
   ```

   Useful flags: `--no-context` (skip the 1h/4h/1d comparison), `--live` (include the
   still-forming candle, which matches what the chart shows right now), `--json`
   (machine-readable), `--log` (append the node scores to `~/.tv-ta/logs/` for later
   calibration), `--as-of 2026-09-22T16:00` (replay a past moment using only candles that
   had closed by then, in UTC; use it to check a call against what actually happened).
3. Show the markdown report as printed: the Section A and B tables, GATES, FINAL,
   the multi-timeframe table and the trade plan. Do not recompute, re-score or override
   any item, and do not change the verdict.
4. After the report, add a short reading of at most five bullet points:
   - which groups drive the verdict (trend, momentum, volume...) and which disagree;
   - whether the other timeframes agree with the requested one;
   - any gate that is reducing trend weights (choppy market, weak ADX);
   - when checklist tally and weighted composite disagree, say that the weights or gates
     caused it and name the heaviest rows;
   - for WAIT, restate the two trigger levels.
5. Keep the closing disclaimer. This is rule-based technical analysis, not investment advice.

If the script exits with an error, report the message and stop. Exit code 3 means market
data was unavailable (network, or Binance does not list the pair). Never fill in indicator
values or verdicts from memory or general market knowledge. An item marked `未核對`
(unchecked) had insufficient data. It is excluded from the denominator and is not a WAIT.

## Answer a daily Up or Down question

For prediction-market questions of the form "will {SYM} be up or down" over a day
(Polymarket or Binance Wallet "Up or Down on <date>"; "明日漲跌", "今天會漲嗎"), run:

```
python scripts/updown.py <SYMBOL>
```

The script picks the next question to settle, so you do not need a date from the user.
These markets compare the Binance 1-minute close at 12:00 ET on the settle day with
12:00 ET the day before. The script fetches the target price and runs the checklist on
the timeframes in `profile.toml [updown]`. It returns P(Up) as a volatility-based base
chance (from the current distance to the target and the time left) plus a technical tilt.
For a question that has already settled, pass `--as-of <start time in UTC>` and then
state the actual result next to the answer.

Show the printed tables, including the question page link and the start and end times in the
user's local time exactly as printed. The user opens that link to check the question, so name the
question by its Binance title and local end time, not by US Eastern time. Ask the user to compare
the printed 目標價 with the one the page labels 需超越的價格.
Also show the "TradingView widget 逐項檢核" matrix: every widget indicator from the crypto-watch
main page and o/ page, one row each, with its signal on 1H, 4H and 1D. Summarise which widgets
agree across timeframes and which conflict, using the evidence tables. `--brief` drops the evidence. Then give the answer in one sentence: the lean (Up or Down),
P(Up), and the main reason. Name the timeframe driving the tilt, or say that the price
distance dominates when little time is left. Say that the tilt scale is not calibrated
yet, so the number is a lean and not a fair price. Do not advise a bet size.

## Tune the decision model

Read `references/node_design.md` before changing anything. The skill's own `config/`
holds the shipped defaults:

- `config/nodes.toml` has one node per checklist item, with its rule, `params`
  (thresholds), `weight` and `group`. Setting a weight to 0 keeps the row but removes its vote.
- `config/profile.toml` holds the verdict thresholds, gates (Noul nodes that scale whole
  groups), trade-plan parameters, indicator lengths and the optional Jev settings.
- `config/symbols/<sym>.toml` holds per-symbol overrides. BTC and ETH ship with values
  calibrated from a backtest.

**Put a user's own tuning in `~/.tv-ta/config/`** (or `$TV_TA_CONFIG`), not in the
skill folder. A plugin update replaces the skill folder, but the user folder survives.
Files there are patches layered over the defaults, and each one is optional:
`profile.toml` (any profile values), `nodes.toml` (`[nodes.<id>]` tables for all
symbols) and `symbols/<sym>.toml` (per-symbol `[nodes.<id>]` tables). The report's `設定:`
line lists every layer that was applied. Edit the skill's own `config/` only when you are
working in the skill's source repository and the user asks to change the shipped defaults.

To try a change, edit the file and rerun with `--cache`, which reuses the downloaded
candles so only the config differs. Then report the composite and the verdict before and
after. Make one change at a time. `--no-user-config` runs the shipped defaults, for
comparison. To check the formulas against TradingView, run
`python scripts/crosscheck.py <SYMBOL> --tf <TF>` (needs `tvscreener`).

`references/checklist.md` maps every node to its check.html rule and to the numeric
definition it uses. Use it when the user asks why an item got its answer.
