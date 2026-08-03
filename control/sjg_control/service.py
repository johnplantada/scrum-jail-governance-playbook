"""Operator socket and durable scheduler service for the 1.0 host supervisor."""
from __future__ import annotations

from pathlib import Path
from threading import Event, Thread
from typing import Any
import argparse
import json
import os
import secrets
import signal
import socket
import stat
import sys
import tempfile
import re

from .host_config import HostConfigError, load_supervisor_config
from .host_process import HostProcessError, provider_sentinel_path
from .policy import PolicyError, load_policy
from .provider import (ProviderPolicyError, materialize_claude_policy,
                       materialize_codex_policy)
from .protocol import ProtocolError, encode_message, receive_message
from .server import peer_uid
from .supervisor import Supervisor, SupervisorError


class ServiceError(RuntimeError):
    pass


def _sealed_provider_policy() -> Path:
    here = Path(__file__).resolve()
    candidates = (here.parents[1] / "provider-policy", here.parents[2] / "provider-policy")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise ServiceError("installed sealed provider policy is absent")


def commission_provider(supervisor: Supervisor) -> dict[str, Any]:
    """Install sealed policy and a readable-before-sandbox sentinel; never touch auth."""
    provider = supervisor.config.provider
    home = Path(provider.managed_home)
    if home.stat().st_uid != supervisor.config.agent_uid:
        raise ServiceError("provider managed home must be owned by the commissioned agent UID")
    source = _sealed_provider_policy()
    if provider.provider == "codex-cli":
        expected = home / ".codex"
        if Path(provider.policy_root).resolve() != expected.resolve():
            raise ServiceError("Codex policyRoot must be managedHome/.codex")
        if expected.exists() and expected.stat().st_uid != supervisor.config.agent_uid:
            raise ServiceError("Codex auth/policy directory must be owned by the agent UID")
        materialize_codex_policy(source / "codex", home)
        if expected.stat().st_uid != supervisor.config.agent_uid:
            os.chown(expected, supervisor.config.agent_uid, supervisor.config.agent_gid)
    else:
        materialize_claude_policy(source / "claude", provider.policy_root)
    sentinel = provider_sentinel_path(provider)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    if sentinel.parent.stat().st_uid != supervisor.config.agent_uid:
        raise ServiceError("provider auth directory must be owned by the commissioned agent UID")
    descriptor, temporary = tempfile.mkstemp(prefix=".sjg-auth-sentinel.", dir=sentinel.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(secrets.token_hex(32) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary, supervisor.config.agent_uid, supervisor.config.agent_gid)
        os.chmod(temporary, 0o400)
        os.replace(temporary, sentinel)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    metadata = sentinel.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or sentinel.is_symlink()
            or metadata.st_uid != supervisor.config.agent_uid
            or stat.S_IMODE(metadata.st_mode) != 0o400):
        raise ServiceError("provider auth sentinel installation failed closed")
    return {"provider": provider.provider, "policy_root": provider.policy_root,
            "sentinel": os.fspath(sentinel), "auth_modified": False}


def _release_identity(organization_path: str | Path, *, source_commit: str | None = None) -> str:
    organization = json.loads(Path(organization_path).read_text(encoding="utf-8"))
    expected = organization.get("controlRelease") if isinstance(organization, dict) else None
    if not isinstance(expected, dict):
        raise ServiceError("organization control-release identity is absent")
    release_root = Path(__file__).resolve().parents[2]
    manifest_path = release_root / "MANIFEST.json"
    if not manifest_path.is_file():
        if source_commit is None:
            raise ServiceError("installed release manifest is absent")
        return source_commit
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("version") != expected.get("version")
            or manifest.get("sourceTreeSha256") != expected.get("sourceTreeSha256")
            or manifest.get("releaseQualified") is not True):
        raise ServiceError("active release does not match the organization policy")
    commit = str(manifest.get("sourceCommit") or "")
    if not commit:
        raise ServiceError("active release source commit is absent")
    return commit


def build_supervisor(config_path: str | Path, *, source_commit: str | None = None,
                     **dependencies: Any) -> Supervisor:
    try:
        initial = json.loads(Path(config_path).read_text(encoding="utf-8"))
        organization_path = initial["organization"]
        commit = _release_identity(organization_path, source_commit=source_commit)
        policy = load_policy(organization_path, source_commit=commit)
        config = load_supervisor_config(config_path, policy)
        return Supervisor(config, policy, **dependencies)
    except (OSError, KeyError, TypeError, json.JSONDecodeError, HostConfigError,
            PolicyError, SupervisorError) as exc:
        raise ServiceError(f"supervisor commissioning failed: {exc}") from exc


