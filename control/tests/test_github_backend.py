from __future__ import annotations

import json
import hashlib
import unittest

from sjg_control.authz import Session
from sjg_control.github_backend import GitHubBackend, GitHubError, ProjectConfig
from sjg_control.policy import compile_policy
from test_control_plane import policy_data


class ScriptedGitHub(GitHubBackend):
    def __init__(self, responses, *, project=None):
        super().__init__(compile_policy(policy_data(), source_commit="abc"),
                         executable="/usr/bin/true", environment={"GH_TOKEN": "test"},
                         project=project)
        self.responses = iter(responses)
        self.calls = []

    def _run(self, argv, *, stdin=None):
        self.calls.append((argv, stdin))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return json.dumps(value)


class RepositorySubmissionTests(unittest.TestCase):
    def test_backend_rejects_host_and_proxy_environment_overrides(self):
        policy = compile_policy(policy_data(), source_commit="abc")
        for override in ("GH_HOST", "HTTPS_PROXY", "GH_CONFIG_DIR"):
            with self.subTest(override=override), self.assertRaises(GitHubError):
                GitHubBackend(policy, executable="/usr/bin/true",
                              environment={"GH_TOKEN": "test", override: "evil"})

    def test_repeating_acceptance_text_is_not_completion_evidence(self):
        backend = ScriptedGitHub([])
        issue = {"number": 7, "body": "Done-when: the page returns 200"}
        self.assertFalse(backend._evidence(issue, {"done_when": "the page returns 200"}))

    def test_only_controller_attributed_commercial_demo_is_evidence(self):
        issue = {"number": 7, "body": "Done-when: the page returns 200"}
        digest = hashlib.sha256(b"the page returns 200").hexdigest()
        payload = "\n[DEMO]\naccepted: true\nitem: alice/acme-org#7\nacceptance_digest: " + digest
        reference = "https://github.com/alice/acme-org/issues/7#issuecomment-99"
        marker = "<!-- sjg-idempotency:wake-1234567890-demo -->\n"
        untrusted = ScriptedGitHub([{"body": marker + "**Software Implementer —** " + payload}])
        self.assertFalse(untrusted._evidence(issue, {"demo": reference}))
        accepted = ScriptedGitHub([{"body": marker + "**Commercial Planner —** " + payload}])
        self.assertTrue(accepted._evidence(issue, {"demo": reference}))

    def test_merged_pr_relation_must_be_an_exact_evidence_line(self):
        issue = {"number": 7, "body": "Done-when: complete"}
        disguised = ScriptedGitHub([
            {"merged": True, "body": "Not-Relates-to alice/acme-org#7"}])
        self.assertFalse(disguised._evidence(
            issue, {"pr": "alice/acme-product#3"}))
        exact = ScriptedGitHub([
            {"merged": True, "body": "Summary\n\nRelates-to alice/acme-org#7\n"}])
        self.assertTrue(exact._evidence(issue, {"pr": "alice/acme-product#3"}))

    def test_incomplete_branch_discovery_never_becomes_absence(self):
        backend = ScriptedGitHub([GitHubError("partial page")])
        with self.assertRaisesRegex(GitHubError, "partial page"):
            backend._submit({"repository": "product", "topic": "fix",
                             "base_oid": "a" * 40, "changes": []},
                            "software-implementer", idempotency_key="wake-1234567890-submit")
        self.assertIn("matching-refs", backend.calls[0][0][1])

    def test_project_created_issue_records_target_as_the_only_owner(self):
        backend = ScriptedGitHub([{"number": 12, "html_url": "https://example/12"}])
        session = Session("s", "w", "acme-org", "project-orchestrator",
                          backend.policy.policy_digest, frozenset({"org"}),
                          "2999-01-01T00:00:00Z")
        result = backend.execute("pm.issue.create", {
            "repository": "org", "kind": "epic", "title": "Deliver",
            "target_role": "software-implementer"}, session=session,
            idempotency_key="wake-1234567890-create")
        self.assertEqual("software-implementer", result["owner_role"])
        body = json.loads(backend.calls[0][1])
        self.assertEqual(["epic", "role:software-implementer"], body["labels"])
        self.assertIn('"owner_role":"software-implementer"', body["body"])
        self.assertIn("sjg-idempotency:wake-1234567890-create", body["body"])

    def test_issue_comments_are_fully_paginated(self):
        backend = ScriptedGitHub([
            {"number": 7, "body": "", "labels": []},
            [[{"id": 1, "body": "one"}], [{"id": 2, "body": "two"}]],
        ])
        session = Session("s", "w", "acme-org", "software-implementer",
                          backend.policy.policy_digest, frozenset({"org"}),
                          "2999-01-01T00:00:00Z")
        result = backend.execute("read.issue.comments", {"repository": "org", "id": 7},
                                 session=session, idempotency_key="wake-1234567890-comments")
        self.assertTrue(result["complete"])
        self.assertEqual([1, 2], [item["id"] for item in result["items"]])
        self.assertIn("--paginate", backend.calls[1][0])

    def test_conflicting_kind_labels_fail_closed(self):
        backend = ScriptedGitHub([
            {"number": 7, "body": "", "labels": [{"name": "objective"}, {"name": "story"}]},
        ])
        with self.assertRaisesRegex(GitHubError, "conflicting"):
            backend.facts("pm.issue.comment", {"repository": "org", "id": 7})

    def test_stage_matches_the_configured_project_id_not_owner_scoped_number(self):
        backend = ScriptedGitHub([
            {"data": {"user": {"projectV2": {
                "id": "P_config", "field": {"id": "F_status", "options": [
                    {"id": "O_todo", "name": "Todo"}]}}}, "organization": None}},
            {"data": {"repository": {"issue": {"projectItems": {
                "nodes": [
                    {"project": {"id": "P_other"},
                     "fieldValueByName": {"name": "Done"}},
                    {"project": {"id": "P_config"},
                     "fieldValueByName": {"name": "Todo"}},
                ], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}},
        ], project=ProjectConfig("alice", 7))
        self.assertEqual("Todo", backend._stage({"repository": "org", "id": 9}))


if __name__ == "__main__":
    unittest.main()
