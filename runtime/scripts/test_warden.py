#!/usr/bin/env python3
"""Unit tests for warden.py's pure helpers (no gh, no filesystem).

Run: PYTHONPATH=scripts python3 scripts/test_warden.py  (CI does this)
"""
import unittest

from warden import (desired_queue, desired_sync_threads, detect_conflicts,
                    expected_stage, kind_mismatch, linked_refs, parse_chart_pin,
                    parse_source, plan_moves, plan_sync, pr_ready, render_report)


def child(number, key=None, state="open", title="t"):
    body = f"stuff\n\nwarden-source: {key}\n(managed)" if key else "hand-written child"
    return {"number": number, "state": state, "title": title, "body": body}


class TestMarkers(unittest.TestCase):
    def test_parse_source(self):
        self.assertEqual(parse_source("x\nwarden-source: blocker:spring-store-url\ny"),
                         "blocker:spring-store-url")
        self.assertIsNone(parse_source("no marker here"))
        self.assertIsNone(parse_source(None))

    def test_parse_chart_pin_regex_fallback(self):
        chart = "global:\n  unlock_wip_limit: 5\n  chairman_queue_issue: 138\n" \
                "  pm_project_number: 6\ndepartments:\n"
        self.assertEqual(parse_chart_pin(chart, "chairman_queue_issue"), 138)
        self.assertEqual(parse_chart_pin(chart, "pm_project_number"), 6)
        self.assertIsNone(parse_chart_pin(chart, "missing_key"))
        self.assertIsNone(parse_chart_pin(None, "chairman_queue_issue"))
        # a commented-out pin must not parse
        self.assertIsNone(parse_chart_pin("# chairman_queue_issue: 9", "chairman_queue_issue"))


class TestPrReady(unittest.TestCase):
    def test_ready_needs_clean_nondraft_and_no_changes_requested(self):
        ok = {"isDraft": False, "mergeStateStatus": "CLEAN", "reviewDecision": ""}
        self.assertTrue(pr_ready(ok))
        self.assertTrue(pr_ready(dict(ok, reviewDecision="APPROVED")))
        self.assertFalse(pr_ready(dict(ok, isDraft=True)))
        self.assertFalse(pr_ready(dict(ok, mergeStateStatus="BLOCKED")))
        self.assertFalse(pr_ready(dict(ok, mergeStateStatus="UNKNOWN")))
        self.assertFalse(pr_ready(dict(ok, reviewDecision="CHANGES_REQUESTED")))
        self.assertFalse(pr_ready(None))

    def test_behind_still_ready(self):
        # BEHIND = only the base branch moved; the PR is still open, mergeable, and a real
        # Chairman action item — treating it as not-ready silently closed its tracker while
        # the PR sat open and unmerged (org#254 / scrum-jail-business PR #253, 2026-07-12).
        behind = {"isDraft": False, "mergeStateStatus": "BEHIND", "reviewDecision": ""}
        self.assertTrue(pr_ready(behind))
        self.assertFalse(pr_ready(dict(behind, reviewDecision="CHANGES_REQUESTED")))
        self.assertFalse(pr_ready(dict(behind, isDraft=True)))


class TestDesiredQueue(unittest.TestCase):
    def test_sources_map_to_keys(self):
        blockers = {"blockers": [
            {"id": "spring-store-url", "state": "open", "action": "Create the store",
             "value": "revenue", "effort_minutes": 20, "blocks": ["merch"]},
            {"id": "done-one", "state": "cleared", "action": "old"},
        ]}
        prs = {"product": [{"number": 105, "title": "Poster redesign", "url": "u",
                            "isDraft": False, "mergeStateStatus": "CLEAN",
                            "reviewDecision": ""}],
               "org": [{"number": 9, "title": "not ready", "url": "u",
                        "isDraft": False, "mergeStateStatus": "BLOCKED",
                        "reviewDecision": ""}]}
        proposals = [{"number": 42, "title": "[PROPOSAL] pick a bet", "url": "u"}]
        d = desired_queue(blockers, prs, proposals)
        self.assertEqual(sorted(d), ["blocker:spring-store-url", "pr:product#105",
                                     "proposal:org#42"])
        self.assertIn("Chairman: Create the store", d["blocker:spring-store-url"]["title"])
        self.assertIn("warden-source: blocker:spring-store-url",
                      d["blocker:spring-store-url"]["body"])
        self.assertIn("merge product PR #105", d["pr:product#105"]["title"])

    def test_blocker_without_id_or_action_degrades_gracefully(self):
        d = desired_queue({"blockers": [{"id": "x"}, {"summary": "no id"}]}, {}, [])
        self.assertEqual(list(d), ["blocker:x"])
        self.assertIn("Chairman: x", d["blocker:x"]["title"])

    def test_market_contact_blocker_is_marked_loud(self):
        d = desired_queue({"blockers": [
            {"id": "gumroad", "state": "open", "action": "Create the listing",
             "value": "revenue", "gates_market_contact": True},
            {"id": "plain", "state": "open", "action": "Set two repo secrets",
             "value": "infra"},
        ]}, {}, [])
        self.assertTrue(d["blocker:gumroad"]["title"].startswith("🔴 "))
        self.assertIn("MARKET-CONTACT", d["blocker:gumroad"]["body"])
        self.assertIn("does NOT go quiet", d["blocker:gumroad"]["body"])
        # an ordinary infra blocker is untouched
        self.assertFalse(d["blocker:plain"]["title"].startswith("🔴"))
        self.assertNotIn("MARKET-CONTACT", d["blocker:plain"]["body"])


