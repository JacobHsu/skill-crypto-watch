"""Offline tests for the Up/Down dataset builder and heatmap (no network).

  python -m unittest discover -s tests
"""

import datetime as dt
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import updown_dataset as ds  # noqa: E402
import updown_heatmap as hm  # noqa: E402


def row(day, target, settle, symbol="BTC"):
    (w,) = ds.windows(dt.date.fromisoformat(day), dt.date.fromisoformat(day))
    return ds.make_row(symbol, w, target, settle)


class WindowTest(unittest.TestCase):
    def test_summer_window_is_taiwan_midnight_to_midnight(self):
        (w,) = ds.windows(dt.date(2026, 9, 25), dt.date(2026, 9, 25))
        r = ds.make_row("BTC", w, 84275.4, 84727.3)
        self.assertEqual((r["start_utc"], r["settle_utc"]), ("2026-09-24T16:00Z", "2026-09-25T16:00Z"))
        self.assertEqual((r["start_tw"], r["settle_tw"]), ("2026-09-25 00:00", "2026-09-26 00:00"))

    def test_winter_window_is_one_am_in_taiwan(self):
        r = row("2026-12-10", 100.0, 101.0)
        self.assertEqual((r["start_utc"], r["settle_utc"]), ("2026-12-09T17:00Z", "2026-12-10T17:00Z"))
        self.assertEqual(r["settle_tw"], "2026-12-11 01:00")

    def test_dst_change_days_are_23_and_25_hours(self):
        spring, fall = ds.windows(dt.date(2026, 3, 8), dt.date(2026, 3, 8))[0], ds.windows(dt.date(2026, 11, 1), dt.date(2026, 11, 1))[0]
        self.assertEqual((spring.settle - spring.start).total_seconds() / 3600, 23)
        self.assertEqual((fall.settle - fall.start).total_seconds() / 3600, 25)

    def test_consecutive_days_share_the_boundary(self):
        a, b = ds.windows(dt.date(2026, 5, 1), dt.date(2026, 5, 2))
        self.assertEqual(a.settle, b.start)


class RowTest(unittest.TestCase):
    def test_up_down_and_tie(self):
        self.assertEqual(row("2026-05-01", 100.0, 101.0)["result"], "Up")
        self.assertEqual(row("2026-05-01", 100.0, 99.0)["result"], "Down")
        self.assertEqual(row("2026-05-01", 100.0, 100.0)["result"], "Down")

    def test_return_pct(self):
        self.assertEqual(row("2026-05-01", 200.0, 210.0)["return_pct"], "5.0000")


class CsvTest(unittest.TestCase):
    def test_round_trip_and_merge_keeps_order_and_prefers_new(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "btc.csv")
            ds.write_rows(path, [row("2026-05-02", 100, 101), row("2026-05-01", 100, 99)][::-1])
            old = ds.read_rows(path)
            merged = ds.merge_rows(old, [row("2026-05-02", 100, 90), row("2026-05-03", 100, 105)])
            self.assertEqual([r["question_date"] for r in merged], ["2026-05-01", "2026-05-02", "2026-05-03"])
            self.assertEqual(merged[1]["result"], "Down")  # replaced by the newer row

    def test_missing_windows_skips_days_already_present(self):
        wins = ds.windows(dt.date(2026, 5, 1), dt.date(2026, 5, 3))
        todo = ds.missing_windows(wins, {"2026-05-02"})
        self.assertEqual([w.day.isoformat() for w in todo], ["2026-05-01", "2026-05-03"])


class HeatmapTest(unittest.TestCase):
    def rows(self):
        data = [row("2026-05-01", 100, 103), row("2026-05-02", 100, 98), row("2026-05-03", 100, 101),
                row("2026-05-04", 100, 102)]
        return [{**r, "date": dt.date.fromisoformat(r["question_date"]), "ret": float(r["return_pct"]),
                 "up": r["result"] == "Up"} for r in data]

    def test_level_has_four_shades(self):
        self.assertEqual([hm.level(x) for x in (0.1, -0.7, 2.0, -5.0)], [1, 2, 3, 4])

    def test_month_total_compounds(self):
        rows = [{"ret": 10.0}, {"ret": -10.0}]
        self.assertAlmostEqual(hm.month_total(rows), -1.0)

    def test_render_marks_up_and_down_squares_with_times_in_tooltip(self):
        page = hm.render({"BTC": self.rows()})
        self.assertIn('class="c u4"', page)   # +3.00% is the top shade
        self.assertIn('class="c d3"', page)   # -2.00%
        self.assertIn('data-a="2026-05-01 00:00"', page)
        self.assertIn('data-w="週五"', page)
        self.assertIn("2026 / 05", page)

    def test_first_of_month_lands_on_its_weekday_column(self):
        # 2026-05-01 is a Friday: Sunday-first grid needs 5 blank cells before day 1
        card = hm.month_card(2026, 5, self.rows(), False)
        self.assertEqual(card.split('<div class="c u')[0].count('class="c blank"'), 5)

    def test_switches_exist_per_symbol(self):
        page = hm.render({"BTC": self.rows(), "ETH": self.rows()})
        for needle in ('id="s-BTC"', 'id="s-ETH"', 'id="r-12"', 'id="r-24"'):
            self.assertIn(needle, page)

    def test_month_cards_are_tagged_by_the_ranges_that_exclude_them(self):
        months = [(2024 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(1, 27)]  # 26 months from 2024-01
        rows = [{**r, "date": dt.date.fromisoformat(r["question_date"]), "ret": float(r["return_pct"]),
                 "up": r["result"] == "Up"} for r in (row(f"{y}-{m:02d}-15", 100, 101) for y, m in months)]
        section = hm.symbol_section("BTC", rows)
        self.assertEqual(section.count('class="card"'), 12)          # inside both ranges
        self.assertEqual(section.count('class="card o12"'), 12)      # only the 24-month view
        self.assertEqual(section.count('class="card o12 o24"'), 2)   # in neither view
        self.assertIn('class="sum s12"', section)
        self.assertIn('class="sum s24"', section)

    def test_render_empty_symbol(self):
        self.assertIn("沒有資料", hm.render({"ETH": []}))


if __name__ == "__main__":
    unittest.main()
