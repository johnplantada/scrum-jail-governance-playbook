#!/usr/bin/env python3
"""Unit tests for the subagent gate's pure helpers, plus the org-chart concurrency
invariant that makes `global_max_agents` a CI-checked number instead of dead config.

Run: PYTHONPATH=scripts python3 scripts/test_subagent_gate.py  (CI does this)
"""
import os
import tempfile
import unittest

from subagent_gate import decide, deny_payload, find_cap, read_count, write_count

CHART = [
    {"name": "ceo", "envelope": {"max_subagents": 0}, "teams": []},
    {"name": "business", "envelope": {"max_subagents": 4},
     "teams": [{"name": "growth", "envelope": {"max_subagents": 2}}]},
    {"name": "bare"},  # node with no envelope at all
]


class TestFindCap(unittest.TestCase):
    def test_explicit_zero_is_zero_not_missing(self):
        self.assertEqual(find_cap(CHART, "ceo"), 0)

    def test_department_and_nested_team(self):
        self.assertEqual(find_cap(CHART, "business"), 4)
        self.assertEqual(find_cap(CHART, "growth"), 2)

    def test_node_without_envelope_gets_documented_default(self):
        self.assertEqual(find_cap(CHART, "bare"), 0)

    def test_unknown_node_is_none(self):
        self.assertIsNone(find_cap(CHART, "warden"))
        self.assertIsNone(find_cap(None, "ceo"))


class TestDecide(unittest.TestCase):
    def test_cap_zero_denies_first_spawn(self):
        self.assertEqual(decide(0, 0), "deny")

    def test_under_cap_allows(self):
        self.assertEqual(decide(3, 4), "allow")

    def test_at_cap_denies(self):
        self.assertEqual(decide(4, 4), "deny")


class TestDenyPayload(unittest.TestCase):
    def test_carries_both_hook_contract_shapes(self):
        p = deny_payload("it", 4, 4)
        self.assertEqual(p["decision"], "block")
        self.assertEqual(p["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("4/4", p["reason"])
        self.assertIn("decisions.yaml", p["reason"])


class TestCounterIO(unittest.TestCase):
    def test_round_trip_and_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state", "subagents-w-test.count")
            self.assertEqual(read_count(path), 0)  # missing file = no spawns yet
            write_count(path, 3)
            self.assertEqual(read_count(path), 3)


class TestChartConcurrencyInvariant(unittest.TestCase):
    """The live chart must keep its promise: every brain plus its full permitted
    fan-out fits under limits.global_max_agents. This is what makes the global
    ceiling code-checked configuration rather than a number nothing reads."""

    def test_roster_fits_global_ceiling(self):
        import yaml
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo, "org-chart.yaml"), encoding="utf-8") as fh:
            chart = yaml.safe_load(fh)
        ceiling = int(chart["limits"]["global_max_agents"])

        def walk(depts):
            total = 0
            for d in depts or []:
                env = d.get("envelope") or {}
                self.assertIn("max_subagents", env,
                              f"{d.get('name')}: every node needs an explicit max_subagents "
                              f"(subagent_gate.py enforces it; implicit defaults hide intent)")
                total += 1 + int(env["max_subagents"]) + walk(d.get("teams"))
            return total

        roster = walk(chart.get("departments"))
        self.assertLessEqual(
            roster, ceiling,
            f"org-chart promises {roster} concurrent agents (brains + max fan-out) but "
            f"limits.global_max_agents is {ceiling} — raise the ceiling by decisions.yaml "
            f"PR or shrink an envelope")


if __name__ == "__main__":
    unittest.main(verbosity=1)
