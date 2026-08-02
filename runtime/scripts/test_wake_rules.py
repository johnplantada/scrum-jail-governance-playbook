#!/usr/bin/env python3
"""The routing table is held to the org chart — wake-rules.yaml vs org-chart.yaml.

Adding a role touches seven surfaces in lockstep (the playbook's Flow C), and
wake-rules.yaml is the one whose omission is silent: a role in the chart with no
issue/PR route simply never wakes — the role-level version of the unlabeled-ticket
gap (playbook/patterns.md Pattern 16; org#28 was the issue-level incident). This test makes that
omission a CI failure instead of a quiet dead role.

This is a roster-derived consistency check, not a runtime authorization mechanism.

Reads the repository-root org-chart.yaml and wake-rules.yaml. It needs the stamped-org
layout; inside the golden's bare runtime/ it fails,
and that is expected — a missing routing table must never pass).

Run: PYTHONPATH=scripts python3 scripts/test_wake_rules.py  (CI does this)
"""
import os
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


RULES = _load("wake-rules.yaml").get("rules") or []
ROLES = [d["name"] for d in _load("org-chart.yaml").get("roles") or []]


def _index(match, wake=None):
    """Index of the first rule whose match equals `match` (and wake, if given); -1 if absent."""
    for i, r in enumerate(RULES):
        if r.get("match") == match and (wake is None or r.get("wake") == wake):
            return i
    return -1


class TestEveryRoleRoutes(unittest.TestCase):
    def test_chart_has_roles(self):
        self.assertTrue(ROLES, "org-chart.yaml roles: is empty or unreadable")

    def test_every_role_has_an_issue_route(self):
        for n in ROLES:
            self.assertNotEqual(
                _index({"kind": "issue", "label": f"role:{n}"}), -1,
                f"role:{n} has no issue route — a ticket labeled for it never wakes it")

    def test_every_role_has_a_pr_route(self):
        for n in ROLES:
            self.assertNotEqual(
                _index({"kind": "pr", "label": f"role:{n}"}), -1,
                f"role:{n} has no PR route — a PR labeled for it never wakes it")


class TestCatchAll(unittest.TestCase):
    def test_governance_reviewer_is_chartered(self):
        # The catch-all's target must exist — a rule pointing at a ghost wakes nobody.
        self.assertIn("governance-reviewer", ROLES)

    def test_unlabeled_issue_catch_all_exists_and_wakes_governance_reviewer(self):
        self.assertNotEqual(
            _index({"kind": "issue"}, wake="governance-reviewer"), -1,
            "no {kind: issue} → governance reviewer catch-all: an unlabeled issue is invisible "
            "(playbook/patterns.md Pattern 16)")

    def test_catch_all_comes_after_every_role_issue_rule(self):
        # First matching rule wins: a catch-all ABOVE the role rules would swallow
        # every issue and route the whole org's intake to the governance reviewer.
        catch = _index({"kind": "issue"})
        for n in ROLES:
            role_rule = _index({"kind": "issue", "label": f"role:{n}"})
            self.assertLess(
                role_rule, catch,
                f"catch-all precedes the role:{n} issue rule — it would swallow its wakes")


class TestStructuralRules(unittest.TestCase):
    def test_comment_rule_exists(self):
        self.assertNotEqual(
            _index({"kind": "comment"}, wake="from-label"), -1,
            "no comment → from-label rule: replies on routed issues re-wake nobody")

    def test_pr_repo_defaults_exist(self):
        for repo in ("org", "product"):
            self.assertNotEqual(
                _index({"kind": "pr", "repo": repo}), -1,
                f"no unlabeled-PR default for repo: {repo} — a Human Principal merge there "
                "wakes nobody (the org#13 lesson)")

    def test_labeled_pr_rules_precede_repo_defaults(self):
        # First matching rule wins: a repo default above the labeled rules would
        # override every role:* PR label.
        defaults = [i for i in (_index({"kind": "pr", "repo": "org"}),
                                _index({"kind": "pr", "repo": "product"})) if i != -1]
        for n in ROLES:
            labeled = _index({"kind": "pr", "label": f"role:{n}"})
            for d in defaults:
                self.assertLess(
                    labeled, d,
                    f"the role:{n} PR rule sits below a repo default that shadows it")

    def test_every_literal_wake_target_is_chartered(self):
        # A wake naming a role not in the chart is a route to a ghost — the
        # removed/renamed-role inverse of the missing-route checks above.
        for r in RULES:
            wake = r.get("wake")
            if wake and wake != "from-label":
                self.assertIn(
                    wake, ROLES,
                    f"rule {r.get('match')} wakes '{wake}', which is not in the chart")


if __name__ == "__main__":
    unittest.main(verbosity=0)
