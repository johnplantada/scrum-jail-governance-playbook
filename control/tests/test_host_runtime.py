from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from dataclasses import replace
from pathlib import Path

from sjg_control.authz import Facts
from sjg_control.delivery import Page, SOURCE_IDS
from sjg_control.host_config import (GitHubHostConfig, HostConfigError, ProviderHostConfig,
                                     SupervisorConfig, load_supervisor_config,
                                     validate_secret_file)
from sjg_control.host_process import (HostProcessError, HostProviderLauncher, ProcessResult,
                                      _terminate_group, extract_usage)
from sjg_control.governance import GovernanceError, GovernanceMonitor
from sjg_control.policy import compile_policy
from sjg_control.protocol import request
from sjg_control.routing import (RoutingError, load_wake_rules, normalize_event, route_event,
                                 wake_rules_digest)
from sjg_control.sources import GitHubSource
from sjg_control.service import AdminServer, ServiceError
from sjg_control.store import Store
from sjg_control.supervisor import Supervisor, SupervisorError
from sjg_control.verification import GitHubOperationVerifier, resolve_pending


def policy_data() -> dict:
    return {
        "apiVersion": "scrum-jail.dev/v1", "kind": "Organization",
        "metadata": {"id": "host-test"},
        "authority": {"principalRole": "human-principal", "principalGitHub": "alice",
                      "upstreamRole": "portfolio-orchestrator"},
        "repositories": {
            "org": {"slug": "alice/host-test", "defaultBranch": "main"},
            "product": {"slug": "alice/product", "defaultBranch": "main"},
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
             "workers": {"allowed": ["researcher", "drafter"],
                         "maxPerWake": 2, "maxConcurrent": 1}},
            {"id": "software-implementer", "repositories": ["org", "product"],
             "dailyTokenBudget": 500000,
             "workers": {"allowed": ["researcher", "drafter", "implementer"],
                         "maxPerWake": 2, "maxConcurrent": 1}},
            {"id": "governance-reviewer", "repositories": ["org"],
             "dailyTokenBudget": 100000,
             "workers": {"allowed": [], "maxPerWake": 0, "maxConcurrent": 0}},
        ],
        "workflows": {"hostedExecution": "manual-only"},
    }


RULES = """rules:
  - match: {kind: issue, label: "role:software-implementer"}
    wake: from-label
  - match: {kind: issue}
    wake: governance-reviewer
  - match: {kind: comment}
    wake: from-label
  - match: {kind: pr, repo: product}
    wake: software-implementer
  - match: {kind: run, repo: product, workflow: deploy.yml}
    wake: software-implementer
"""


