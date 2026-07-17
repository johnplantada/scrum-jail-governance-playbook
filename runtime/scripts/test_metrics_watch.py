#!/usr/bin/env python3
"""Unit tests for the demand-telemetry watcher's pure helpers (no yaml, no network).

Run: PYTHONPATH=scripts python3 scripts/test_metrics_watch.py  (CI does this)
"""
import unittest

from metrics_watch import (
    diff_for_announce,
    expand_env,
    extract_path,
    is_milestone,
    latest_values,
    observations_to_append,
)

TODAY = "2026-07-04"


def row(source, metric, value, ts=TODAY + " 09:00:00"):
    return {"source": source, "metric": metric, "value": value, "ts": ts}


class TestExtract(unittest.TestCase):
    def test_dotted_path(self):
        body = {"stats": {"orders_count": 3}}
        self.assertEqual(extract_path(body, "stats.orders_count"), 3)
        self.assertEqual(extract_path(body, "orders_count"), None)

    def test_non_numeric_rejected(self):
        self.assertIsNone(extract_path({"v": "three"}, "v"))
        self.assertIsNone(extract_path({"v": True}, "v"))  # bools are not counts

    def test_env_expansion(self):
        env = {"REPORTS_API_URL": "https://api.example.com/prod"}
        self.assertEqual(expand_env("${REPORTS_API_URL}/reports/stats", env),
                         "https://api.example.com/prod/reports/stats")
        # unresolved vars make the URL unusable — signal skip, don't fetch garbage
        self.assertIsNone(expand_env("${MISSING}/x", env))


class TestStoreLogic(unittest.TestCase):
    def test_latest_wins_by_append_order(self):
        rows = [row("spring", "tshirt_sales", 0, "2026-07-01 08:00:00"),
                row("spring", "tshirt_sales", 2)]
        self.assertEqual(latest_values(rows)["spring.tshirt_sales"], (2, TODAY + " 09:00:00"))

    def test_append_on_change_and_daily_heartbeat(self):
        latest = {"product.orders_count": (5, TODAY + " 08:00:00"),
                  "product.reports_count": (1, "2026-07-03 08:00:00")}
        fresh = {"product.orders_count": 5,  # unchanged, sampled today → skip
                 "product.reports_count": 1,    # unchanged but stale sample → heartbeat
                 "spring.tshirt_sales": 3}         # brand new → append
        keys = [k for k, _ in observations_to_append(latest, fresh, TODAY)]
        self.assertEqual(sorted(keys), ["product.reports_count", "spring.tshirt_sales"])


class TestAnnounce(unittest.TestCase):
    def test_diff_against_cursor(self):
        cursor = {"spring.tshirt_sales": 0}
        latest = {"spring.tshirt_sales": (2, "t"), "product.orders_count": (1, "t")}
        self.assertEqual(diff_for_announce(cursor, latest),
                         [("product.orders_count", None, 1),
                          ("spring.tshirt_sales", 0, 2)])

    def test_no_change_no_announce(self):
        cursor = {"product.orders_count": 2}
        latest = {"product.orders_count": (2, "t")}
        self.assertEqual(diff_for_announce(cursor, latest), [])

    def test_milestones(self):
        # first_nonzero fires exactly once — on the transition out of nothing
        self.assertTrue(is_milestone("first_nonzero", None, 1))
        self.assertTrue(is_milestone("first_nonzero", 0, 3))
        self.assertFalse(is_milestone("first_nonzero", 1, 2))
        # any_change fires on every movement, including a refund going down
        self.assertTrue(is_milestone("any_change", 2, 1))
        self.assertFalse(is_milestone("any_change", 2, 2))
        # none never wakes anyone
        self.assertFalse(is_milestone("none", 0, 100))


if __name__ == "__main__":
    unittest.main()