class TestPlanSync(unittest.TestCase):
    def test_create_close_keep_unmanaged(self):
        desired = {"blocker:a": {"title": "A", "body": "…"},
                   "pr:product#7": {"title": "B", "body": "…"}}
        children = [child(101, "blocker:a"),               # kept
                    child(102, "blocker:gone"),            # source cleared → close
                    child(103),                            # unmanaged → hands off
                    child(104, "pr:product#7", state="closed")]  # closed → fresh child ok
        plan = plan_sync(desired, children)
        self.assertEqual([k for k, _ in plan["create"]], ["pr:product#7"])
        self.assertEqual(plan["close"], [(102, "blocker:gone")])
        self.assertEqual(plan["unmanaged"], [103])
        self.assertEqual(plan["kept"], ["blocker:a"])

    def test_empty_everything(self):
        plan = plan_sync({}, [])
        self.assertEqual((plan["create"], plan["close"], plan["unmanaged"]), ([], [], []))

    def test_duplicate_key_children_are_closed_not_kept(self):
        desired = {"blocker:a": {"title": "A", "body": "…"}}
        plan = plan_sync(desired, [child(101, "blocker:a"), child(105, "blocker:a")])
        self.assertEqual(plan["kept"], ["blocker:a"])
        self.assertEqual(plan["create"], [])
        self.assertEqual(plan["close"], [(105, "blocker:a (duplicate of #101)")])


class TestHygieneAndReport(unittest.TestCase):
    def test_kind_mismatch(self):
        self.assertTrue(kind_mismatch("Fix the footer", ["story"]))
        self.assertTrue(kind_mismatch("[STORY] Fix the footer", []))
        self.assertFalse(kind_mismatch("[STORY] Fix the footer", ["story", "dept:it"]))
        self.assertFalse(kind_mismatch("plain ticket", ["dept:it"]))

    def test_report_is_deterministic_and_carries_marker(self):
        plan = dict(create=[("blocker:a", {"title": "A", "body": ""})],
                    close=[(102, "blocker:gone")], unmanaged=[103], kept=["pr:product#7"])
        r1 = render_report(plan, ["finding one"], "2026-07-11 16:00", 138)
        r2 = render_report(plan, ["finding one"], "2026-07-11 16:00", 138)
        self.assertEqual(r1, r2)
        self.assertIn("<!-- warden-report -->", r1)
        self.assertIn("1 current, 1 to add, 1 cleared", r1)
        self.assertIn("#103", r1)
        self.assertIn("finding one", r1)
        clean = render_report(dict(create=[], close=[], unmanaged=[], kept=[]),
                              [], "t", 138)
        self.assertIn("Hygiene: clean.", clean)


