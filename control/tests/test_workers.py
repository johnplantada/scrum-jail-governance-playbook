from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from pathlib import Path

from sjg_control.authz import Session
from sjg_control.policy import compile_policy
from sjg_control.store import Store
from sjg_control.workers import WorkerError, WorkerSupervisor
import sjg_control.workers as worker_module
from test_control_plane import policy_data


class Launcher:
    def __init__(self, change=None, output="result"):
        self.change = change
        self.output = output
        self.environment = None

    def run_worker(self, *, worker_type, model, prompt, workspace, environment, timeout_seconds):
        self.environment = environment
        if self.change:
            (workspace / self.change).write_text("changed\n", encoding="utf-8")
        return self.output


class ParentRaceLauncher(Launcher):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent

    def run_worker(self, *, worker_type, model, prompt, workspace, environment, timeout_seconds):
        (workspace / "allowed.txt").write_text("worker version\n", encoding="utf-8")
        (self.parent / "allowed.txt").write_text("parent newer version\n", encoding="utf-8")
        return "result"


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "allowed.txt").write_text("before\n", encoding="utf-8")
        (self.workspace / "other.txt").write_text("before\n", encoding="utf-8")
        self.store = Store(root / "state.sqlite")
        self.policy = compile_policy(policy_data(), source_commit="abc")
        self.session = self.store.create_session(
            org_id=self.policy.org_id, role="software-implementer",
            policy_digest=self.policy.policy_digest, workspace_aliases={"org", "product"},
            global_cap=self.policy.global_max_agents)
        self.scratch = root / "scratch"

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def supervisor(self, launcher):
        return WorkerSupervisor(self.policy, self.store, launcher,
                                luna_models={"researcher": "luna", "drafter": "luna",
                                             "implementer": "luna"}, scratch_root=self.scratch)

    def test_worker_has_no_parent_broker_socket(self):
        launcher = Launcher()
        result = self.supervisor(launcher).run(self.session, {
            "type": "researcher", "task": "inspect", "readPaths": ["allowed.txt"],
            "writePaths": []}, parent_workspace=self.workspace)
        self.assertEqual("result", result.output)
        self.assertNotIn("SJG_WAKE_SOCKET", launcher.environment)

    def test_implementer_imports_only_allowed_path(self):
        launcher = Launcher("allowed.txt")
        result = self.supervisor(launcher).run(self.session, {
            "type": "implementer", "task": "change", "readPaths": ["allowed.txt"],
            "writePaths": ["allowed.txt"]}, parent_workspace=self.workspace)
        self.assertEqual(("allowed.txt",), result.changed_paths)
        self.assertEqual("changed\n", (self.workspace / "allowed.txt").read_text())

    def test_worker_import_refuses_to_overwrite_a_newer_parent_file(self):
        with self.assertRaisesRegex(WorkerError, "newer parent file"):
            self.supervisor(ParentRaceLauncher(self.workspace)).run(self.session, {
                "type": "implementer", "task": "change", "readPaths": ["allowed.txt"],
                "writePaths": ["allowed.txt"]}, parent_workspace=self.workspace)
        self.assertEqual("parent newer version\n",
                         (self.workspace / "allowed.txt").read_text(encoding="utf-8"))
        state = self.store.connection.execute(
            "SELECT state FROM worker_attempt ORDER BY sequence DESC LIMIT 1").fetchone()[0]
        self.assertEqual("failed", state)

    def test_worker_supervisor_resolves_the_controller_bound_parent_session(self):
        forged = Session(
            self.session.session_id, self.session.wake_id, self.session.org_id,
            "commercial-planner", self.session.policy_digest, frozenset({"org"}),
            self.session.expires_at,
        )
        with self.assertRaisesRegex(WorkerError, "controller-owned state"):
            self.supervisor(Launcher()).run(forged, {
                "type": "researcher", "task": "inspect", "readPaths": ["allowed.txt"],
                "writePaths": []}, parent_workspace=self.workspace)

    def test_readonly_worker_cannot_receive_write_scope(self):
        with self.assertRaises(WorkerError):
            self.supervisor(Launcher()).run(self.session, {
                "type": "researcher", "task": "change", "readPaths": ["allowed.txt"],
                "writePaths": ["allowed.txt"]}, parent_workspace=self.workspace)

    def test_path_escape_is_rejected(self):
        with self.assertRaises(WorkerError):
            self.supervisor(Launcher()).run(self.session, {
                "type": "implementer", "task": "escape", "readPaths": ["../outside"],
                "writePaths": ["../outside"]}, parent_workspace=self.workspace)

    def test_symlink_aliases_cannot_overwrite_undeclared_targets(self):
        target = self.workspace / "secret.txt"
        target.write_text("secret\n", encoding="utf-8")
        (self.workspace / "alias.txt").symlink_to("secret.txt")
        with self.assertRaisesRegex(WorkerError, "symlink"):
            self.supervisor(Launcher("alias.txt")).run(self.session, {
                "type": "implementer", "task": "change", "readPaths": ["alias.txt"],
                "writePaths": ["alias.txt"]}, parent_workspace=self.workspace)
        self.assertEqual("secret\n", target.read_text())

        directory_target = self.workspace / "private"
        directory_target.mkdir()
        (directory_target / "value.txt").write_text("private\n", encoding="utf-8")
        (self.workspace / "linked").symlink_to("private", target_is_directory=True)
        with self.assertRaisesRegex(WorkerError, "symlink"):
            self.supervisor(Launcher("linked/value.txt")).run(self.session, {
                "type": "implementer", "task": "change",
                "readPaths": ["linked/value.txt"],
                "writePaths": ["linked/value.txt"]}, parent_workspace=self.workspace)
        self.assertEqual("private\n", (directory_target / "value.txt").read_text())

    def test_component_swap_at_atomic_import_cannot_redirect_the_write(self):
        slot = self.workspace / "slot"
        slot.mkdir()
        (slot / "file.txt").write_text("before\n")
        outside = self.workspace.parent / "outside"
        outside.mkdir()
        (outside / "file.txt").write_text("outside\n")
        real_rename = worker_module.os.rename
        swapped = False

        def swap_then_rename(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
            nonlocal swapped
            if not swapped:
                swapped = True
                real_rename(slot, self.workspace / "detached")
                slot.symlink_to(outside, target_is_directory=True)
            return real_rename(source, destination, src_dir_fd=src_dir_fd,
                               dst_dir_fd=dst_dir_fd)

        with mock.patch.object(worker_module.os, "rename", side_effect=swap_then_rename):
            with self.assertRaisesRegex(WorkerError, "conflicted"):
                self.supervisor(Launcher("slot/file.txt")).run(self.session, {
                    "type": "implementer", "task": "change",
                    "readPaths": ["slot/file.txt"], "writePaths": ["slot/file.txt"]},
                    parent_workspace=self.workspace)
        self.assertEqual("outside\n", (outside / "file.txt").read_text())
        self.assertEqual("changed\n", (self.workspace / "detached/file.txt").read_text())
        self.assertEqual("failed", self.store.connection.execute(
            "SELECT state FROM worker_attempt ORDER BY sequence DESC LIMIT 1").fetchone()[0])

    def test_noncanonical_and_oversized_path_scopes_are_rejected(self):
        for paths in (["a//b"], ["a\\b"], ["x" * 256],
                      [f"p-{index}" for index in range(201)]):
            with self.subTest(paths=paths[:2]), self.assertRaises(WorkerError):
                self.supervisor(Launcher()).run(self.session, {
                    "type": "researcher", "task": "inspect", "readPaths": paths,
                    "writePaths": []}, parent_workspace=self.workspace)

    def test_write_path_must_also_be_declared_as_input(self):
        with self.assertRaises(WorkerError):
            self.supervisor(Launcher()).run(self.session, {
                "type": "implementer", "task": "change", "readPaths": [],
                "writePaths": ["new.txt"]}, parent_workspace=self.workspace)

    def test_undeclared_new_file_is_detected_and_not_imported(self):
        with self.assertRaises(WorkerError):
            self.supervisor(Launcher("extra.txt")).run(self.session, {
                "type": "implementer", "task": "change", "readPaths": ["allowed.txt"],
                "writePaths": ["allowed.txt"]}, parent_workspace=self.workspace)
        self.assertFalse((self.workspace / "extra.txt").exists())

    def test_worker_text_output_is_bounded_before_success(self):
        supervisor = WorkerSupervisor(
            self.policy, self.store, Launcher(output="x" * 17),
            luna_models={"researcher": "luna"}, scratch_root=self.scratch,
            max_output_bytes=16)
        with self.assertRaisesRegex(WorkerError, "response-size"):
            supervisor.run(self.session, {
                "type": "researcher", "task": "inspect", "readPaths": ["allowed.txt"],
                "writePaths": []}, parent_workspace=self.workspace)
        state = self.store.connection.execute(
            "SELECT state FROM worker_attempt ORDER BY sequence DESC LIMIT 1").fetchone()[0]
        self.assertEqual("failed", state)

    def test_zero_cap_role_cannot_start_a_worker(self):
        project = self.store.create_session(
            org_id=self.policy.org_id, role="project-orchestrator",
            policy_digest=self.policy.policy_digest, workspace_aliases={"org"},
            global_cap=self.policy.global_max_agents)
        with self.assertRaises(WorkerError):
            self.supervisor(Launcher()).run(project, {
                "type": "researcher", "task": "inspect", "readPaths": ["allowed.txt"],
                "writePaths": []}, parent_workspace=self.workspace)


if __name__ == "__main__":
    unittest.main()
