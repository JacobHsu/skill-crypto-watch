"""Offline tests for the forward test: the frozen model, settling, the scoreboard and idempotent logging.

  python -m unittest discover -s tests
"""

import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import forward_log  # noqa: E402
import forward_model  # noqa: E402
import updown  # noqa: E402

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 22, 16, 5, tzinfo=UTC)       # just after a question's start (US daylight time)
EVAL = {"orig_avg": 0.3, "orig_pred": "Up", "tf_composites": {"1h": 0.3}, "learned_score": -1.5,
        "learned_pred": "Down", "contributions": {"hma": -2.0, "kc": 0.5}}


def row(symbol="BTC", result="Up", orig="Up", learned="Down", late="0", day="2026-09-23"):
    return {**dict.fromkeys(forward_log.FIELDS, ""), "symbol": symbol, "question_date": day, "result": result,
            "orig_pred": orig, "learned_pred": learned, "late": late,
            "start_utc": "2026-09-22T16:00Z", "settle_utc": "2026-09-23T16:00Z"}


class ModelFile(unittest.TestCase):
    def test_shipped_model_loads_and_covers_the_candidate_nodes(self):
        model = forward_model.load_model()
        self.assertEqual(len(model["features_order"]), len(model["coef"]))
        self.assertTrue(any(c < 0 for c in model["coef"]) and any(c > 0 for c in model["coef"]))

    def test_edited_coefficients_are_refused(self):
        model = forward_model.load_model()
        model["coef"][0] += 1
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(model, f)
            with self.assertRaises(forward_model.ModelError):
                forward_model.load_model(path)

    def test_missing_file_is_a_model_error(self):
        with self.assertRaises(forward_model.ModelError):
            forward_model.load_model(os.path.join(HERE, "nope.json"))


class Scoring(unittest.TestCase):
    def test_sign_call_leaves_a_tie_unanswered(self):
        self.assertEqual([forward_model.sign_call(x) for x in (1.2, -0.1, 0, None)], ["Up", "Down", "", ""])

    def test_features_average_only_timeframes_with_a_score(self):
        feats = forward_model.mean_features([{"a": 1.0, "b": 2.0}, {"a": 3.0}], ["a", "b", "c"])
        self.assertEqual(feats, {"a": 2.0, "b": 2.0, "c": 0.0})

    def test_learned_score_is_the_signed_weighted_sum(self):
        model = {"features_order": ["a", "b"], "coef": [-2.0, 1.0]}
        score, parts = forward_model.learned_score({"a": 1.5, "b": 1.0}, model)
        self.assertEqual((score, parts), (-2.0, {"a": -3.0, "b": 1.0}))


class Scoreboard(unittest.TestCase):
    def test_hit_rates_and_late_and_unsettled_rows_are_left_out(self):
        rows = [row(result="Up", orig="Up", learned="Up"), row(result="Down", orig="Up", learned="Down"),
                row(result="Down", orig="Up", learned="Down", late="1"), row(result="", orig="Up", learned="Up"),
                row(result="Up", orig="", learned="Up")]
        g = forward_log.score_group(rows)
        self.assertEqual(g["n"], 2)
        self.assertEqual((g["hit"]["orig"]["hit"], g["hit"]["learned"]["hit"], g["hit"]["up"]["hit"]), (0.5, 1.0, 0.5))
        self.assertAlmostEqual(g["learned_vs_orig"]["diff"], 0.5)

    def test_paired_difference_needs_two_questions(self):
        self.assertIsNone(forward_log.diff_stats([True], [False])["diff"])

    def test_empty_scoreboard_renders(self):
        board = forward_log.scoreboard([], forward_model.load_model())
        self.assertEqual(forward_log.render(board), "還沒有任何紀錄。")


class Logging(unittest.TestCase):
    def setUp(self):
        self.model = forward_model.load_model()

    def test_predict_logs_once_per_symbol_and_day(self):
        with mock.patch.object(forward_model, "evaluate_question", return_value=EVAL) as ev:
            rows, added, failed = forward_log.predict(["BTC", "ETH"], NOW, self.model, [])
            again, added2, _ = forward_log.predict(["BTC", "ETH"], NOW, self.model, rows)
        self.assertEqual((added, failed, added2, len(again)), (["BTC", "ETH"], [], [], 2))
        self.assertEqual(ev.call_count, 2)
        self.assertEqual((rows[0]["question_date"], rows[0]["late"], rows[0]["learned_pred"]), ("2026-09-23", "0", "Down"))
        # the model sees the question's start, not the moment the job ran
        self.assertEqual(ev.call_args[0][1], int(dt.datetime(2026, 9, 22, 16, tzinfo=UTC).timestamp() * 1000))

    def test_a_row_logged_hours_late_is_marked_late(self):
        with mock.patch.object(forward_model, "evaluate_question", return_value=EVAL):
            rows, _, _ = forward_log.predict(["BTC"], NOW + dt.timedelta(hours=5), self.model, [])
        self.assertEqual(rows[0]["late"], "1")

    def test_one_failing_symbol_does_not_stop_the_others(self):
        def flaky(sym, at, model):
            if sym == "ETH":
                raise forward_log.fetch_ohlcv.FetchError("no data")
            return EVAL
        with mock.patch.object(forward_model, "evaluate_question", side_effect=flaky):
            rows, added, failed = forward_log.predict(["BTC", "ETH"], NOW, self.model, [])
        self.assertEqual((added, len(failed), len(rows)), (["BTC"], 1, 1))

    def test_settle_marks_up_only_when_the_close_is_above_the_target(self):
        closes = {"2026-09-22T16:00Z": 100.0, "2026-09-23T16:00Z": 100.0}
        fake = lambda pair, at: closes[at.strftime(forward_log.TIME_FMT)]  # noqa: E731
        later = dt.datetime(2026, 9, 23, 16, 30, tzinfo=UTC)
        with mock.patch.object(updown, "minute_close", side_effect=fake):
            rows, done, failed = forward_log.settle([row(result="")], later)
        self.assertEqual((rows[0]["result"], done, failed), ("Down", 1, []))    # equal counts as Down
        closes["2026-09-23T16:00Z"] = 100.5
        with mock.patch.object(updown, "minute_close", side_effect=fake):
            rows, _, _ = forward_log.settle([row(result="")], later)
        self.assertEqual(rows[0]["result"], "Up")

    def test_a_question_that_has_not_ended_is_not_settled(self):
        with mock.patch.object(updown, "minute_close", side_effect=AssertionError("must not fetch")):
            rows, done, _ = forward_log.settle([row(result="")], dt.datetime(2026, 9, 23, 16, 5, tzinfo=UTC))
        self.assertEqual((rows[0]["result"], done), ("", 0))

    def test_a_settled_row_is_never_changed(self):
        with mock.patch.object(updown, "minute_close", side_effect=AssertionError("must not fetch")):
            rows, done, _ = forward_log.settle([row(result="Up")], dt.datetime(2026, 12, 1, tzinfo=UTC))
        self.assertEqual((rows[0]["result"], done), ("Up", 0))

    def test_csv_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "forward.csv")
            forward_log.write_rows(path, [row(), row(symbol="ETH")])
            self.assertEqual([r["symbol"] for r in forward_log.read_rows(path)], ["BTC", "ETH"])


if __name__ == "__main__":
    unittest.main()
