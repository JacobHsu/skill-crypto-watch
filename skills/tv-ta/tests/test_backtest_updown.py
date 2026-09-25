"""Offline tests for scripts/backtest_updown.py (pure helpers, no network).

  python -m unittest discover -s tests
"""

import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import backtest_updown as bt  # noqa: E402


def sample(base=None, avg=None, p_up=None, up=True, h=8):
    return bt.Sample("2026-01-01", h, base, avg, p_up, up)


class BaseProbabilityTest(unittest.TestCase):
    def test_at_target_is_fifty(self):
        self.assertAlmostEqual(bt.base_probability(100.0, 100.0, 0.005, 24), 50.0)

    def test_above_target_favours_up_and_grows_as_time_runs_out(self):
        early = bt.base_probability(100.5, 100.0, 0.005, 20)
        late = bt.base_probability(100.5, 100.0, 0.005, 1)
        self.assertGreater(early, 50)
        self.assertGreater(late, early)

    def test_below_target_is_symmetric(self):
        up = bt.base_probability(101.0, 100.0, 0.004, 6)
        down = bt.base_probability(100.0 ** 2 / 101.0, 100.0, 0.004, 6)
        self.assertAlmostEqual(up + down, 100.0)


class TiltTest(unittest.TestCase):
    def test_scaled_and_capped(self):
        self.assertEqual(bt.tilt(0.5, 5.0, 10.0), 2.5)
        self.assertEqual(bt.tilt(9.0, 5.0, 10.0), 10.0)
        self.assertEqual(bt.tilt(-9.0, 5.0, 10.0), -10.0)

    def test_combined_is_clamped_to_1_and_99(self):
        self.assertEqual(bt.combine(99.5, 2.0, 5.0, 10.0), 99.0)
        self.assertEqual(bt.combine(0.2, -2.0, 5.0, 10.0), 1.0)


class HourlySigmaTest(unittest.TestCase):
    def test_constant_growth_has_zero_sigma(self):
        closes = [100.0 * 1.001 ** i for i in range(50)]
        self.assertAlmostEqual(bt.hourly_sigma(closes), 0.0, places=9)

    def test_alternating_returns(self):
        closes = [100.0, 101.0, 100.0, 101.0, 100.0]
        rets = [math.log(1.01), -math.log(1.01)] * 2
        expected = math.sqrt(sum(r * r for r in rets) / 3)
        self.assertAlmostEqual(bt.hourly_sigma(closes), expected)


class ScoreTest(unittest.TestCase):
    def test_hit_rate_counts_matching_direction(self):
        r = bt.hit_rate([(True, True), (False, False), (True, False), (False, True)])
        self.assertEqual((r["n"], r["hit"]), (4, 0.5))

    def test_empty_has_no_rate(self):
        self.assertIsNone(bt.hit_rate([])["hit"])

    def test_score_skips_ties_and_missing(self):
        rows = [sample(base=60, avg=1.0, p_up=62, up=True),
                sample(base=50, avg=0.0, p_up=50, up=False),   # all three are ties: no answer
                sample(base=40, avg=-1.0, p_up=41, up=True)]   # all three say Down, market went Up
        s = bt.score(rows)
        self.assertEqual((s["base"]["n"], s["base"]["hit"]), (2, 0.5))
        self.assertEqual((s["tech"]["n"], s["tech"]["hit"]), (2, 0.5))
        self.assertEqual((s["combined"]["n"], s["combined"]["hit"]), (2, 0.5))

    def test_report_splits_by_hours_left_and_halves(self):
        rows = [sample(base=60, avg=1, p_up=60, up=True, h=8), sample(base=60, avg=1, p_up=60, up=False, h=8),
                sample(base=40, avg=-1, p_up=40, up=False, h=2)]
        out = bt.report(rows, [8, 2])
        self.assertEqual(out[8]["questions"], 2)
        self.assertEqual(out[2]["questions"], 1)
        self.assertEqual(out[8]["first_half"]["combined"]["hit"], 1.0)
        self.assertEqual(out[8]["second_half"]["combined"]["hit"], 0.0)


if __name__ == "__main__":
    unittest.main()
