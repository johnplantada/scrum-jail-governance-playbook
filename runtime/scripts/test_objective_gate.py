#!/usr/bin/env python3
"""Unit tests for the objective gate's pure helpers.

The regression under test is real: the CEO opened org#7/8/9 with bare
`gh issue create --label objective` while following agents/ceo.md's then-mandate. The
false-positive cases matter just as much — the org reasons about objectives on every
wake, and a gate that blocked reading them would be worse than no gate.

Run: PYTHONPATH=scripts python3 scripts/test_objective_gate.py  (CI does this)
"""
import unittest

from objective_gate import (
    carries_objective_kind,
    decide,
    deny_payload,
    flag_values,
    has_verb,
    is_objective_write,
    split_commands,
)


class TestSplitCommands(unittest.TestCase):
    def test_splits_on_operators(self):
        self.assertEqual(
            split_commands(["a", "&&", "b", ";", "c", "|", "d"]),
            [["a"], ["b"], ["c"], ["d"]],
        )

    def test_collapses_empties_and_handles_none(self):
        self.assertEqual(split_commands(["&&", "a", "&&", "&&"]), [["a"]])
        self.assertEqual(split_commands(None), [])


class TestFlagValues(unittest.TestCase):
    def test_space_and_equals_forms(self):
        self.assertEqual(flag_values(["--label", "objective"], ("--label",)), ["objective"])
        self.assertEqual(flag_values(["--label=objective"], ("--label",)), ["objective"])

    def test_trailing_flag_without_value_is_not_a_crash(self):
        self.assertEqual(flag_values(["--label"], ("--label",)), [])


class TestHasVerb(unittest.TestCase):
    def test_adjacent_only(self):
        self.assertTrue(has_verb(["gh", "issue", "create"], "issue", "create"))
        self.assertFalse(has_verb(["gh", "issue", "list"], "issue", "create"))
        self.assertFalse(has_verb(["gh"], "issue", "create"))


class TestCarriesObjectiveKind(unittest.TestCase):
    def test_label_forms(self):
        self.assertTrue(carries_objective_kind(["--label", "objective"]))
        self.assertTrue(carries_objective_kind(["--label", "dept:it,objective"]))
        self.assertTrue(carries_objective_kind(["-l", "objective"]))

    def test_title_prefix(self):
        self.assertTrue(carries_objective_kind(["--title", "[OBJECTIVE] Governed AI"]))

    def test_other_kinds_are_not_objectives(self):
        self.assertFalse(carries_objective_kind(["--label", "epic"]))
        self.assertFalse(carries_objective_kind(["--title", "[EPIC] Two-mode eval harness"]))
        # substring, not the kind — "objectives" is a different label
        self.assertFalse(carries_objective_kind(["--label", "objectives"]))


class TestIsObjectiveWrite(unittest.TestCase):
    def test_the_actual_org7_regression(self):
        self.assertTrue(is_objective_write(
            ["gh", "issue", "create", "--repo", "acme/acme-org",
             "--title", "[OBJECTIVE] Independence boundary held", "--label", "objective",
             "--label", "dept:it", "--body", "..."]))

    def test_relabelling_an_issue_into_an_objective(self):
        self.assertTrue(is_objective_write(["gh", "issue", "edit", "5", "--add-label", "objective"]))
        self.assertTrue(is_objective_write(["gh", "issue", "edit", "5", "--title", "[OBJECTIVE] x"]))

    def test_gh_api_path(self):
        self.assertTrue(is_objective_write(
            ["gh", "api", "repos/o/r/issues", "-f", "title=[OBJECTIVE] x", "-f", "labels[]=objective"]))
        self.assertTrue(is_objective_write(
            ["gh", "api", "-X", "POST", "repos/o/r/issues", "-f", "labels[]=objective"]))

    def test_reads_are_never_gated(self):
        self.assertFalse(is_objective_write(["gh", "issue", "list", "--label", "objective"]))
        self.assertFalse(is_objective_write(["gh", "issue", "view", "7", "--comments"]))
        self.assertFalse(is_objective_write(["gh", "api", "repos/o/r/issues/7/sub_issues"]))

    def test_commenting_about_an_objective_is_not_minting_one(self):
        self.assertFalse(is_objective_write(
            ["gh", "issue", "comment", "6", "--body", "org#7 is an [OBJECTIVE] I can't open"]))

    def test_building_under_an_objective_is_allowed(self):
        self.assertFalse(is_objective_write(
            ["gh", "issue", "create", "--title", "[EPIC] Citation gate", "--label", "epic"]))

    def test_non_gh_programs_are_not_ours(self):
        self.assertFalse(is_objective_write(["echo", "gh", "issue", "create", "--label", "objective"]))


class TestDecide(unittest.TestCase):
    def test_denies_the_regression(self):
        self.assertEqual(
            decide('gh issue create --title "[OBJECTIVE] Governed AI" --label objective'), "deny")

    def test_denies_when_hidden_behind_an_operator(self):
        self.assertEqual(decide("gh issue list && gh issue create --label objective"), "deny")

    def test_allows_ordinary_work(self):
        self.assertEqual(decide("gh issue view 6 --comments"), "allow")
        self.assertEqual(decide("scripts/pm-gh.sh create --type epic --parent 1 --title x"), "allow")
        self.assertEqual(decide(""), "allow")

    def test_unparseable_command_fails_open(self):
        self.assertEqual(decide('gh issue create --title "unbalanced'), "allow")


class TestDenyPayload(unittest.TestCase):
    def test_shape_and_routing_advice(self):
        p = deny_payload("ceo")
        self.assertEqual(p["decision"], "block")
        self.assertEqual(p["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(p["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        # the refusal must name the path forward, or it just teaches evasion
        self.assertIn("[PROPOSAL]", p["reason"])
        self.assertIn("ceo", p["reason"])


if __name__ == "__main__":
    unittest.main()
