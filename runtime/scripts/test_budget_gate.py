#!/usr/bin/env python3
"""Unit tests for the budget gate's pure helpers (no yaml, no filesystem).

Run: PYTHONPATH=scripts python3 scripts/test_budget_gate.py  (CI does this)
"""
import unittest

from budget_gate import decide, find_budget, tokens_today

TODAY = "2026-07-04"


def row(agent, in_, out, ts=TODAY + " 09:00:00", **extra):
    return {"agent": agent, "in": in_, "out": out, "ts": ts, **extra}


class TestTokensToday(unittest.TestCase):
    def test_sums_only_this_agent_today(self):
        rows = [
            row("it", 40000, 2000),                       # counts
            row("it", 10000, 500, via="offload"),         # offloads count too
            row("business", 90000, 1000),                 # other agent
            row("it", 70000, 3000, ts="2026-07-03 22:00:00"),  # yesterday
        ]
        self.assertEqual(tokens_today(rows, "it", TODAY), 52500)

    def test_cache_tokens_excluded(self):
        rows = [row("it", 1000, 100, cache_read=500000, cache_creation=90000)]
        self.assertEqual(tokens_today(rows, "it", TODAY), 1100)

    def test_malformed_rows_skipped(self):
        rows = [row("it", 100, 10), {"agent": "it", "ts": TODAY, "in": "garbage", "out": None},
                {"nonsense": True}]
        self.assertEqual(tokens_today(rows, "it", TODAY), 110)


class TestDecide(unittest.TestCase):
    def test_over_at_budget(self):
        self.assertEqual(decide(500000, 500000), "over")
        self.assertEqual(decide(500001, 500000), "over")

    def test_ok_under_budget(self):
        self.assertEqual(decide(499999, 500000), "ok")

    def test_zero_or_missing_budget_is_unlimited(self):
        self.assertEqual(decide(10**9, 0), "ok")
        self.assertEqual(decide(10**9, None), "ok")


class TestFindBudget(unittest.TestCase):
    DEPTS = [
        {"name": "ceo", "envelope": {"daily_token_budget": 300000}},
        {"name": "business", "envelope": {"daily_token_budget": 500000},
         "teams": [{"name": "seo", "envelope": {"daily_token_budget": 100000}}]},
        {"name": "opslog"},  # no envelope at all
    ]

    def test_top_level_and_nested(self):
        self.assertEqual(find_budget(self.DEPTS, "ceo"), 300000)
        self.assertEqual(find_budget(self.DEPTS, "seo"), 100000)

    def test_unknown_or_unset_is_zero(self):
        self.assertEqual(find_budget(self.DEPTS, "nope"), 0)
        self.assertEqual(find_budget(self.DEPTS, "opslog"), 0)


if __name__ == "__main__":
    unittest.main()
