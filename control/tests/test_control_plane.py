from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from sjg_control.authz import AuthorizationError, Facts, Session, authorize
from sjg_control.broker import Broker
from sjg_control.control_backend import ControlBackend
from sjg_control.policy import PolicyError, compile_policy
from sjg_control.provider import (ProviderPolicyError, load_claude_settings,
                                  materialize_claude_wake_settings, parent_command,
                                  parse_auth_evidence, read_subscription_auth,
                                  sanitized_environment,
                                  validate_codex_config, validate_workspace, worker_command)
from sjg_control.store import StateError, Store


def policy_data():
    return {
        "apiVersion": "scrum-jail.dev/v1",
        "kind": "Organization",
        "metadata": {"id": "acme-org"},
        "authority": {"principalRole": "human-principal", "principalGitHub": "alice",
                      "upstreamRole": "portfolio-orchestrator"},
        "repositories": {
            "org": {"slug": "alice/acme-org", "defaultBranch": "main"},
            "product": {"slug": "alice/acme-product", "defaultBranch": "main"},
        },
        "controlRelease": {"version": "1.0.0", "sourceTreeSha256": "a" * 64},
        "budgets": {"organizationDailyTokens": 2000000},
        "limits": {"globalMaxAgents": 6},
        "agents": [
            {"id": "project-orchestrator", "repositories": ["org"],
             "dailyTokenBudget": 300000,
             "workers": {"allowed": [], "maxPerWake": 0, "maxConcurrent": 0}},
            {"id": "commercial-planner", "repositories": ["org"],
             "dailyTokenBudget": 500000,
             "workers": {"allowed": ["researcher", "drafter"], "maxPerWake": 2, "maxConcurrent": 1}},
            {"id": "software-implementer", "repositories": ["org", "product"],
             "dailyTokenBudget": 500000,
             "workers": {"allowed": ["researcher", "drafter", "implementer"], "maxPerWake": 2, "maxConcurrent": 1}},
            {"id": "governance-reviewer", "repositories": ["org"],
             "dailyTokenBudget": 100000,
             "workers": {"allowed": [], "maxPerWake": 0, "maxConcurrent": 0}},
        ],
        "workflows": {"hostedExecution": "manual-only"},
    }


class FakeBackend:
    def __init__(self, facts=Facts()):
        self.current_facts = facts
        self.calls = []
        self.fact_calls = []

    def facts(self, operation, arguments):
        self.fact_calls.append((operation, arguments))
        return self.current_facts

    def execute(self, operation, arguments, *, session, idempotency_key):
        self.calls.append((operation, arguments, session.role, idempotency_key))
        return {"operation": operation}


class FailingBackend(FakeBackend):
    def execute(self, operation, arguments, *, session, idempotency_key):
        self.calls.append((operation, arguments, session.role, idempotency_key))
        raise RuntimeError("connection ended")


class SequencedGitHub(FakeBackend):
    def __init__(self, policy, facts):
        super().__init__()
        self.policy = policy
        self.fact_sequence = list(facts)

    def facts(self, operation, arguments):
        self.fact_calls.append((operation, arguments))
        return self.fact_sequence.pop(0)


