#!/usr/bin/env python3
"""Unit tests for the work-item tree's pure helpers — the taxonomy edges and, above
all, the closure rules (the upward path). Same pattern as test_handoff_check.py:
pure logic tested here, gh I/O stays untested-thin.

Run: PYTHONPATH=scripts python3 scripts/test_workitems.py  (CI does this)
"""
import unittest

from workitems import (CHILD_KINDS, KINDS, close_problems, kind_of, link_problems,
                       open_children, parse_comment_ref, parse_pr_ref, render_tree)


class TestKindOf(unittest.TestCase):
    def test_kind_from_label(self):
        self.assertEqual(kind_of(["dept:it", "story"]), "story")

    def test_untyped(self):
        self.assertIsNone(kind_of(["dept:business", "bug"]))
        self.assertIsNone(kind_of([]))
        self.assertIsNone(kind_of(None))

    def test_double_labeled_reads_as_higher_level(self):
        # Mislabeling fails toward the stricter (rollup) closure, not the looser one.
        self.assertEqual(kind_of(["story", "epic"]), "epic")


class TestLinkProblems(unittest.TestCase):
    def test_taxonomy_edges_allowed(self):
        for parent, kids in CHILD_KINDS.items():
            for kid in kids:
                self.assertEqual(link_problems(parent, kid), [])

    def test_level_skipping_refused(self):
        self.assertTrue(link_problems("objective", "story"))
        self.assertTrue(link_problems("epic", "story"))

    def test_upside_down_refused(self):
        self.assertTrue(link_problems("story", "epic"))
        self.assertTrue(link_problems("feature", "objective"))

    def test_leaves_take_no_children(self):
        self.assertTrue(link_problems("story", "story"))
        self.assertTrue(link_problems("proposal", "epic"))

    def test_untyped_side_always_allowed(self):
        # Pre-hierarchy tickets and hand-assembly keep working.
        self.assertEqual(link_problems(None, "story"), [])
        self.assertEqual(link_problems("epic", None), [])
        self.assertEqual(link_problems(None, None), [])


class TestCloseProblems(unittest.TestCase):
    def kids(self, *states):
        return [{"number": i + 1, "state": s, "title": f"t{i}"}
                for i, s in enumerate(states)]

    def test_open_children_block_any_kind(self):
        kids = self.kids("closed", "open")
        for kind in list(KINDS) + [None]:
            self.assertTrue(close_problems(kind, kids, {"done_when": "x"}))

    def test_untyped_childless_passes(self):
        self.assertEqual(close_problems(None, [], {}), [])

    def test_untyped_unmerged_pr_refused(self):
        # The bug this guards: an untyped ticket has no dedicated evidence gate, but
        # citing --pr must still mean a MERGED pr — org#217 closed three times on an
        # unmerged PR before this check existed (workitems.py had no universal check).
        ev = {"pr": "o/r#109", "pr_ref": ("o/r", 109), "pr_merged": False}
        self.assertIn("not merged", close_problems(None, [], ev)[0])

    def test_untyped_merged_pr_passes(self):
        ev = {"pr": "o/r#109", "pr_ref": ("o/r", 109), "pr_merged": True}
        self.assertEqual(close_problems(None, [], ev), [])

    def test_epic_unmerged_pr_refused(self):
        # Same universal check applies to rollup kinds when a PR happens to be cited.
        ev = {"pr": "o/r#109", "pr_ref": ("o/r", 109), "pr_merged": False}
        self.assertIn("not merged", close_problems("epic", [], ev)[0])

    def test_rollup_kinds_close_on_children_alone(self):
        kids = self.kids("closed", "closed")
        self.assertEqual(close_problems("objective", kids, {}), [])
        self.assertEqual(close_problems("epic", kids, {}), [])
        self.assertEqual(close_problems("proposal", [], {}), [])  # rejected = cheap to bury

    def test_story_needs_evidence(self):
        problems = close_problems("story", [], {})
        self.assertEqual(len(problems), 1)
        self.assertIn("--pr", problems[0])
        self.assertIn("--done-when", problems[0])

    def test_story_done_when_passes(self):
        self.assertEqual(close_problems("story", [], {"done_when": "page live"}), [])

    def test_story_unqualified_pr_refused(self):
        problems = close_problems("story", [], {"pr": "#12", "pr_ref": None})
        self.assertIn("repo-qualified", problems[0])

    def test_story_unmerged_pr_refused(self):
        ev = {"pr": "o/r#12", "pr_ref": ("o/r", 12), "pr_merged": False}
        self.assertIn("not merged", close_problems("story", [], ev)[0])

    def test_story_unverifiable_pr_refused(self):
        # gh offline → merged unknown (None) → fails toward refusing, never toward closing.
        ev = {"pr": "o/r#12", "pr_ref": ("o/r", 12), "pr_merged": None}
        self.assertTrue(close_problems("story", [], ev))

    def test_story_merged_pr_passes(self):
        ev = {"pr": "o/r#12", "pr_ref": ("o/r", 12), "pr_merged": True}
        self.assertEqual(close_problems("story", [], ev), [])

    def test_feature_needs_demo_or_done_when(self):
        problems = close_problems("feature", [], {})
        self.assertIn("[DEMO]", problems[0])
        self.assertIn("--done-when", problems[0])

    def test_feature_demo_must_be_a_demo(self):
        ev = {"demo": "https://github.com/o/r/issues/1#issuecomment-9",
              "demo_ref": "repos/o/r/issues/comments/9", "demo_is_demo": False}
        self.assertIn("[DEMO] marker", close_problems("feature", [], ev)[0])

    def test_feature_accepted_demo_passes(self):
        ev = {"demo": "https://github.com/o/r/issues/1#issuecomment-9",
              "demo_ref": "repos/o/r/issues/comments/9", "demo_is_demo": True}
        self.assertEqual(close_problems("feature", [], ev), [])

    def test_feature_done_when_passes(self):
        # Internal/no-product-surface work: the demo gate's own one-line done-when bar.
        self.assertEqual(close_problems("feature", [], {"done_when": "cron installed"}), [])


