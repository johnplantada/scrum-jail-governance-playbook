#!/usr/bin/env python3
"""Unit tests for the blocker queue's pure helpers (no yaml, no I/O).

Run: PYTHONPATH=scripts python3 scripts/test_blockers.py  (CI does this)
"""
import datetime
import unittest

from blockers import (age_days, format_line, is_market_contact, market_contact_alert,
                      needs_runbook, open_entries, sort_key, wip_warning)

TODAY = datetime.date(2026, 7, 5)


def entry(**kw):
    base = {"id": "x", "kind": "external-input", "state": "open"}
    base.update(kw)
    return base


class TestAgeDays(unittest.TestCase):
    def test_counts_whole_days(self):
        self.assertEqual(age_days("2026-06-28", TODAY), 7)

    def test_today_is_zero(self):
        self.assertEqual(age_days("2026-07-05", TODAY), 0)

    def test_future_clamps_to_zero(self):
        self.assertEqual(age_days("2026-07-09", TODAY), 0)

    def test_garbage_is_none(self):
        self.assertIsNone(age_days(None, TODAY))
        self.assertIsNone(age_days("soon", TODAY))


class TestQueueOrder(unittest.TestCase):
    def test_value_class_dominates_effort(self):
        cheap_infra = entry(id="a", value="infra", effort_minutes=1)
        pricey_revenue = entry(id="b", value="revenue", effort_minutes=60)
        self.assertLess(sort_key(pricey_revenue), sort_key(cheap_infra))

    def test_effort_breaks_ties_within_a_class(self):
        slow = entry(id="a", value="revenue", effort_minutes=30)
        quick = entry(id="b", value="revenue", effort_minutes=5)
        self.assertLess(sort_key(quick), sort_key(slow))

    def test_unclassified_sinks_below_infra(self):
        infra = entry(id="a", value="infra", effort_minutes=60)
        mystery = entry(id="b", effort_minutes=1)
        self.assertLess(sort_key(infra), sort_key(mystery))

    def test_unknown_effort_sorts_last_in_class(self):
        estimated = entry(id="a", value="signal", effort_minutes=45)
        unestimated = entry(id="b", value="signal")
        self.assertLess(sort_key(estimated), sort_key(unestimated))

    def test_market_contact_outranks_every_value_class(self):
        # a flagged signal beats an unflagged revenue — existential trumps EV
        flagged = entry(id="a", value="signal", effort_minutes=60,
                        gates_market_contact=True)
        pricey_revenue = entry(id="b", value="revenue", effort_minutes=5)
        self.assertLess(sort_key(flagged), sort_key(pricey_revenue))

    def test_market_contact_entries_float_to_the_top(self):
        data = {"blockers": [
            entry(id="rev", value="revenue", effort_minutes=5),
            entry(id="mc-sig", value="signal", effort_minutes=10, gates_market_contact=True),
            entry(id="mc-rev", value="revenue", effort_minutes=45, gates_market_contact=True),
        ]}
        # both flagged first (revenue before signal within the flagged tier), then the rest
        self.assertEqual([b["id"] for b in open_entries(data)],
                         ["mc-rev", "mc-sig", "rev"])


class TestMarketContact(unittest.TestCase):
    def test_flag_predicate(self):
        self.assertTrue(is_market_contact(entry(gates_market_contact=True)))
        self.assertFalse(is_market_contact(entry()))
        self.assertFalse(is_market_contact(entry(gates_market_contact=False)))

    def test_alert_none_when_nothing_flagged(self):
        self.assertIsNone(market_contact_alert(
            [entry(id="a", value="revenue")], TODAY))

    def test_alert_names_each_flagged_entry_loudly(self):
        flagged = entry(id="reddit-linkedin-publish", opened="2026-06-29",
                        summary="nobody has been told the product exists",
                        gates_market_contact=True)
        alert = market_contact_alert([flagged, entry(id="infra", value="infra")], TODAY)
        self.assertIn("MARKET-CONTACT BLOCKED", alert)
        self.assertIn("does NOT go quiet", alert)
        self.assertIn("reddit-linkedin-publish", alert)
        self.assertIn("6d open", alert)          # age surfaced
        self.assertNotIn("infra", alert)         # only flagged entries are named

    def test_format_line_marks_flagged(self):
        line = format_line(entry(id="mc", value="revenue", gates_market_contact=True), TODAY)
        self.assertIn("🔴 market-contact", line)


class TestRunbookLint(unittest.TestCase):
    def test_open_entry_without_action_needs_runbook(self):
        self.assertTrue(needs_runbook(entry(id="bare")))
        self.assertTrue(needs_runbook(entry(id="thin", action="do it")))

    def test_actionable_entry_is_fine(self):
        self.assertFalse(needs_runbook(entry(
            id="ok", action="Create the Spring store at springworks.com and paste the URL")))

    def test_runbook_pointer_satisfies_it(self):
        self.assertFalse(needs_runbook(entry(id="ptr", runbook="playbook/DEPLOY-RUNBOOK.md")))

    def test_cleared_entries_are_exempt(self):
        self.assertFalse(needs_runbook(entry(id="done", state="cleared")))

    def test_open_entries_filters_and_sorts(self):
        data = {"blockers": [
            entry(id="done", state="cleared", value="revenue", effort_minutes=1),
            entry(id="slow-rev", value="revenue", effort_minutes=20),
            entry(id="sig", value="signal", effort_minutes=10),
            entry(id="fast-rev", value="revenue", effort_minutes=5),
        ]}
        self.assertEqual([b["id"] for b in open_entries(data)],
                         ["fast-rev", "slow-rev", "sig"])

    def test_missing_state_counts_as_open(self):
        data = {"blockers": [{"id": "bare"}]}
        self.assertEqual(len(open_entries(data)), 1)


class TestWipWarning(unittest.TestCase):
    def test_quiet_at_or_under_limit(self):
        self.assertIsNone(wip_warning(5, 5))
        self.assertIsNone(wip_warning(0, 5))

    def test_fires_over_limit(self):
        w = wip_warning(7, 5)
        self.assertIn("7 open", w)
        self.assertIn("unlock_wip_limit is 5", w)

    def test_no_limit_never_fires(self):
        self.assertIsNone(wip_warning(50, None))
        self.assertIsNone(wip_warning(50, 0))


class TestFormatLine(unittest.TestCase):
    def test_carries_value_effort_age_and_action(self):
        b = entry(id="spring-store-url", value="revenue", effort_minutes=20,
                  opened="2026-06-28", summary="Mug store  needs a URL.",
                  action="Create the Spring store.")
        line = format_line(b, TODAY)
        self.assertEqual(line, "- [spring-store-url] (external-input, revenue, ~20min, "
                               "7d old) Mug store needs a URL.  → Create the Spring store.")

    def test_minimal_entry_renders(self):
        self.assertEqual(format_line({"id": "x"}, TODAY), "- [x] () ")

    def test_all_mode_shows_state(self):
        line = format_line(entry(id="x", state="cleared"), TODAY, show_state=True)
        self.assertIn("[cleared]", line)


if __name__ == "__main__":
    unittest.main()
