"""Puts the shipped skill's scripts on sys.path so the maintainer tools can reuse fetch_ohlcv, run,
replay, updown and friends without copying them. Import it before any of those modules."""

import os
import sys

BACKTEST = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BACKTEST)
SKILL_SCRIPTS = os.path.join(REPO, "skills", "tv-ta", "scripts")

for path in (BACKTEST, SKILL_SCRIPTS):
    if path not in sys.path:
        sys.path.insert(0, path)