class AdminServer:
    """Authenticated operator surface. It contains no agent-role claim mechanism."""

    def __init__(self, supervisor: Supervisor):
        self.supervisor = supervisor
        self.socket_path = Path(supervisor.config.admin_socket)
        self.stop_event = Event()
        self._listener: socket.socket | None = None

    def stop(self) -> None:
        self.stop_event.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass

    @staticmethod
    def _exact(arguments: Any, fields: set[str]) -> dict[str, Any]:
        if not isinstance(arguments, dict) or set(arguments) != fields:
            raise ServiceError("admin operation arguments have an invalid shape")
        return arguments

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        if set(request) != {"schema", "request_id", "operation", "arguments"}:
            raise ServiceError("admin request has invalid fields")
        if request.get("schema") != "sjg.admin/v1":
            raise ServiceError("admin request schema is unsupported")
        if not isinstance(request.get("request_id"), str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{15,127}", request["request_id"]):
            raise ServiceError("admin request id is invalid")
        operation = str(request.get("operation") or "")
        arguments = request.get("arguments")
        if operation == "provider.check_auth":
            self._exact(arguments, set())
            result = self.supervisor.commissioning_check()
        elif operation == "runner.tick":
            self._exact(arguments, set())
            result = self.supervisor.tick()
        elif operation == "runner.preview":
            self._exact(arguments, set())
            result = self.supervisor.tick(preview=True)
        elif operation == "runner.pause":
            self._exact(arguments, set())
            result = self.supervisor.pause()
        elif operation == "runner.resume":
            self._exact(arguments, set())
            result = self.supervisor.resume()
        elif operation == "monitor.audit":
            values = self._exact(arguments, {"dry_run"})
            if values["dry_run"] is not True:
                raise ServiceError("monitor audit requires dry_run=true")
            result = self.supervisor.governance_audit()
        elif operation == "monitor.sync":
            values = self._exact(arguments, {"dry_run"})
            if not isinstance(values["dry_run"], bool):
                raise ServiceError("monitor sync dry_run must be boolean")
            result = (self.supervisor.governance_audit()
                      if values["dry_run"] else self.supervisor.governance_sync())
        elif operation == "wake.run":
            values = self._exact(arguments, {"role", "reason", "note"})
            result = self.supervisor.run_manual_wake(
                str(values["role"]), reason=str(values["reason"]), note=str(values["note"]))
        elif operation == "status":
            self._exact(arguments, set())
            result = self.supervisor.status()
        else:
            raise ServiceError("admin operation is not allowlisted")
        return {"schema": "sjg.admin.response/v1", "status": "ok", "result": result}

    def _connection(self, connection: socket.socket) -> None:
        with connection:
            try:
                if peer_uid(connection) != self.supervisor.config.operator_uid:
                    raise ServiceError("admin socket peer is not the commissioned operator")
                response = self.handle(receive_message(connection))
            except Exception as exc:
                response = {"schema": "sjg.admin.response/v1", "status": "denied",
                            "error": str(exc)}
            try:
                connection.sendall(encode_message(response))
            except OSError:
                pass

    def _scheduler(self) -> None:
        while not self.stop_event.wait(self.supervisor.config.poll_interval_seconds):
            try:
                self.supervisor.tick()
            except Exception:
                # Durable state and subsequent status expose failures. The service remains
                # available for pause, inspection, and explicit retry.
                continue

    def serve_forever(self) -> None:
        # Stop any orphaned provider groups before an expired login or broken policy can
        # prevent startup recovery.
        self.supervisor.recover()
        self.supervisor.commissioning_check()
        if self.socket_path.exists() or self.socket_path.is_symlink():
            raise ServiceError(f"refusing to replace existing admin socket: {self.socket_path}")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener = listener
        scheduler = Thread(target=self._scheduler, name="sjg-scheduler", daemon=True)
        try:
            listener.bind(os.fspath(self.socket_path))
            os.chown(self.socket_path, self.supervisor.config.operator_uid, -1)
            os.chmod(self.socket_path, 0o600)
            listener.listen(8)
            listener.settimeout(0.5)
            scheduler.start()
            while not self.stop_event.is_set():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self.stop_event.is_set():
                        break
                    raise
                Thread(target=self._connection, args=(connection,), daemon=True).start()
        finally:
            self.stop_event.set()
            try:
                listener.close()
            finally:
                self._listener = None
                try:
                    self.socket_path.unlink()
                except FileNotFoundError:
                    pass
            scheduler.join(timeout=2)
            # Direct/launchd operation does not provide systemd's cgroup cleanup. Prove
            # provider groups stopped and persist uncertainty before the service exits.
            self.supervisor.recover()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sjg-supervisor")
    parser.add_argument("--config", required=True)
    parser.add_argument("--source-commit",
                        help="development-only source checkout identity when no installed manifest exists")
    parser.add_argument("command", choices=("commission-provider", "serve", "check", "tick",
                                             "preview", "recover", "status"))
    args = parser.parse_args(argv)
    supervisor = build_supervisor(args.config, source_commit=args.source_commit)
    if os.geteuid() != 0:
        raise ServiceError("supervisor commissioning and operation require root")
    if args.command == "commission-provider":
        result = commission_provider(supervisor)
    elif args.command == "check":
        result = {"commissioning": supervisor.commissioning_check(),
                  "status": supervisor.status()}
    elif args.command == "tick":
        supervisor.commissioning_check()
        result = supervisor.tick()
    elif args.command == "preview":
        supervisor.commissioning_check()
        result = supervisor.tick(preview=True)
    elif args.command == "recover":
        result = supervisor.recover()
    elif args.command == "status":
        result = supervisor.status()
    else:
        server = AdminServer(supervisor)
        prior = {}

        def stop(_signum: int, _frame: Any) -> None:
            server.stop()

        for signum in (signal.SIGINT, signal.SIGTERM):
            prior[signum] = signal.signal(signum, stop)
        try:
            server.serve_forever()
        finally:
            for signum, handler in prior.items():
                signal.signal(signum, handler)
        return 0
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cli(argv: list[str] | None = None) -> int:
    try:
        return main(argv)
    except (OSError, HostProcessError, ProtocolError, ProviderPolicyError, ServiceError,
            SupervisorError) as exc:
        print(f"sjg-supervisor: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