class RoutingAndSourceTests(unittest.TestCase):
    def setUp(self):
        self.policy = compile_policy(policy_data(), source_commit="host")

    def test_rules_are_digest_bound_and_route_only_canonical_roles(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wake-rules.yaml"
            path.write_text(RULES, encoding="utf-8")
            rules = load_wake_rules(path, self.policy,
                                    expected_sha256=wake_rules_digest(path))
            event = normalize_event("org-issues", {
                "number": 1, "state": "open",
                "labels": [{"name": "role:software-implementer"}],
            })
            self.assertEqual("software-implementer", route_event(event, rules, self.policy))
            path.write_text(RULES + "# drift\n", encoding="utf-8")
            with self.assertRaisesRegex(RoutingError, "commissioned digest"):
                load_wake_rules(path, self.policy,
                                expected_sha256=hashlib.sha256(RULES.encode()).hexdigest())

    def test_comment_pr_and_workflow_sources_are_enriched_before_routing(self):
        class Transport:
            def api(self, endpoint, *, fields=None):
                if endpoint.endswith("issues/comments"):
                    return [{"id": 9, "updated_at": "2026-01-01T01:00:00Z",
                             "issue_url": "https://api.github.com/repos/alice/host-test/issues/4"}]
                if endpoint.endswith("issues/4"):
                    return {"number": 4, "labels": [{"name": "role:commercial-planner"}]}
                if endpoint.endswith("/issues"):
                    return [{"number": 7, "updated_at": "2026-01-01T01:00:00Z",
                             "state": "closed", "labels": [], "pull_request": {
                                 "url": "https://api.github.com/repos/alice/product/pulls/7"}}]
                if endpoint.endswith("pulls/7"):
                    return {"number": 7, "merged_at": "2026-01-01T01:00:00Z"}
                if endpoint.endswith("actions/runs"):
                    return {"workflow_runs": [{"id": 8, "workflow_id": 3,
                                               "updated_at": "2026-01-01T01:00:00Z",
                                               "status": "completed", "conclusion": "success"}]}
                if endpoint.endswith("actions/workflows/3"):
                    return {"path": ".github/workflows/deploy.yml"}
                raise AssertionError(endpoint)

        transport = Transport()
        comment = GitHubSource("org-comments", self.policy, transport).fetch_page(
            watermark="2026-01-01T00:00:00Z", page_cursor=None, etag=None).items[0]
        self.assertEqual({"role:commercial-planner"},
                         set(normalize_event("org-comments", comment).labels))
        pull = GitHubSource("product-issues", self.policy, transport).fetch_page(
            watermark="2026-01-01T00:00:00Z", page_cursor=None, etag=None).items[0]
        self.assertEqual("merged", normalize_event("product-issues", pull).pr_state)
        run = GitHubSource("product-workflow-runs", self.policy, transport).fetch_page(
            watermark="2026-01-01T00:00:00Z", page_cursor=None, etag=None).items[0]
        self.assertEqual("deploy.yml", normalize_event("product-workflow-runs", run).workflow)


class HostConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.policy = compile_policy(policy_data(), source_commit="host")

    def test_configuration_is_exact_separated_and_secret_file_is_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            organization = root / "organization.yaml"
            organization.write_text(json.dumps(policy_data()), encoding="utf-8")
            rules = root / "wake-rules.yaml"
            rules.write_text(RULES, encoding="utf-8")
            token = root / "github-token"
            token.write_text("ghs_" + "x" * 36, encoding="utf-8")
            token.chmod(0o600)
            home = root / "provider-home"
            policy_root = root / "provider-policy"
            github_home = root / "github-home"
            host = root / "host"
            for directory in (home, policy_root, github_home, host):
                directory.mkdir()
            workspaces = {}
            for role, role_policy in self.policy.roles.items():
                workspaces[role] = {}
                for alias in role_policy.repositories:
                    workspace = root / "workspaces" / role / alias
                    workspace.mkdir(parents=True)
                    workspaces[role][alias] = str(workspace)
            config = {
                "organization": str(organization), "wakeRules": str(rules),
                "wakeRulesSha256": wake_rules_digest(rules),
                "stateDatabase": str(host / "state.sqlite"),
                "adminSocket": str(host / "admin/admin.sock"),
                "wakeSocketDirectory": str(host / "wakes"),
                "scratchDirectory": str(host / "scratch"), "workspaces": workspaces,
                "github": {"executable": "/usr/bin/true", "tokenFile": str(token),
                           "home": str(github_home), "projectOwner": None, "projectNumber": None},
                "provider": {"kind": "claude-cli", "executable": "/usr/bin/true",
                             "managedHome": str(home), "policyRoot": str(policy_root),
                             "allowedVersions": ["1.0.0"],
                             "roleModels": {role: "model" for role in self.policy.roles},
                             "workerModels": {name: "model" for name in
                                              ("researcher", "drafter", "implementer")},
                             "timeoutSeconds": 60},
                "operatorUid": os.getuid(), "agentUid": os.getuid() + 1,
                "agentGid": max(os.getgid(), 1), "mode": "shadow", "pollIntervalSeconds": 60,
                "sessionTtlSeconds": 300, "deliveryLeaseSeconds": 300,
                "roleTokenReservations": {role: 1000 for role in self.policy.roles},
                "initialWatermark": "2026-01-01T00:00:00Z",
            }
            path = root / "supervisor.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            loaded = load_supervisor_config(path, self.policy)
            self.assertEqual("shadow", loaded.mode)
            validate_secret_file(token)
            token.chmod(0o640)
            with self.assertRaisesRegex(HostConfigError, "group or other"):
                validate_secret_file(token)
            config["operatorUid"] = config["agentUid"]
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(HostConfigError, "different OS UIDs"):
                load_supervisor_config(path, self.policy)
            config["operatorUid"] = os.getuid()
            config["agentUid"] = os.getuid() + 1
            config["github"]["home"] = next(
                iter(next(iter(workspaces.values())).values()))
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(HostConfigError, "outside agent workspaces"):
                load_supervisor_config(path, self.policy)

    def test_machine_readable_usage_is_required_and_not_double_counted(self):
        output = "\n".join((
            json.dumps({"type": "progress", "usage": {"input_tokens": 10,
                                                        "output_tokens": 2}}),
            json.dumps({"type": "result", "usage": {"input_tokens": 25,
                                                      "output_tokens": 5}}),
        ))
        self.assertEqual(30, extract_usage("claude-cli", output))
        self.assertIsNone(extract_usage("codex-cli", "not-json"))


class EmptySource:
    def fetch_page(self, *, watermark, page_cursor, etag):
        return Page([], None)


class EventSource:
    def fetch_page(self, *, watermark, page_cursor, etag):
        return Page([{"number": 1, "state": "open", "updated_at": "2026-01-01T01:00:00Z",
                      "labels": [{"name": "role:software-implementer"}]}], None)


class TwoEventSource:
    def fetch_page(self, *, watermark, page_cursor, etag):
        return Page([
            {"number": number, "state": "open",
             "updated_at": f"2026-01-01T0{number}:00:00Z",
             "labels": [{"name": "role:software-implementer"}]}
            for number in (1, 2)
        ], None)


class FakeGitHub:
    def __init__(self, policy):
        self.policy = policy

    def facts(self, operation, arguments):
        return Facts()

    def execute(self, operation, arguments, *, session, idempotency_key):
        return {"items": [], "complete": True, "role": session.role}


class FakeLauncher:
    def __init__(self):
        self.requests = []

    def run_parent(self, *, session_id, wake_id, role, workspace, socket_path, prompt,
                   on_spawned=None, on_started=None):
        if on_spawned:
            on_spawned()
        if on_started:
            on_started()
        response = request(socket_path, {
            "schema": "sjg.request/v1", "idempotency_key": "wake-host-runtime-read-0001",
            "operation": "read.issue.list", "arguments": {"repository": "org"},
        })
        self.requests.append(response)
        output = json.dumps({"type": "result", "usage": {"input_tokens": 7,
                                                            "output_tokens": 3}})
        return ProcessResult(output, "", 0, 10, hashlib.sha256(output.encode()).hexdigest())

    def take_worker_usage(self, session_id):
        return 0

    def terminate_session(self, session_id):
        return True


class UncertainSpawnLauncher(FakeLauncher):
    def run_parent(self, *, session_id, wake_id, role, workspace, socket_path, prompt,
                   on_spawned=None, on_started=None):
        del session_id, wake_id, role, workspace, socket_path, prompt, on_started
        if on_spawned:
            on_spawned()
        raise HostProcessError("durable process identity could not be proved")

    def terminate_session(self, session_id):
        del session_id
        return False


class SupervisorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.policy = compile_policy(policy_data(), source_commit="host")
        rules = self.root / "wake-rules.yaml"
        rules.write_text(RULES, encoding="utf-8")
        workspaces = {}
        for role, role_policy in self.policy.roles.items():
            workspaces[role] = {}
            for alias in role_policy.repositories:
                workspace = self.root / "workspaces" / role / alias
                (workspace / "agents").mkdir(parents=True)
                (workspace / "agents/_policy.md").write_text("Shared policy", encoding="utf-8")
                (workspace / f"agents/{role}.md").write_text("Role mandate", encoding="utf-8")
                workspaces[role][alias] = str(workspace)
        provider = ProviderHostConfig(
            "codex-cli", "/usr/bin/true", str(self.root / "home"),
            str(self.root / "policy"), frozenset({"1.0.0"}),
            {role: "model" for role in self.policy.roles},
            {name: "model" for name in ("researcher", "drafter", "implementer")}, 60)
        for directory in (Path(provider.managed_home), Path(provider.policy_root)):
            directory.mkdir()
        self.config = SupervisorConfig(
            str(self.root / "organization.yaml"), str(rules), wake_rules_digest(rules),
            str(self.root / "host/state.sqlite"), str(self.root / "host/admin/admin.sock"),
            str(self.root / "host/wakes"), str(self.root / "host/scratch"), workspaces,
            GitHubHostConfig("/usr/bin/true", str(self.root / "token"), str(self.root),
                             None, None), provider, os.getuid() + 1, os.getuid(), os.getgid(),
            "live", 60, 300, 300, {role: 1000 for role in self.policy.roles},
            "2026-01-01T00:00:00Z")
        sources = {source_id: EmptySource() for source_id in SOURCE_IDS}
        sources["org-issues"] = EventSource()
        self.launcher = FakeLauncher()
        self.supervisor = Supervisor(
            self.config, self.policy, github_backend=FakeGitHub(self.policy),
            sources=sources, launcher=self.launcher)

    def tearDown(self):
        self.temp.cleanup()

    def _require_unix_sockets(self):
        probe_path = self.root / "host/wakes/socket-probe"
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.bind(str(probe_path))
        except PermissionError:
            self.skipTest("filesystem Unix sockets are unavailable in this test sandbox")
        finally:
            probe.close()
            try:
                probe_path.unlink()
            except FileNotFoundError:
                pass

    def test_live_tick_routes_runs_and_settles_one_wake_end_to_end(self):
        self._require_unix_sockets()
        result = self.supervisor.tick()
        self.assertEqual("succeeded", result["wakes"][0]["status"])
        self.assertEqual("ok", self.launcher.requests[0]["status"], self.launcher.requests[0])
        store = Store(self.config.state_database)
        try:
            self.assertEqual("succeeded", store.connection.execute(
                "SELECT state FROM delivery").fetchone()[0])
            self.assertEqual("finished", store.connection.execute(
                "SELECT state FROM wake_session").fetchone()[0])
            self.assertEqual("settled", store.connection.execute(
                "SELECT state FROM budget_reservation").fetchone()[0])
        finally:
            store.close()

    def test_shadow_tick_does_not_advance_live_state(self):
        shadow_config = replace(self.config, mode="shadow")
        shadow = Supervisor(
            shadow_config, self.policy, github_backend=FakeGitHub(self.policy),
            sources=self.supervisor.sources, launcher=self.launcher)
        result = shadow.tick()
        self.assertEqual("preview", result["mode"])
        store = Store(self.config.state_database)
        try:
            self.assertEqual(0, store.connection.execute("SELECT count(*) FROM delivery").fetchone()[0])
            self.assertEqual(0, store.connection.execute(
                "SELECT count(*) FROM source_checkpoint").fetchone()[0])
        finally:
            store.close()

    def test_shadow_mode_blocks_manual_spend_and_governance_mutation(self):
        shadow = Supervisor(
            replace(self.config, mode="shadow"), self.policy,
            github_backend=FakeGitHub(self.policy), sources=self.supervisor.sources,
            launcher=self.launcher)
        with self.assertRaisesRegex(SupervisorError, "shadow mode"):
            shadow.run_manual_wake("software-implementer", reason="must not run")
        with self.assertRaisesRegex(SupervisorError, "shadow mode"):
            shadow.governance_sync()

    def test_events_for_one_role_are_batched_into_one_durable_wake(self):
        self._require_unix_sockets()
        sources = {source_id: EmptySource() for source_id in SOURCE_IDS}
        sources["org-issues"] = TwoEventSource()
        supervisor = Supervisor(
            self.config, self.policy, github_backend=FakeGitHub(self.policy),
            sources=sources, launcher=self.launcher)
        result = supervisor.tick()
        self.assertEqual(1, len(result["wakes"]))
        store = Store(self.config.state_database)
        try:
            self.assertEqual(2, store.connection.execute(
                "SELECT count(*) FROM delivery_event").fetchone()[0])
        finally:
            store.close()

    def test_pending_route_left_before_delivery_is_recovered_by_next_tick(self):
        self._require_unix_sockets()
        store = Store(self.config.state_database)
        try:
            snapshot = store.begin_snapshot("org-issues")
            store.stage_observation(
                snapshot, "org-issues", "77", "77:2026-01-01T03:00:00Z",
                "2026-01-01T03:00:00Z", {"number": 77, "state": "open",
                                          "updated_at": "2026-01-01T03:00:00Z",
                                          "labels": []})
            store.checkpoint("org-issues", initial_watermark="2026-01-01T00:00:00Z")
            store.finish_snapshot(
                snapshot, complete=True, page_count=1,
                maximum_event_time="2026-01-01T03:00:00Z",
                next_watermark="2026-01-01T03:00:00Z")
            event_id = str(store.connection.execute(
                "SELECT event_id FROM observed_event").fetchone()[0])
            store.finish_event_routing(event_id, "software-implementer")
        finally:
            store.close()
        supervisor = Supervisor(
            self.config, self.policy, github_backend=FakeGitHub(self.policy),
            sources={source_id: EmptySource() for source_id in SOURCE_IDS},
            launcher=self.launcher)
        result = supervisor.tick()
        self.assertEqual("succeeded", result["wakes"][0]["status"])
        store = Store(self.config.state_database)
        try:
            self.assertEqual("succeeded", store.connection.execute(
                "SELECT state FROM delivery").fetchone()[0])
        finally:
            store.close()

    def test_unproved_process_death_holds_session_capacity_and_budget(self):
        self._require_unix_sockets()
        supervisor = Supervisor(
            self.config, self.policy, github_backend=FakeGitHub(self.policy),
            sources={source_id: EmptySource() for source_id in SOURCE_IDS},
            launcher=UncertainSpawnLauncher())
        result = supervisor.run_manual_wake("software-implementer", reason="test")
        self.assertEqual("uncertain", result["status"])
        store = Store(self.config.state_database)
        try:
            self.assertEqual("active", store.connection.execute(
                "SELECT state FROM wake_session").fetchone()[0])
            self.assertEqual("uncertain", store.connection.execute(
                "SELECT state FROM wake_runtime").fetchone()[0])
            self.assertEqual("uncertain", store.connection.execute(
                "SELECT state FROM budget_reservation").fetchone()[0])
        finally:
            store.close()

    def test_recovery_releases_pre_wake_orphan_atomically(self):
        store = Store(self.config.state_database)
        session = store.create_session(
            org_id=self.policy.org_id, role="software-implementer",
            policy_digest=self.policy.policy_digest, workspace_aliases={"org", "product"},
            global_cap=self.policy.global_max_agents)
        reservation = store.reserve_budget(
            session.session_id, epoch="2026-01-01", requested_tokens=100,
            buckets=[("org", self.policy.org_id, 1000),
                     ("role", "software-implementer", 1000)])
        store.close()
        result = self.supervisor.recover()
        self.assertEqual(1, result["recovered"])
        store = Store(self.config.state_database)
        try:
            self.assertEqual("revoked", store.connection.execute(
                "SELECT state FROM wake_session WHERE session_id=?",
                (session.session_id,)).fetchone()[0])
            self.assertEqual("released", store.connection.execute(
                "SELECT state FROM budget_reservation WHERE reservation_id=?",
                (reservation,)).fetchone()[0])
        finally:
            store.close()

    def test_any_durable_process_record_prevents_safe_budget_release(self):
        store = Store(self.config.state_database)
        session = store.create_session(
            org_id=self.policy.org_id, role="software-implementer",
            policy_digest=self.policy.policy_digest, workspace_aliases={"org", "product"},
            global_cap=self.policy.global_max_agents)
        reservation = store.reserve_budget(
            session.session_id, epoch="2026-01-01", requested_tokens=100,
            buckets=[("org", self.policy.org_id, 1000),
                     ("role", "software-implementer", 1000)])
        store.prepare_wake(
            session, delivery_id=None, reservation_id=reservation,
            socket_path=str(self.root / "host/wakes/recovery.sock"), reason="recovery test")
        store.record_process(
            process_key="parent:recovery", session_id=session.session_id,
            worker_attempt_id=None, kind="parent", pid=9999, pgid=9999,
            birth_token="historic")
        store.finish_process("parent:recovery", state="terminated", exit_code=-9)
        store.close()
        result = self.supervisor.recover()
        self.assertEqual(1, result["recovered"])
        store = Store(self.config.state_database)
        try:
            self.assertEqual("uncertain", store.connection.execute(
                "SELECT state FROM budget_reservation WHERE reservation_id=?",
                (reservation,)).fetchone()[0])
            self.assertNotEqual("released", store.connection.execute(
                "SELECT state FROM budget_reservation WHERE reservation_id=?",
                (reservation,)).fetchone()[0])
        finally:
            store.close()


class HostLifecycleTests(unittest.TestCase):
    def test_pid_reuse_never_signals_the_replacement_process_group(self):
        with (patch("sjg_control.host_process.process_birth_token", return_value="new-birth"),
              patch("sjg_control.host_process.os.killpg") as kill):
            self.assertFalse(_terminate_group(42, 42, "old-birth", grace_seconds=0))
            kill.assert_not_called()

    def test_timeout_with_changed_process_identity_is_persisted_as_uncertain(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = compile_policy(policy_data(), source_commit="host")
            provider = ProviderHostConfig(
                "codex-cli", "/usr/bin/true", str(root / "home"), str(root / "policy"),
                frozenset({"1.0.0"}), {role: "model" for role in policy.roles},
                {name: "model" for name in ("researcher", "drafter", "implementer")}, 60)
            config = SupervisorConfig(
                str(root / "organization"), str(root / "rules"), "a" * 64,
                str(root / "state.sqlite"), str(root / "admin.sock"), str(root / "wakes"),
                str(root / "scratch"), {}, GitHubHostConfig(
                    "/usr/bin/true", str(root / "token"), str(root), None, None),
                provider, os.getuid() + 1, os.getuid(), os.getgid(), "live", 60, 300, 300,
                {}, "2026-01-01T00:00:00Z")
            store = Store(config.state_database)
            session = store.create_session(
                org_id=policy.org_id, role="software-implementer",
                policy_digest=policy.policy_digest, workspace_aliases={"org", "product"},
                global_cap=policy.global_max_agents)
            store.close()

            class TimedOutProcess:
                pid = 4242
                returncode = -9
                calls = 0

                def communicate(self, *_args, **_kwargs):
                    self.calls += 1
                    if self.calls == 1:
                        raise subprocess.TimeoutExpired(["provider"], 60)
                    return "", ""

            launcher = HostProviderLauncher(config)
            process = TimedOutProcess()
            with (patch("sjg_control.host_process.subprocess.Popen", return_value=process),
                  patch("sjg_control.host_process.process_birth_token", return_value="birth"),
                  patch("sjg_control.host_process.os.getpgid", return_value=4242),
                  patch("sjg_control.host_process._terminate_group", return_value=False)):
                with self.assertRaises(TimeoutError):
                    launcher._run(  # noqa: SLF001 - exercises the durable host boundary.
                        command=["/usr/bin/true"], prompt="", environment={},
                        session_id=session.session_id, process_key="parent:test", kind="parent")
            store = Store(config.state_database)
            try:
                self.assertEqual("uncertain", store.connection.execute(
                    "SELECT state FROM process_runtime").fetchone()[0])
            finally:
                store.close()


class AdminProtocolTests(unittest.TestCase):
    def test_admin_request_id_and_argument_shape_are_exact(self):
        supervisor = SimpleNamespace(config=SimpleNamespace(admin_socket="/tmp/sjg-admin-test"))
        server = AdminServer(supervisor)
        with self.assertRaisesRegex(ServiceError, "request id"):
            server.handle({"schema": "sjg.admin/v1", "request_id": "short",
                           "operation": "status", "arguments": {}})
        with self.assertRaisesRegex(ServiceError, "invalid shape"):
            server.handle({"schema": "sjg.admin/v1", "request_id": "request-admin-0001",
                           "operation": "status", "arguments": {"extra": True}})


class VerificationTests(unittest.TestCase):
    def test_pending_comment_is_resolved_only_by_its_exact_marker(self):
        policy = compile_policy(policy_data(), source_commit="host")
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / "state.sqlite")
            session = store.create_session(
                org_id=policy.org_id, role="software-implementer",
                policy_digest=policy.policy_digest, workspace_aliases={"org", "product"},
                global_cap=policy.global_max_agents)
            key = "wake-verification-comment-0001"
            arguments = {"repository": "org", "id": 3, "body": "hello"}
            digest = hashlib.sha256(json.dumps(
                arguments, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            store.reserve_request(session.session_id, key, "pm.issue.comment", digest, arguments)
            store.mark_request_applying(key)
            store.finish_request(key, "verify_pending", {"status": "indeterminate"})

            class Backend:
                def __init__(self):
                    self.policy = policy

                def _pages(self, endpoint):
                    return [{"id": 99, "body": f"<!-- sjg-idempotency:{key} -->\nhello"}]

            counts = resolve_pending(store, GitHubOperationVerifier(Backend()))
            self.assertEqual(1, counts["present"])
            self.assertEqual("succeeded", store.connection.execute(
                "SELECT state FROM broker_request WHERE idempotency_key=?", (key,)).fetchone()[0])
            store.close()


class GovernanceMonitorTests(unittest.TestCase):
    class Backend:
        project = None

        def __init__(self, policy, *, complete=True):
            self.policy = policy
            self.complete = complete
            self.next_number = 20
            self.issues = [
                {"id": 101, "number": 1, "title": "Human queue", "body": "",
                 "state": "open", "updated_at": "2026-01-01T00:00:00Z"},
                {"id": 102, "number": 2, "title": "[PROPOSAL] New market", "body": "",
                 "state": "open", "html_url": "https://example/2",
                 "updated_at": "2026-01-01T00:00:00Z"},
                {"id": 110, "number": 10, "title": "Old human task",
                 "body": "<!-- governance-monitor-source:blocker:old -->",
                 "state": "open", "updated_at": "2026-01-01T00:00:00Z"},
            ]
            self.children = [self.issues[-1]]

        def _issue(self, arguments):
            number = int(arguments["id"])
            return next(value for value in self.issues if value["number"] == number)

        def _pages(self, endpoint):
            if endpoint.endswith("issues?state=all&per_page=100"):
                return list(self.issues)
            if endpoint.endswith("issues/1/sub_issues?per_page=100"):
                return list(self.children)
            if endpoint.endswith("pulls?state=open&per_page=100"):
                return ([{"number": 3}] if "alice/host-test" in endpoint else [])
            raise AssertionError(endpoint)

        def _api(self, endpoint, *, method="GET", body=None):
            if endpoint.endswith("pulls/3"):
                return {"number": 3, "title": "Ready", "draft": False,
                        "mergeable": True if self.complete else None,
                        "mergeable_state": "clean", "html_url": "https://example/pr/3"}
            if method == "POST" and endpoint.endswith("/issues"):
                issue = {"id": 1000 + self.next_number, "number": self.next_number,
                         "title": body["title"], "body": body["body"], "state": "open",
                         "updated_at": "2026-01-01T01:00:00Z"}
                self.next_number += 1
                self.issues.append(issue)
                return issue
            if method == "POST" and endpoint.endswith("issues/1/sub_issues"):
                issue = next(value for value in self.issues
                             if value["id"] == body["sub_issue_id"])
                if issue not in self.children:
                    self.children.append(issue)
                return {}
            if method == "PATCH" and "/issues/" in endpoint:
                number = int(endpoint.rsplit("/", 1)[1])
                issue = next(value for value in self.issues if value["number"] == number)
                issue.update(body)
                issue["updated_at"] = "2026-01-01T02:00:00Z"
                return issue
            raise AssertionError((method, endpoint, body))

    def _workspace(self, root, *, open_blocker=True):
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "org-chart.yaml").write_text(json.dumps({
            "human_principal": {"github": "alice"},
            "global": {"principal_queue_issue": 1, "pm_project_number": 7},
        }), encoding="utf-8")
        blockers = []
        if open_blocker:
            blockers.append({"id": "need-key", "kind": "external-input",
                             "needs": "human-principal", "value": "infra",
                             "effort_minutes": 5, "summary": "Provide key",
                             "action": "Add the reviewed credential", "blocks": ["deploy"],
                             "opened": "2026-01-01", "state": "open"})
        (workspace / "blockers.yaml").write_text(
            json.dumps({"blockers": blockers}), encoding="utf-8")
        return workspace

    def test_audit_is_read_only_and_sync_reconciles_marker_owned_queue(self):
        policy = compile_policy(policy_data(), source_commit="host")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = Store(root / "state.sqlite")
            backend = self.Backend(policy)
            monitor = GovernanceMonitor(
                policy, backend, store, org_workspace=str(self._workspace(root)))
            audit = monitor.audit()
            self.assertFalse(audit["mutation"])
            self.assertEqual(4, len(audit["actions"]))
            self.assertEqual(0, store.connection.execute(
                "SELECT count(*) FROM reconcile_operation").fetchone()[0])
            synced = monitor.sync()
            self.assertEqual({"succeeded": 4}, synced["applied"])
            self.assertEqual([], monitor.audit()["actions"])
            self.assertEqual("closed", next(
                value for value in backend.issues if value["number"] == 10)["state"])
            store.close()

    def test_incomplete_mergeability_aborts_before_any_reconciliation_write(self):
        policy = compile_policy(policy_data(), source_commit="host")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = Store(root / "state.sqlite")
            monitor = GovernanceMonitor(
                policy, self.Backend(policy, complete=False), store,
                org_workspace=str(self._workspace(root)))
            with self.assertRaisesRegex(GovernanceError, "complete PR"):
                monitor.sync()
            self.assertEqual(0, store.connection.execute(
                "SELECT count(*) FROM reconcile_operation").fetchone()[0])
            store.close()


if __name__ == "__main__":
    unittest.main()
