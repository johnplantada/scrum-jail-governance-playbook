#!/usr/bin/env python3
"""Unit tests for the constitution linter's pure checks (no filesystem walks).

Each drift case below is the REAL string that had drifted in the repo before the linter
existed — the tests pin that each historical drift class stays detectable.

Run: PYTHONPATH=scripts python3 scripts/test_lint_constitution.py  (CI does this)
"""
import os
import tempfile
import unittest

from lint_constitution import (
    canonical_stages,
    check_cadence,
    check_skills,
    check_stages,
    holding_stages,
)

STAGES = ["To-Do", "Doing", "Staged", "Demo", "Done"]
HOLDING = ["Blocked", "On-Hold"]


class TestCadence(unittest.TestCase):
    def test_every_n_wakes_flagged(self):
        # the constitution pre-fix
        hits = check_cadence("d.md", "- **Every 5 wakes** — Business and IT each post `REVIEW`")
        self.assertEqual(len(hits), 1)

    def test_n_cycle_review_flagged(self):
        # agents/it.md §heading pre-fix
        self.assertTrue(check_cadence("d.md", "## 5-cycle review"))
        # _policy.md pre-fix (hyphenated compound)
        self.assertTrue(check_cadence("d.md", "no convening a fresh 5-cycle-review debate"))

    def test_pi_equals_n_iterations_flagged(self):
        # the constitution pre-fix
        self.assertTrue(check_cadence("d.md", "**Program Increments.** A PI = 4 iterations (~20 wakes)."))

    def test_parameter_reference_clean(self):
        text = ("Every review interval (`global.review_interval` wakes — org-chart.yaml), "
                "Business and IT post a [REVIEW]. A PI = `global.pi_interval` iterations.")
        self.assertEqual(check_cadence("d.md", text), [])

    def test_unrelated_numbers_clean(self):
        # wake-guard prose and daily cadence must not false-positive
        text = "90s per-agent cooldown + 40 wakes/hr global breaker; 1 cycle/day per agent"
        self.assertEqual(check_cadence("d.md", text), [])


class TestStages(unittest.TestCase):
    def test_canon_parse(self):
        chart = "global:\n  pm_stages: [To-Do, Doing, Staged, Demo, Done]\n"
        self.assertEqual(canonical_stages(chart), STAGES)

    def test_wrong_order_flagged(self):
        # the constitution pre-fix: Demo listed before Staged, contradicting its own bullets
        line = "**Kanban workflow stages (in order):** `To-Do → Doing → Demo → Staged → Done`"
        hits = check_stages("d.md", line, STAGES)
        self.assertTrue(any("out of canonical order" in h for h in hits))

    def test_incomplete_claim_flagged(self):
        # agents/it.md pre-fix: claimed the workflow was three stages
        line = "the workflow stages are `To-Do`, `Doing`, `Done`:"
        hits = check_stages("d.md", line, STAGES)
        self.assertTrue(any("full canon" in h for h in hits))

    def test_partial_chain_in_order_clean(self):
        self.assertEqual(check_stages("d.md", "move the ticket `Demo → Done`", STAGES), [])

    def test_non_stage_chain_clean(self):
        self.assertEqual(
            check_stages("d.md", "one poller (`poll → route → wake`)", STAGES), [])

    def test_wrapped_claim_with_reference_clean(self):
        text = ("the workflow stages are the canonical\n"
                "`To-Do → Doing → Staged → Demo → Done` (org-chart.yaml `global.pm_stages`)")
        self.assertEqual(check_stages("d.md", text, STAGES), [])

    def test_holding_stages_parse(self):
        chart = ("global:\n  pm_stages: [To-Do, Doing, Staged, Demo, Done]\n"
                 "  pm_holding_stages: [Blocked, On-Hold]\n")
        self.assertEqual(holding_stages(chart), HOLDING)
        self.assertEqual(holding_stages("global:\n  pm_stages: [To-Do]\n"), [])

    def test_holding_stage_in_chain_is_recognized_not_flagged(self):
        # A chain routing a flow item through a holding column names a valid stage —
        # not canonical-flow, but not an error either. Order is judged on flow stages only.
        line = "park it: `Doing → Blocked → Doing` while the credential is pending"
        self.assertEqual(check_stages("d.md", line, STAGES, HOLDING), [])

    def test_unknown_stage_in_chain_still_flagged(self):
        # Holding-awareness must not blanket-pass typos: a non-stage token still fails.
        line = "`To-Do → Doing → Frozen`"
        hits = check_stages("d.md", line, STAGES, HOLDING)
        self.assertTrue(any("non-canonical" in h for h in hits))


class TestSkillRefs(unittest.TestCase):
    def _root_with(self, *skills):
        root = tempfile.mkdtemp()
        for name in skills:
            d = os.path.join(root, ".claude", "skills", name)
            os.makedirs(d)
            with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write("---\nname: %s\n---\n" % name)
        return root

    def test_backtick_ref_missing_flagged(self):
        root = self._root_with()
        hits = check_skills("d.md", "invoke the `org-worktree` skill", root)
        self.assertTrue(any("org-worktree" in h for h in hits))

    def test_bold_domain_skill_ref_missing_flagged(self):
        # the real dangling reference: two mandates named their authoritative tool in
        # bold + "domain skill" and the linter never saw it
        root = self._root_with()
        line = "consult the **urar-review-compliance-expert** domain skill first"
        hits = check_skills("d.md", line, root)
        self.assertTrue(any("urar-review-compliance-expert" in h for h in hits))

    def test_backtick_domain_skill_ref_checked(self):
        root = self._root_with("urar-review-compliance-expert")
        line = "consult the `urar-review-compliance-expert` skill first"
        self.assertEqual(check_skills("d.md", line, root), [])

    def test_existing_skill_clean(self):
        root = self._root_with("blocker-triage")
        self.assertEqual(
            check_skills("d.md", "invoke the `blocker-triage` skill", root), [])

    def test_bold_prose_word_not_flagged(self):
        # un-hyphenated bold words before "skill" are prose emphasis, not a skill name
        root = self._root_with()
        self.assertEqual(check_skills("d.md", "**every** skill in the repo", root), [])


if __name__ == "__main__":
    unittest.main()
