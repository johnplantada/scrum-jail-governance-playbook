#!/usr/bin/env python3
"""Unit tests for the efficiency denominator's pure helpers (no gh, no ledger).

Run: PYTHONPATH=scripts python3 scripts/test_efficiency.py  (CI does this)
"""
import unittest

from efficiency import count_since, per_unit, render_line, window_cost


class TestWindowCost(unittest.TestCase):
    def test_sums_only_rows_in_window(self):
        rows = [
            {"ts": "2026-06-01 09:00:00", "cost_usd": 5.0},   # before the window
            {"ts": "2026-06-20 09:00:00", "cost_usd": 1.25},
            {"ts": "2026-07-01 09:00:00", "cost_usd": 0.75},
        ]
        self.assertEqual(window_cost(rows, "2026-06-05"), 2.0)

    def test_bad_rows_are_skipped(self):
        rows = [{"ts": "2026-06-20 09:00:00", "cost_usd": "not-a-number"},
                {"cost_usd": 1.0},  # no ts → "" < since → skipped
                {"ts": "2026-06-21 09:00:00", "cost_usd": 3.0}]
        self.assertEqual(window_cost(rows, "2026-06-05"), 3.0)

    def test_empty_ledger(self):
        self.assertEqual(window_cost([], "2026-06-05"), 0.0)


class TestCountSince(unittest.TestCase):
    def test_counts_on_or_after_the_date(self):
        stamps = ["2026-06-04T23:59:59Z", "2026-06-05T00:00:00Z", "2026-07-01T12:00:00Z"]
        self.assertEqual(count_since(stamps, "2026-06-05"), 2)

    def test_ignores_empties(self):
        self.assertEqual(count_since([None, "", "2026-07-01T00:00:00Z"], "2026-06-05"), 1)


class TestPerUnit(unittest.TestCase):
    def test_divides(self):
        self.assertEqual(per_unit(30.0, 4), "$7.50")

    def test_zero_output_is_na(self):
        self.assertEqual(per_unit(30.0, 0), "n/a")


class TestRenderLine(unittest.TestCase):
    def test_full_line(self):
        line = render_line(30.0, 30, prs=4, demos=2, ships=0)
        self.assertEqual(line, "$30.0 spent over 30d — $7.50/merged PR (4), "
                               "$15.00/accepted demo (2), n/a/prod ship (0)")

    def test_unavailable_source_reads_na(self):
        line = render_line(10.0, 30, prs=None, demos=1, ships=None)
        self.assertIn("merged PR n/a", line)
        self.assertIn("$10.00/accepted demo (1)", line)
        self.assertIn("prod ship n/a", line)


if __name__ == "__main__":
    unittest.main()
