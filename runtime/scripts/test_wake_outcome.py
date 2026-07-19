#!/usr/bin/env python3
"""Unit tests for wake_outcome's pure helpers (no SDK, no filesystem).

Run: PYTHONPATH=scripts python3 scripts/test_wake_outcome.py  (CI does this)
"""
import unittest

from wake_outcome import classify_tool_use, render_yield, wake_yield, worst_case


class TestClassify(unittest.TestCase):
    def test_ship_commands(self):
        for cmd in ("git push origin agent/it/fix", "cd repo && git push",
                    "gh pr create --title x", "gh pr merge 12 --rebase", "gh release create v1"):
            self.assertEqual(classify_tool_use("Bash", {"command": cmd}), "ship", cmd)

    def test_post_commands(self):
        for cmd in ("gh issue comment 5 --body hi", "gh issue close 5",
                    "gh pr review 12 --approve", "scripts/pm-gh.sh move --id 3 --to Todo",
                    "./scripts/pm-gh.sh done --id 9 --pr prod-PR-#1",
                    "git commit -m 'wip'", "gh api repos/o/r/issues -X POST",
                    "gh project item-add 1 --owner me --url u"):
            self.assertEqual(classify_tool_use("Bash", {"command": cmd}), "post", cmd)

    def test_reads_are_not_mutations(self):
        for cmd in ("gh issue list", "gh pr view 12 --json state", "gh pr checks 12",
                    "git status", "git log --oneline", "gh api repos/o/r/issues/5",
                    "cat blockers.yaml", "scripts/pm-gh.sh tasks --project it",
                    "scripts/pm-gh.sh comments --id 5", "gh run list"):
            self.assertIsNone(classify_tool_use("Bash", {"command": cmd}), cmd)

    def test_ledger_edit_is_post_but_code_edit_is_not(self):
        self.assertEqual(classify_tool_use("Edit", {"file_path": "/org/blockers.yaml"}), "post")
        self.assertEqual(classify_tool_use("Write", {"file_path": "decisions.yaml"}), "post")
        self.assertIsNone(classify_tool_use("Edit", {"file_path": "src/App.jsx"}))
        self.assertIsNone(classify_tool_use("Read", {"file_path": "blockers.yaml"}))

    def test_never_raises_on_garbage(self):
        self.assertIsNone(classify_tool_use(None, None))
        self.assertIsNone(classify_tool_use("Bash", {"command": None}))
        self.assertIsNone(classify_tool_use("Bash", "not-a-dict"))

    def test_worst_case_ranks_ship_over_post_over_noop(self):
        o = "noop"
        o = worst_case(o, None)
        self.assertEqual(o, "noop")
        o = worst_case(o, "post")
        self.assertEqual(o, "post")
        o = worst_case(o, "ship")
        self.assertEqual(o, "ship")
        self.assertEqual(worst_case("ship", "post"), "ship")


def row(**kw):
    base = {"source": "cycle", "ts": "2026-07-11 10:00:00", "status": "ok"}
    base.update(kw)
    return base


class TestYield(unittest.TestCase):
    def test_counts_one_outcome_per_wake(self):
        rows = [row(wake_id="w1", outcome="ship"),
                row(wake_id="w1"),                 # per-model sibling: not untagged
                row(wake_id="w2", outcome="noop")]
        y = wake_yield(rows)
        self.assertEqual((y["ship"], y["noop"], y["tagged"], y["untagged"]), (1, 1, 2, 0))

    def test_sibling_before_primary_still_not_untagged(self):
        rows = [row(wake_id="w1"), row(wake_id="w1", outcome="post")]
        y = wake_yield(rows)
        self.assertEqual((y["post"], y["untagged"]), (1, 0))

    def test_untagged_history_and_errors_are_separated(self):
        rows = [row(wake_id="old1"),                       # pre-Phase-0: untagged
                row(),                                     # no wake_id, no outcome
                row(wake_id="e1", outcome="noop", status="error"),  # crash: excluded
                row(wake_id="w1", outcome="noop")]
        y = wake_yield(rows)
        self.assertEqual((y["tagged"], y["untagged"], y["noop"]), (1, 2, 1))

    def test_since_filter_and_non_cycle_rows(self):
        rows = [row(wake_id="w0", outcome="ship", ts="2026-07-01 09:00:00"),
                row(wake_id="w1", outcome="post"),
                {"source": "offload", "ts": "2026-07-11 10:00:00", "outcome": "ship"}]
        y = wake_yield(rows, since_ts="2026-07-10")
        self.assertEqual((y["ship"], y["post"], y["tagged"]), (0, 1, 1))

    def test_render_handles_empty_and_percentages(self):
        self.assertIn("n/a", render_yield(wake_yield([])))
        line = render_yield(wake_yield([row(wake_id="a", outcome="ship"),
                                        row(wake_id="b", outcome="noop")]))
        self.assertIn("50% productive over 2 wakes", line)


if __name__ == "__main__":
    unittest.main()