class PolicyTests(unittest.TestCase):
    def test_required_roles_and_no_custom_compatibility(self):
        data = policy_data()
        data["agents"] = [r for r in data["agents"] if r["id"] != "software-implementer"]
        with self.assertRaises(PolicyError):
            compile_policy(data, source_commit="abc")
        data = policy_data()
        data["agents"].append({"id": "ceo"})
        with self.assertRaises(PolicyError):
            compile_policy(data, source_commit="abc")

    def test_digest_is_deterministic(self):
        one = compile_policy(policy_data(), source_commit="abc")
        two = compile_policy(policy_data(), source_commit="abc")
        self.assertEqual(one.policy_digest, two.policy_digest)

    def test_unknown_fields_and_capability_widening_fail_closed(self):
        data = policy_data()
        data["compatibility"] = True
        with self.assertRaises(PolicyError):
            compile_policy(data, source_commit="abc")
        data = policy_data()
        data["workflows"]["principalActor"] = "mallory"
        with self.assertRaises(PolicyError):
            compile_policy(data, source_commit="abc")
        data = policy_data()
        data["repositories"]["product"]["slug"] = "../.."
        with self.assertRaises(PolicyError):
            compile_policy(data, source_commit="abc")
        data = policy_data()
        data["agents"][1]["repositories"].append("product")
        with self.assertRaises(PolicyError):
            compile_policy(data, source_commit="abc")
        data = policy_data()
        data["agents"][0]["workers"] = {
            "allowed": ["researcher"], "maxPerWake": 1, "maxConcurrent": 1}
        with self.assertRaises(PolicyError):
            compile_policy(data, source_commit="abc")


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.policy = compile_policy(policy_data(), source_commit="abc")
        self.session = Session("s", "w", "acme-org", "software-implementer",
                               self.policy.policy_digest, frozenset({"org", "product"}),
                               "2999-01-01T00:00:00Z")

    def test_human_only_operations_are_absent(self):
        for operation in ("objective.create", "pr.merge", "workflow.dispatch",
                          "deployment.execute", "spend.execute", "governance.activate"):
            with self.subTest(operation=operation), self.assertRaises(AuthorizationError):
                authorize(self.policy, self.session, operation, {}, Facts())

    def test_external_portfolio_identity_has_no_project_session_capability(self):
        external = Session("p", "w", "acme-org", "portfolio-orchestrator",
                           self.policy.policy_digest, frozenset({"org"}),
                           "2999-01-01T00:00:00Z")
        with self.assertRaises(AuthorizationError):
            authorize(self.policy, external, "read.issue.list", {"repository": "org"}, Facts())

    def test_unknown_or_authority_bearing_arguments_are_denied(self):
        for arguments in (
            {"repository": "org", "issue": 1, "url": "https://api.github.com"},
            {"repository": "org", "issue": 1, "priority": 1},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(AuthorizationError):
                authorize(self.policy, self.session, "read.issue.get", arguments, Facts())

    def test_pm_operations_are_confined_to_the_org_repository(self):
        with self.assertRaisesRegex(AuthorizationError, "org repository"):
            authorize(
                self.policy, self.session, "pm.issue.drop", {"repository": "product"},
                Facts(issue_kind="story", owner_role="software-implementer",
                      has_open_children=False),
            )

    def test_objective_variants_are_denied(self):
        for arguments in (
            {"repository": "org", "kind": "objective", "title": "Grow"},
            {"repository": "org", "kind": "story", "title": "[OBJECTIVE] Grow"},
            {"repository": "org", "kind": "story", "title": "Grow", "labels": ["objective"]},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(AuthorizationError):
                authorize(self.policy, self.session, "pm.issue.create", arguments, Facts())

    def test_delivery_taxonomy_requires_a_complete_typed_parent(self):
        valid = {"repository": "org", "kind": "story", "title": "Implement", "parent": 9,
                 "body": "Done-when: exact evidence"}
        authorize(self.policy, self.session, "pm.issue.create", valid,
                  Facts(complete=True, parent_kind="feature"))
        for facts in (Facts(complete=True), Facts(complete=True, parent_kind="epic"),
                      Facts(complete=False, parent_kind="feature")):
            with self.subTest(facts=facts), self.assertRaises(AuthorizationError):
                authorize(self.policy, self.session, "pm.issue.create", valid, facts)

    def test_delivery_taxonomy_requires_exact_lowercase_kinds(self):
        with self.assertRaisesRegex(AuthorizationError, "canonical lowercase"):
            authorize(
                self.policy, self.session, "pm.issue.create",
                {"repository": "org", "kind": "Feature", "title": "Mixed case",
                 "parent": 9, "body": "Acceptance: exact evidence"},
                Facts(parent_kind="epic"),
            )

    def test_project_created_delivery_work_has_a_real_owner(self):
        project = Session("p", "w", "acme-org", "project-orchestrator",
                          self.policy.policy_digest, frozenset({"org"}),
                          "2999-01-01T00:00:00Z")
        request = {"repository": "org", "kind": "epic", "title": "Deliver", "parent": 1,
                   "target_role": "software-implementer"}
        authorize(self.policy, project, "pm.issue.create", request,
                  Facts(parent_kind="objective"))
        for target in (None, "project-orchestrator", "governance-reviewer"):
            invalid = dict(request)
            if target is None:
                invalid.pop("target_role")
            else:
                invalid["target_role"] = target
            with self.subTest(target=target), self.assertRaises(AuthorizationError):
                authorize(self.policy, project, "pm.issue.create", invalid,
                          Facts(parent_kind="objective"))
        with self.assertRaisesRegex(AuthorizationError, "may not create feature"):
            authorize(
                self.policy, project, "pm.issue.create",
                {"repository": "org", "kind": "feature", "title": "Over-decomposed",
                 "parent": 2, "target_role": "software-implementer",
                 "body": "Acceptance: exact evidence"},
                Facts(parent_kind="epic"),
            )

    def test_feature_and_story_creation_require_evidence_text(self):
        with self.assertRaises(AuthorizationError):
            authorize(self.policy, self.session, "pm.issue.create",
                      {"repository": "org", "kind": "story", "title": "No bar", "parent": 1},
                      Facts(parent_kind="feature"))

    def test_empty_issue_and_pr_mutations_are_denied(self):
        with self.assertRaises(AuthorizationError):
            authorize(self.policy, self.session, "pm.issue.create",
                      {"repository": "org", "kind": "story", "title": "", "parent": 1,
                       "body": "Done-when: exact evidence"}, Facts(parent_kind="feature"))
        with self.assertRaises(AuthorizationError):
            authorize(self.policy, self.session, "pr.create",
                      {"repository": "product", "topic": "empty", "title": ""}, Facts())
        with self.assertRaises(AuthorizationError):
            authorize(self.policy, self.session, "pr.comment",
                      {"repository": "product", "pr": 1, "body": ""},
                      Facts(pr_open=True, pr_head_role="software-implementer"))

    def test_incomplete_reads_never_authorize_mutation(self):
        with self.assertRaises(AuthorizationError):
            authorize(self.policy, self.session, "pm.issue.done", {"repository": "org"},
                      Facts(complete=False))

    def test_cross_owner_and_unbound_evidence_are_denied(self):
        with self.assertRaises(AuthorizationError):
            authorize(self.policy, self.session, "pm.issue.done", {"repository": "org"},
                      Facts(issue_kind="story", owner_role="commercial-planner",
                            has_open_children=False, evidence_valid=True))
        with self.assertRaises(AuthorizationError):
            authorize(self.policy, self.session, "pm.issue.done", {"repository": "org"},
                      Facts(issue_kind="story", owner_role="software-implementer",
                            has_open_children=False, evidence_valid=False))

    def test_repeating_done_when_is_not_closure_evidence(self):
        with self.assertRaisesRegex(AuthorizationError, "unknown arguments"):
            authorize(
                self.policy, self.session, "pm.issue.done",
                {"repository": "org", "done_when": "the acceptance line"},
                Facts(issue_kind="story", owner_role="software-implementer",
                      has_open_children=False, evidence_valid=True),
            )

    def test_routing_cannot_steal_owned_work(self):
        governance = Session("s", "w", "acme-org", "governance-reviewer",
                             self.policy.policy_digest, frozenset({"org"}),
                             "2999-01-01T00:00:00Z")
        authorize(self.policy, governance, "pm.issue.route",
                  {"repository": "org", "target_role": "commercial-planner"},
                  Facts(issue_kind="story", owner_role=None))
        with self.assertRaises(AuthorizationError):
            authorize(self.policy, governance, "pm.issue.route",
                      {"repository": "org", "target_role": "commercial-planner"},
                      Facts(issue_kind="story", owner_role="software-implementer"))
        with self.assertRaises(AuthorizationError):
            authorize(self.policy, self.session, "pm.issue.route",
                      {"repository": "org", "target_role": "software-implementer"}, Facts())
        authorize(self.policy, governance, "pm.issue.route",
                  {"repository": "org", "target_role": "project-orchestrator"},
                  Facts(issue_kind="objective", owner_role=None))
        with self.assertRaises(AuthorizationError):
            authorize(self.policy, governance, "pm.issue.route",
                      {"repository": "org", "target_role": "software-implementer"},
                      Facts(issue_kind="objective", owner_role=None))

    def test_branch_is_derived_and_protected_paths_are_denied(self):
        import base64
        import hashlib
        content = b"ok\n"
        valid = {"repository": "product", "topic": "login-fix", "changes": [
            {"operation": "upsert", "path": "src/login.py", "mode": "100644",
             "sha256": hashlib.sha256(content).hexdigest(),
             "content_base64": base64.b64encode(content).decode()}],
                 "base_oid": "a" * 40}
        authorize(self.policy, self.session, "repo.submit", valid, Facts())
        for message in ("Fixes alice/acme-org#7", "Fixes: alice/acme-org#7"):
            with self.subTest(message=message), self.assertRaisesRegex(
                    AuthorizationError, "commit message"):
                authorize(self.policy, self.session, "repo.submit",
                          {**valid, "message": message}, Facts())
        for path in (".github/workflows/ci.yml", ".github/actions/x/action.yml",
                     "pkg/action.yml", "../escape"):
            bad = dict(valid, changes=[{"operation": "upsert", "path": path, "mode": "100644",
                                        "sha256": hashlib.sha256(content).hexdigest(),
                                        "content_base64": base64.b64encode(content).decode()}])
            with self.subTest(path=path), self.assertRaises(AuthorizationError):
                authorize(self.policy, self.session, "repo.submit", bad, Facts())

    def test_pr_closing_keywords_and_approve_are_denied(self):
        for text in ("Fixes alice/acme-org#7",
                     "Fixes: alice/acme-org#7",
                     "Closes https://github.com/alice/acme-org/issues/7"):
            with self.subTest(text=text), self.assertRaises(AuthorizationError):
                authorize(self.policy, self.session, "pr.create",
                          {"repository": "product", "title": text, "topic": "closure-bypass"},
                          Facts())
        with self.assertRaises(AuthorizationError):
            authorize(self.policy, self.session, "pr.review",
                      {"repository": "product", "event": "APPROVE"},
                      Facts(pr_open=True, pr_head_role="software-implementer"))

    def test_pull_request_reviews_are_independent_and_non_authorizing(self):
        governance = Session("g", "w", "acme-org", "governance-reviewer",
                             self.policy.policy_digest, frozenset({"org"}),
                             "2999-01-01T00:00:00Z")
        review = {"repository": "org", "pr": 7, "body": "Evidence-backed hold",
                  "event": "REQUEST_CHANGES"}
        authorize(self.policy, governance, "pr.review", review,
                  Facts(pr_open=True, pr_head_role="software-implementer"))
        for session, head, event in (
            (governance, "governance-reviewer", "COMMENT"),
            (self.session, "commercial-planner", "COMMENT"),
            (governance, "software-implementer", "APPROVE"),
        ):
            with self.subTest(role=session.role, head=head, event=event), self.assertRaises(
                    AuthorizationError):
                authorize(self.policy, session, "pr.review",
                          {**review, "event": event}, Facts(pr_open=True, pr_head_role=head))

    def test_worker_scope_is_validated_before_execution(self):
        valid = {"repository": "product", "type": "implementer", "task": "edit",
                 "readPaths": ["src/app.py"], "writePaths": ["src/app.py"]}
        authorize(self.policy, self.session, "worker.run", valid, Facts())
        for change in (
            {"readPaths": ["src//app.py"]},
            {"writePaths": ["other.py"]},
            {"task": ""},
            {"model": "caller-choice"},
        ):
            invalid = {**valid, **change}
            with self.subTest(change=change), self.assertRaises(AuthorizationError):
                authorize(self.policy, self.session, "worker.run", invalid, Facts())


class StoreAndBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state.sqlite")
        self.policy = compile_policy(policy_data(), source_commit="abc")
        self.session = self.store.create_session(
            org_id=self.policy.org_id, role="software-implementer",
            policy_digest=self.policy.policy_digest, workspace_aliases={"org", "product"},
            global_cap=self.policy.global_max_agents)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_cumulative_worker_cap_does_not_reset_on_finish(self):
        first = self.store.reserve_worker(self.session.session_id, "researcher",
                                          cumulative_cap=1, concurrent_cap=1, global_cap=8)
        self.store.connection.execute(
            "UPDATE worker_attempt SET state='succeeded',finished_at='now' WHERE attempt_id=?", (first,))
        with self.assertRaises(StateError):
            self.store.reserve_worker(self.session.session_id, "researcher",
                                      cumulative_cap=1, concurrent_cap=1, global_cap=8)

    def test_parent_singleflight_and_global_agent_cap(self):
        with self.assertRaises(StateError):
            self.store.create_session(
                org_id=self.policy.org_id, role="software-implementer",
                policy_digest=self.policy.policy_digest, workspace_aliases={"org", "product"},
                global_cap=8)
        tiny = Store(Path(self.temp.name) / "tiny.sqlite")
        try:
            tiny.create_session(org_id="tiny", role="one", policy_digest="p",
                                workspace_aliases={"org"}, global_cap=1)
            with self.assertRaises(StateError):
                tiny.create_session(org_id="tiny", role="two", policy_digest="p",
                                    workspace_aliases={"org"}, global_cap=1)
        finally:
            tiny.close()

    def test_global_cap_counts_parent_and_active_workers(self):
        first = self.store.reserve_worker(self.session.session_id, "researcher",
                                          cumulative_cap=2, concurrent_cap=2, global_cap=2)
        self.assertTrue(first)
        with self.assertRaises(StateError):
            self.store.reserve_worker(self.session.session_id, "drafter",
                                      cumulative_cap=2, concurrent_cap=2, global_cap=2)

    def test_broker_idempotency_executes_once(self):
        backend = FakeBackend(Facts())
        broker = Broker(self.policy, self.store, backend)
        request = {"schema": "sjg.request/v1", "idempotency_key": "wake-1234567890-read",
                   "operation": "read.issue.get", "arguments": {"repository": "org", "issue": 1}}
        self.assertEqual("ok", broker.handle(self.session.session_id, request)["status"])
        self.assertEqual("ok", broker.handle(self.session.session_id, request)["status"])
        self.assertEqual(1, len(backend.calls))

    def test_mutation_failure_is_indeterminate_and_same_key_never_reexecutes(self):
        backend = FailingBackend(Facts(complete=True))
        broker = Broker(self.policy, self.store, backend)
        request = {"schema": "sjg.request/v1", "idempotency_key": "wake-1234567890-comment",
                   "operation": "pm.issue.comment",
                   "arguments": {"repository": "org", "id": 1, "body": "hello"}}
        self.assertEqual("indeterminate", broker.handle(self.session.session_id, request)["status"])
        self.assertEqual("indeterminate", broker.handle(self.session.session_id, request)["status"])
        self.assertEqual(1, len(backend.calls))

    def test_remote_preconditions_are_revalidated_immediately_before_mutation(self):
        permitted = Facts(complete=True, issue_kind="story",
                          owner_role="software-implementer", has_open_children=False,
                          evidence_valid=True)
        changed = Facts(complete=True, issue_kind="story",
                        owner_role="commercial-planner", has_open_children=False,
                        evidence_valid=True)
        github = SequencedGitHub(self.policy, [permitted, changed])
        backend = ControlBackend(github, None, lambda _session, _repository: self.temp.name)
        broker = Broker(self.policy, self.store, backend)
        request = {"schema": "sjg.request/v1",
                   "idempotency_key": "wake-1234567890-precondition",
                   "operation": "pm.issue.done",
                   "arguments": {"repository": "org", "id": 7, "pr": "alice/acme-product#3"}}
        self.assertEqual("denied", broker.handle(self.session.session_id, request)["status"])
        self.assertEqual("denied", broker.handle(self.session.session_id, request)["status"])
        self.assertEqual(2, len(github.fact_calls))
        self.assertEqual([], github.calls)
        state = self.store.connection.execute(
            "SELECT state FROM broker_request WHERE idempotency_key=?",
            (request["idempotency_key"],),
        ).fetchone()[0]
        self.assertEqual("failed", state)

    def test_read_failure_can_retry_with_the_same_key(self):
        backend = FailingBackend(Facts())
        broker = Broker(self.policy, self.store, backend)
        request = {"schema": "sjg.request/v1", "idempotency_key": "wake-1234567890-readfail",
                   "operation": "read.issue.get",
                   "arguments": {"repository": "org", "issue": 1}}
        self.assertEqual("retryable", broker.handle(self.session.session_id, request)["status"])
        self.assertEqual("retryable", broker.handle(self.session.session_id, request)["status"])
        self.assertEqual(2, len(backend.calls))

    def test_unauthorized_scope_is_denied_before_external_facts(self):
        backend = FakeBackend(Facts(complete=True))
        broker = Broker(self.policy, self.store, backend)
        request = {"schema": "sjg.request/v1", "idempotency_key": "wake-1234567890-crossrepo",
                   "operation": "pm.issue.comment",
                   "arguments": {"repository": "outside", "id": 1, "body": "hello"}}
        self.assertEqual("denied", broker.handle(self.session.session_id, request)["status"])
        self.assertEqual([], backend.fact_calls)


class ProviderTests(unittest.TestCase):
    def test_subscription_only_authentication(self):
        evidence = parse_auth_evidence("codex-cli", "Logged in using ChatGPT\n",
                                       cli_version="0.146.0")
        self.assertEqual("chatgpt", evidence.method)
        parse_auth_evidence(
            "claude-cli", json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                                      "apiProvider": "firstParty", "email": "a@example.test",
                                      "orgId": "o", "orgName": "Acme",
                                      "subscriptionType": "max"}), cli_version="2.1.212")
        for provider, raw in (
            ("codex-cli", '{"loggedIn":true,"authMode":"api_key","apiKeyPresent":true}'),
            ("claude-cli", '{"loggedIn":true,"authMethod":"api_key","apiProvider":"firstParty"}'),
            ("claude-cli", '{"loggedIn":true,"authMethod":"oauth","apiProvider":"firstParty"}'),
            ("claude-cli", '{"loggedIn":true,"authMethod":"oauth","apiProvider":"bedrock"}'),
        ):
            with self.subTest(provider=provider), self.assertRaises(ProviderPolicyError):
                parse_auth_evidence(provider, raw, cli_version="1")

    def test_auth_check_rejects_ambient_provider_overrides_before_execution(self):
        with self.assertRaisesRegex(ProviderPolicyError, "forbidden overrides"):
            read_subscription_auth(
                "codex-cli", "/usr/bin/true", allowed_versions={"1.0.0"},
                environment={"OPENAI_API_KEY": "secret"})

    def test_environment_is_allowlisted(self):
        env = sanitized_environment({"PATH": "/workspace/bin", "SHELL": "/workspace/sh",
                                     "GH_TOKEN": "secret", "UNRELATED": "x"},
                                    managed_home="/sealed/home")
        self.assertEqual({"PATH": "/usr/bin:/bin", "SHELL": "/bin/sh",
                          "HOME": "/sealed/home"}, env)
        with self.assertRaises(ProviderPolicyError):
            sanitized_environment({}, managed_home="/sealed/home",
                                  additions={"CLAUDE_CONFIG_DIR": "/workspace/override"})
        with self.assertRaises(ProviderPolicyError):
            sanitized_environment({}, managed_home="/sealed/home",
                                  additions={"SJG_WAKE_SOCKET": "relative.sock"})

    def test_claude_launch_has_redundant_headless_restrictions(self):
        root = Path(__file__).resolve().parents[1] / "provider-policy/claude"
        with tempfile.TemporaryDirectory() as workspace:
            command = parent_command("claude-cli", "/usr/bin/claude", model="luna",
                                     workspace=workspace, managed_root=str(root),
                                     settings_path=str(root / "parent-settings.json"))
        for flag in ("--safe-mode", "--strict-mcp-config", "--no-chrome",
                     "--disable-slash-commands", "--no-session-persistence",
                     "--disallowedTools"):
            self.assertIn(flag, command)
        self.assertNotIn("--dangerously-skip-permissions", command)

    def test_codex_launch_uses_only_the_sealed_profile_and_fixed_sandbox(self):
        root = Path(__file__).resolve().parents[1] / "provider-policy/codex"
        with tempfile.TemporaryDirectory() as workspace:
            command = parent_command("codex-cli", "/usr/bin/codex", model="luna",
                                     workspace=workspace, managed_root=str(root))
        self.assertIn("--strict-config", command)
        self.assertIn("--ephemeral", command)
        self.assertEqual("workspace-write", command[command.index("--sandbox") + 1])
        self.assertEqual("never", command[command.index("--ask-for-approval") + 1])
        self.assertLess(command.index("--ask-for-approval"), command.index("exec"))
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)

    def test_codex_execpolicy_content_is_sealed(self):
        source = Path(__file__).resolve().parents[1] / "provider-policy/codex"
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "codex"
            shutil.copytree(source, copied)
            validate_codex_config(copied / "config.toml")
            (copied / "rules/default.rules").write_text("# weakened\n", encoding="utf-8")
            with self.assertRaisesRegex(ProviderPolicyError, "sealed policy"):
                validate_codex_config(copied / "config.toml")

    def test_workspace_rejects_secret_config_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "safe.txt").write_text("safe\n", encoding="utf-8")
            validate_workspace(root)
            (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            with self.assertRaises(ProviderPolicyError):
                validate_workspace(root)
            (root / ".env").unlink()
            (root / "escape").symlink_to("/etc/passwd")
            with self.assertRaises(ProviderPolicyError):
                validate_workspace(root)

    def test_named_worker_launch_is_nonrecursive_and_profile_scoped(self):
        provider_root = Path(__file__).resolve().parents[1] / "provider-policy"
        with tempfile.TemporaryDirectory() as workspace:
            readonly = worker_command(
                "codex-cli", "/usr/bin/codex", model="luna", workspace=workspace,
                managed_root=str(provider_root / "codex"), worker_type="researcher")
            self.assertEqual("read-only", readonly[readonly.index("--sandbox") + 1])
            implementer = worker_command(
                "claude-cli", "/usr/bin/claude", model="luna", workspace=workspace,
                managed_root=str(provider_root / "claude"), worker_type="implementer")
            self.assertEqual("Read,Glob,Grep,Edit,Write",
                             implementer[implementer.index("--tools") + 1])
            self.assertIn("Agent,Task,Bash,WebSearch,WebFetch", implementer)

    def test_claude_settings_validation_and_single_wake_socket(self):
        root = Path(__file__).resolve().parents[1] / "provider-policy/claude"
        load_claude_settings(root / "parent-settings.json")
        with tempfile.TemporaryDirectory() as temporary:
            generated = materialize_claude_wake_settings(
                root / "parent-settings.json", Path(temporary) / "wake.json",
                wake_socket="/private/run/sjg/wake.sock")
            data = load_claude_settings(generated)
            self.assertEqual(["/private/run/sjg/wake.sock"],
                             data["sandbox"]["network"]["allowUnixSockets"])
            data["sandbox"]["allowUnsandboxedCommands"] = True
            with self.assertRaises(ProviderPolicyError):
                from sjg_control.provider import validate_claude_settings
                validate_claude_settings(data, profile="parent")


if __name__ == "__main__":
    unittest.main()