class TestBoardReconcile(unittest.TestCase):
    def test_linked_refs(self):
        self.assertEqual(linked_refs("closes org#163, also org#12"), {163, 12})
        self.assertEqual(linked_refs("plain #42 is not a work-item ref"), set())
        self.assertEqual(linked_refs(None), set())

    def test_expected_stage_is_forward_only(self):
        self.assertEqual(expected_stage("To-Do", False, True), "Doing")
        self.assertEqual(expected_stage("To-Do", True, False), "Staged")
        self.assertEqual(expected_stage("Doing", True, True), "Staged")
        self.assertIsNone(expected_stage("Staged", True, False))    # never re-move
        self.assertIsNone(expected_stage("Demo", True, True))       # never backward
        self.assertIsNone(expected_stage("Done", True, True))
        self.assertIsNone(expected_stage("Doing", False, True))     # already there
        self.assertEqual(expected_stage(None, False, True), "Doing")  # off-board

    def test_holding_stages_are_never_auto_advanced(self):
        # A parked item (pm_holding_stages) stays put even with a PR — someone put it
        # there on purpose; forward reconcile must not yank it back into the flow.
        self.assertIsNone(expected_stage("Blocked", True, True))
        self.assertIsNone(expected_stage("Blocked", False, True))
        self.assertIsNone(expected_stage("On-Hold", True, False))
        self.assertIsNone(expected_stage("On-Hold", False, True))

    def test_plan_moves_and_anomalies(self):
        issues = [{"number": 1, "title": "story a"}, {"number": 2, "title": "ghost"},
                  {"number": 3, "title": "fine"}]
        stages = {1: "To-Do", 2: "Staged", 3: "Doing"}
        links = {1: {"open": ["product#9"], "merged": []},
                 3: {"open": ["product#7"], "merged": []}}
        moves, anomalies = plan_moves(issues, stages, links)
        self.assertEqual(moves, [(1, "To-Do", "Doing", "open PR product#9")])
        self.assertEqual(len(anomalies), 1)
        self.assertIn("org#2", anomalies[0])

    def test_weak_links_suppress_anomalies_but_never_move(self):
        issues = [{"number": 4, "title": "mentioned only"},
                  {"number": 5, "title": "staged, weakly linked"}]
        stages = {4: "To-Do", 5: "Staged"}
        links = {4: {"open": [], "merged": [], "open_weak": ["org#99"]},
                 5: {"open": [], "merged": [], "merged_weak": ["product#88"]}}
        moves, anomalies = plan_moves(issues, stages, links)
        self.assertEqual(moves, [])         # a body mention never drives a move
        self.assertEqual(anomalies, [])     # …but it does vouch against the anomaly

    def test_parked_item_with_pr_is_not_moved(self):
        # org#8 is parked in Blocked with an open PR — plan_moves must leave it there
        # (and not flag it as a backward anomaly either).
        issues = [{"number": 8, "title": "blocked but has a PR"}]
        stages = {8: "Blocked"}
        links = {8: {"open": ["product#12"], "merged": []}}
        moves, anomalies = plan_moves(issues, stages, links)
        self.assertEqual((moves, anomalies), ([], []))

    def test_epics_and_objectives_are_rollup_not_pr_tracked(self):
        issues = [{"number": 6, "title": "[EPIC] big", "labels": [{"name": "epic"}]},
                  {"number": 7, "title": "[OBJECTIVE] bigger",
                   "labels": [{"name": "objective"}, {"name": "dept:ceo"}]}]
        stages = {6: "Doing", 7: "Staged"}
        links = {6: {"open": [], "merged": ["product#1"]}}
        moves, anomalies = plan_moves(issues, stages, links)
        self.assertEqual((moves, anomalies), ([], []))


class TestConflicts(unittest.TestCase):
    def test_dirty_overlap_and_dependency(self):
        prs = [{"number": 10, "title": "a", "mergeStateStatus": "DIRTY", "body": "",
                "files": ["src/App.jsx", "package-lock.json"]},
               {"number": 11, "title": "b", "mergeStateStatus": "CLEAN",
                "body": "depends on #10", "files": ["src/App.jsx"]},
               {"number": 12, "title": "c", "mergeStateStatus": "CLEAN",
                "body": "blocked by #99 (already merged)", "files": ["src/Other.jsx"]}]
        c = detect_conflicts(prs)
        self.assertIn("conflict:dirty:product#10", c)
        self.assertIn("conflict:dep:product#11->product#10", c)
        self.assertIn("conflict:overlap:product#10+product#11", c)
        # lockfile-only overlap and closed-dep references don't convene anyone
        self.assertNotIn("conflict:dep:product#12->product#99", c)
        self.assertIn("src/App.jsx", c["conflict:overlap:product#10+product#11"]["facts"])

    def test_sync_threads_carry_marker_and_converge_via_plan_sync(self):
        desired = desired_sync_threads(
            {"conflict:dirty:product#10": {"title": "[SYNC] merge conflict: product PR #10",
                                           "facts": "f"}})
        body = desired["conflict:dirty:product#10"]["body"]
        self.assertIn("warden-source: conflict:dirty:product#10", body)
        existing = [{"number": 55, "state": "open", "title": "[SYNC] stale",
                     "body": "x\nwarden-source: conflict:dirty:product#9\ny"}]
        plan = plan_sync(desired, existing)
        self.assertEqual([k for k, _ in plan["create"]], ["conflict:dirty:product#10"])
        self.assertEqual(plan["close"], [(55, "conflict:dirty:product#9")])

    def test_report_sections_for_moves_and_conflicts(self):
        base = dict(create=[], close=[], unmanaged=[], kept=[])
        r = render_report(base, [], "t", 138,
                          moves=[(1, "To-Do", "Doing", "open PR product#9")],
                          sync_plan=dict(create=[("k", {})], close=[(55, "k2")],
                                         unmanaged=[], kept=["k3"]))
        self.assertIn("org#1: To-Do → Doing", r)
        self.assertIn("2 active [SYNC] discussion(s)", r)
        self.assertIn("1 resolved", r)


if __name__ == "__main__":
    unittest.main()