class TestParseRefs(unittest.TestCase):
    def test_pr_url(self):
        self.assertEqual(parse_pr_ref("https://github.com/o/r/pull/12"), ("o/r", 12))

    def test_pr_shorthand(self):
        self.assertEqual(parse_pr_ref("acme/product#7"),
                         ("acme/product", 7))

    def test_bare_number_rejected(self):
        # Same repo-qualification bar as [DEMO] pr — org and product PR spaces collide.
        self.assertIsNone(parse_pr_ref("#12"))
        self.assertIsNone(parse_pr_ref("12"))
        self.assertIsNone(parse_pr_ref(""))
        self.assertIsNone(parse_pr_ref(None))

    def test_comment_html_url(self):
        self.assertEqual(
            parse_comment_ref("https://github.com/o/r/issues/5#issuecomment-42"),
            "repos/o/r/issues/comments/42")

    def test_comment_on_pr_url(self):
        self.assertEqual(
            parse_comment_ref("https://github.com/o/r/pull/5#issuecomment-42"),
            "repos/o/r/issues/comments/42")

    def test_non_comment_rejected(self):
        self.assertIsNone(parse_comment_ref("https://github.com/o/r/issues/5"))
        self.assertIsNone(parse_comment_ref(None))


class TestRenderTree(unittest.TestCase):
    def test_shape_and_glyphs(self):
        tree = {"number": 1, "title": "[OBJECTIVE] Grow", "state": "open", "children": [
            {"number": 2, "title": "[EPIC] Reddit", "state": "closed", "children": [
                {"number": 4, "title": "[FEATURE] CTA", "state": "open", "children": []},
            ]},
            {"number": 3, "title": "[PROPOSAL] Mugs", "state": "closed", "children": []},
        ]}
        self.assertEqual(render_tree(tree), [
            "● org#1  [OBJECTIVE] Grow",
            "├─ ✓ org#2  [EPIC] Reddit",
            "│  └─ ● org#4  [FEATURE] CTA",
            "└─ ✓ org#3  [PROPOSAL] Mugs",
        ])


if __name__ == "__main__":
    unittest.main(verbosity=1)
