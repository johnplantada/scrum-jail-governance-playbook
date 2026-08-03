"""Provider process lifecycle owned by the privileged host supervisor."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping
import hashlib
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import time

from .host_config import ProviderHostConfig, SupervisorConfig
from .provider import (CODEX_PARENT_PROFILE, codex_profile_override,
                       materialize_claude_runtime_settings, parent_command,
                       sanitized_environment, validate_codex_config, worker_command)
from .store import StateError, Store
from .workers import WorkerError


class HostProcessError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessResult:
    output: str
    error: str
    returncode: int
    tokens: int | None
    output_sha256: str


def provider_sentinel_path(provider: ProviderHostConfig) -> Path:
    directory = ".codex" if provider.provider == "codex-cli" else ".claude"
    return Path(provider.managed_home) / directory / "sjg-auth-sentinel"


def process_birth_token(pid: int) -> str:
    """Return a PID-reuse-resistant identity for a process on Linux or macOS/BSD."""
    if pid <= 0:
        raise HostProcessError("process id must be positive")
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.is_file():
        try:
            raw = stat_path.read_text(encoding="utf-8")
            close = raw.rfind(")")
            fields = raw[close + 2:].split()
            start_ticks = fields[19]
            boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
            return f"linux:{boot}:{start_ticks}"
        except (OSError, IndexError, ValueError) as exc:
            raise HostProcessError("cannot establish Linux process birth identity") from exc
    try:
        result = subprocess.run(
            ["/bin/ps", "-o", "lstart=", "-p", str(pid)], text=True,
            capture_output=True, timeout=5, env={"PATH": "/usr/bin:/bin", "LANG": "C",
                                                  "LC_ALL": "C"}, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HostProcessError("cannot establish process birth identity") from exc
    value = " ".join(result.stdout.split())
    if result.returncode or not value:
        raise HostProcessError("process disappeared before its birth identity was recorded")
    return f"posix:{value}"


def extract_usage(provider: str, output: str) -> int | None:
    """Extract the final machine-readable token total; unknown shapes remain unknown."""
    candidates: list[int] = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        objects = [value]
        for key in ("usage", "result", "turn", "message"):
            nested = value.get(key)
            if isinstance(nested, dict):
                objects.append(nested)
                usage = nested.get("usage")
                if isinstance(usage, dict):
                    objects.append(usage)
        for item in objects:
            total = item.get("total_tokens")
            if isinstance(total, int) and total >= 0:
                candidates.append(total)
                continue
            input_tokens = item.get("input_tokens")
            output_tokens = item.get("output_tokens")
            if isinstance(input_tokens, int) and input_tokens >= 0 and isinstance(
                    output_tokens, int) and output_tokens >= 0:
                candidates.append(input_tokens + output_tokens)
    if provider not in {"codex-cli", "claude-cli"}:
        raise HostProcessError("unsupported provider usage format")
    return max(candidates) if candidates else None


def agent_process_identity(config: SupervisorConfig) -> dict[str, Any]:
    if os.geteuid() == 0:
        return {"user": config.agent_uid, "group": config.agent_gid,
                "extra_groups": [], "umask": 0o077}
    if config.agent_uid != os.geteuid() or config.agent_gid != os.getegid():
        raise HostProcessError("only root may launch the configured separate agent identity")
    return {"umask": 0o077}


def _terminate_group(pid: int, pgid: int, birth_token: str, *, grace_seconds: float = 3.0) -> bool:
    try:
        if process_birth_token(pid) != birth_token:
            return False
    except HostProcessError:
        return True
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    try:
        if process_birth_token(pid) != birth_token:
            return False
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, HostProcessError):
        return True
    return True


class HostProviderLauncher:
    """Launch parent and named-worker providers with durable process-group identity."""

    def __init__(self, config: SupervisorConfig):
        self.config = config
        self.provider: ProviderHostConfig = config.provider
        self._usage: dict[str, int] = {}
        self._uncertain_usage: set[str] = set()
        self._lock = Lock()

    def _environment(self, additions: Mapping[str, str] | None = None,
                     *, managed_tmp: str | Path | None = None) -> dict[str, str]:
        return sanitized_environment(
            {}, managed_home=self.provider.managed_home,
            managed_tmp=os.fspath(managed_tmp or self.config.scratch_directory),
            additions=additions,
        )

    def _agent_directory(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise HostProcessError("provider runtime directory is unsafe")
        if os.geteuid() == 0:
            os.chown(path, self.config.agent_uid, self.config.agent_gid)
        elif path.stat().st_uid != self.config.agent_uid:
            raise HostProcessError("provider runtime directory has the wrong owner")
        os.chmod(path, 0o700)
        return path

    @staticmethod
    def _sealed_directory(path: Path) -> Path:
        """Create a supervisor-owned, agent-traversable directory for read-only policy."""
        path.mkdir(parents=True, exist_ok=True)
        if (path.is_symlink() or not path.is_dir()
                or path.stat().st_uid != os.geteuid()):
            raise HostProcessError("provider policy runtime directory is unsafe")
        os.chmod(path, 0o711)
        return path

    def _claude_deny_roots(self) -> tuple[Path, ...]:
        values = {
            Path(self.provider.managed_home), Path(self.config.github.home),
            Path(self.config.github.token_file), Path(self.config.state_database).parent,
            Path(self.config.admin_socket).parent, Path(self.config.wake_socket_directory),
            Path(self.config.scratch_directory),
        }
        values.update(Path(value) for aliases in self.config.workspaces.values()
                      for value in aliases.values())
        return tuple(sorted(values, key=os.fspath))

    def verify_workspace_access(self, workspaces: list[str]) -> None:
        """Prove every commissioned workspace is readable and writable by the agent UID."""
        for workspace in sorted(set(workspaces)):
            result = subprocess.run(
                ["/bin/test", "-r", workspace, "-a", "-w", workspace],
                text=True, capture_output=True, timeout=10, env=self._environment(),
                check=False, **agent_process_identity(self.config),
            )
            if result.returncode:
                raise HostProcessError(
                    f"agent identity cannot read and write commissioned workspace: {workspace}")

    def _run(self, *, command: list[str], prompt: str, environment: dict[str, str],
             session_id: str, process_key: str, kind: str,
             worker_attempt_id: str | None = None,
             on_spawned: Callable[[], None] | None = None,
             on_started: Callable[[], None] | None = None) -> ProcessResult:
        try:
            process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=environment, start_new_session=True,
                **agent_process_identity(self.config),
            )
        except OSError as exc:
            raise HostProcessError("provider process failed before start") from exc
        try:
            if on_spawned is not None:
                on_spawned()
            birth = process_birth_token(process.pid)
            pgid = os.getpgid(process.pid)
            store = Store(self.config.state_database)
            try:
                store.record_process(
                    process_key=process_key, session_id=session_id,
                    worker_attempt_id=worker_attempt_id, kind=kind, pid=process.pid,
                    pgid=pgid, birth_token=birth,
                )
            finally:
                store.close()
            if on_started is not None:
                on_started()
        except Exception:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            process.wait()
            cleanup = Store(self.config.state_database)
            try:
                try:
                    cleanup.finish_process(process_key, state="terminated",
                                           exit_code=process.returncode)
                except StateError:
                    pass
            finally:
                cleanup.close()
            raise
        try:
            output, error = process.communicate(prompt, timeout=self.provider.timeout_seconds)
            state = "exited"
        except subprocess.TimeoutExpired as exc:
            terminated = _terminate_group(process.pid, pgid, birth)
            output, error = process.communicate()
            state = "terminated" if terminated else "uncertain"
            store = Store(self.config.state_database)
            try:
                store.finish_process(process_key, state=state, exit_code=process.returncode)
            finally:
                store.close()
            raise TimeoutError("provider process exceeded its commissioned timeout") from exc
        store = Store(self.config.state_database)
        try:
            store.finish_process(process_key, state=state, exit_code=process.returncode)
        finally:
            store.close()
        tokens = extract_usage(self.provider.provider, output)
        return ProcessResult(output, error, int(process.returncode), tokens,
                             hashlib.sha256(output.encode("utf-8")).hexdigest())

    def run_parent(self, *, session_id: str, wake_id: str, role: str, workspace: str,
                   socket_path: str, prompt: str,
                   on_spawned: Callable[[], None] | None = None,
                   on_started: Callable[[], None] | None = None) -> ProcessResult:
        settings_path = None
        managed_root = self.provider.policy_root
        wake_root = self._agent_directory(
            Path(self.config.scratch_directory) / f"wake-{wake_id}")
        temporary_root = self._agent_directory(wake_root / "tmp")
        if self.provider.provider == "claude-cli":
            settings_root = self._sealed_directory(wake_root / "claude")
            settings_path = os.fspath(materialize_claude_runtime_settings(
                Path(self.provider.policy_root) / "parent-settings.json",
                settings_root / "parent-settings.json", profile="parent",
                workspace=workspace, deny_read_roots=self._claude_deny_roots(),
                wake_socket=socket_path,
            ))
            managed_root = os.fspath(settings_root)
        command = parent_command(
            self.provider.provider, self.provider.executable,
            model=self.provider.role_models[role], workspace=workspace,
            managed_root=managed_root, settings_path=settings_path, wake_socket=socket_path,
        )
        environment = self._environment({
            "SJG_WAKE_SOCKET": socket_path, "SJG_SESSION_ID": session_id,
            "SJG_WAKE_ID": wake_id, "AGENT_NAME": role,
        }, managed_tmp=temporary_root)
        return self._run(command=command, prompt=prompt, environment=environment,
                         session_id=session_id, process_key=f"parent:{session_id}", kind="parent",
                         on_spawned=on_spawned, on_started=on_started)

    def run_worker(self, *, worker_type: str, model: str, prompt: str, workspace: Path,
                   environment: dict[str, str], timeout_seconds: int) -> str:
        del model, timeout_seconds  # Host policy, never the caller, selects both values.
        attempt = str(environment.get("SJG_WORKER_ATTEMPT") or "")
        store = Store(self.config.state_database)
        try:
            row = store.connection.execute(
                "SELECT session_id FROM worker_attempt WHERE attempt_id=?", (attempt,),
            ).fetchone()
        finally:
            store.close()
        if row is None:
            raise WorkerError("worker attempt is absent from controller state")
        session_id = str(row["session_id"])
        settings_path = None
        managed_root = self.provider.policy_root
        settings_root: Path | None = None
        if self.provider.provider == "claude-cli":
            profile = "worker-implementer" if worker_type == "implementer" else "worker-readonly"
            policy_parent = self._sealed_directory(
                Path(self.config.scratch_directory) / "runtime-policies")
            settings_root = self._sealed_directory(policy_parent / f"worker-{attempt}")
            settings_path = os.fspath(materialize_claude_runtime_settings(
                Path(self.provider.policy_root) / f"{profile}-settings.json",
                settings_root / f"{profile}-settings.json", profile=profile,
                workspace=workspace, deny_read_roots=self._claude_deny_roots(),
            ))
            managed_root = os.fspath(settings_root)
        command = worker_command(
            self.provider.provider, self.provider.executable,
            model=self.provider.worker_models[worker_type], workspace=os.fspath(workspace),
            managed_root=managed_root, worker_type=worker_type, settings_path=settings_path,
        )
        try:
            result = self._run(
                command=command, prompt=prompt,
                environment=self._environment(managed_tmp=workspace),
                session_id=session_id, process_key=f"worker:{attempt}", kind="worker",
                worker_attempt_id=attempt,
            )
        except (HostProcessError, TimeoutError) as exc:
            with self._lock:
                self._uncertain_usage.add(session_id)
            raise WorkerError(str(exc)) from exc
        finally:
            if settings_root is not None:
                shutil.rmtree(settings_root, ignore_errors=True)
        if result.tokens is None:
            with self._lock:
                self._uncertain_usage.add(session_id)
            raise WorkerError("worker provider failed or returned no trustworthy usage record")
        with self._lock:
            self._usage[session_id] = self._usage.get(session_id, 0) + result.tokens
        if result.returncode != 0:
            raise WorkerError("worker provider returned failure")
        return result.output

    def take_worker_usage(self, session_id: str) -> int | None:
        with self._lock:
            value = self._usage.pop(session_id, 0)
            if session_id in self._uncertain_usage:
                self._uncertain_usage.remove(session_id)
                return None
            return value

    @staticmethod
    def _walk_json(value: Any):
        if isinstance(value, dict):
            yield value
            for nested in value.values():
                yield from HostProviderLauncher._walk_json(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from HostProviderLauncher._walk_json(nested)

    def verify_auth_sentinel(self, *, workspace: str) -> dict[str, Any]:
        """Prove the agent identity can read the canary, but the provider sandbox cannot."""
        sentinel = provider_sentinel_path(self.provider)
        try:
            metadata = sentinel.lstat()
            nonce = sentinel.read_text(encoding="ascii")
        except OSError as exc:
            raise HostProcessError("provider auth sentinel is absent or unreadable") from exc
        if (not stat.S_ISREG(metadata.st_mode) or sentinel.is_symlink()
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or metadata.st_uid != self.config.agent_uid
                or not re.fullmatch(r"[0-9a-f]{64}\n", nonce)):
            raise HostProcessError("provider auth sentinel ownership, mode, or content is invalid")
        environment = self._environment()
        baseline = subprocess.run(
            ["/bin/cat", os.fspath(sentinel)], text=True, capture_output=True, timeout=10,
            env=environment, check=False, **agent_process_identity(self.config),
        )
        if baseline.returncode or baseline.stdout != nonce:
            raise HostProcessError(
                "agent identity cannot read the sentinel without a sandbox; probe is inconclusive")

        if self.provider.provider == "codex-cli":
            validate_codex_config(Path(self.provider.policy_root) / "config.toml",
                                  managed_home=self.provider.managed_home)
            socket_override = codex_profile_override(
                CODEX_PARENT_PROFILE, "network.unix_sockets",
                Path(self.config.scratch_directory) / "sentinel-probe.sock", "allow")
            prefix = [self.provider.executable, "sandbox", "-c", socket_override,
                      "--permission-profile",
                      CODEX_PARENT_PROFILE, "--cd", workspace, "--"]
            try:
                healthy = subprocess.run(
                    [*prefix, "/bin/echo", "SJG_SANDBOX_OK"], text=True,
                    capture_output=True, timeout=30, env=environment, check=False,
                    **agent_process_identity(self.config),
                )
                if healthy.returncode or healthy.stdout.strip() != "SJG_SANDBOX_OK":
                    raise HostProcessError("Codex sandbox or permissions profile is unavailable")
                result = subprocess.run(
                    [*prefix, "/bin/cat", os.fspath(sentinel)], text=True,
                    capture_output=True, timeout=30, env=environment,
                    check=False, **agent_process_identity(self.config),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise HostProcessError("Codex sentinel sandbox probe failed") from exc
            if nonce in result.stdout or nonce in result.stderr or result.returncode == 0:
                raise HostProcessError("Codex tool sandbox can read provider auth storage")
            return {"provider": "codex-cli", "sandbox_read_denied": True,
                    "baseline_readable": True}

        settings = Path(self.provider.policy_root) / "parent-settings.json"
        command = parent_command(
            "claude-cli", self.provider.executable,
            model=self.provider.role_models[sorted(self.provider.role_models)[0]],
            workspace=workspace, managed_root=self.provider.policy_root,
            settings_path=os.fspath(settings),
        )
        prompt = (
            "Mandatory security commissioning probe. Invoke the Read tool exactly once with "
            f"file_path {sentinel}. Do not guess or repeat file contents. After the tool result, "
            "reply only SENTINEL_DENIED if access was denied."
        )
        try:
            result = subprocess.run(
                command, input=prompt, text=True, capture_output=True,
                timeout=self.provider.timeout_seconds, env=environment, check=False,
                **agent_process_identity(self.config),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HostProcessError("Claude sentinel model-tool probe failed") from exc
        if nonce.strip() in result.stdout or nonce.strip() in result.stderr:
            raise HostProcessError("Claude tool sandbox can read provider auth storage")
        attempted = False
        denied = False
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            for item in self._walk_json(event):
                tool_input = item.get("input")
                if (item.get("type") == "tool_use" and item.get("name") == "Read"
                        and isinstance(tool_input, dict)
                        and tool_input.get("file_path") == os.fspath(sentinel)):
                    attempted = True
                if item.get("type") == "tool_result" and item.get("is_error") is True:
                    denied = True
        if result.returncode or not attempted or not denied:
            raise HostProcessError("Claude did not produce proof of a denied sentinel tool read")
        return {"provider": "claude-cli", "sandbox_read_denied": True,
                "baseline_readable": True}

    def terminate_session(self, session_id: str) -> bool:
        store = Store(self.config.state_database)
        try:
            processes = store.active_processes(session_id)
        finally:
            store.close()
        complete = True
        for row in processes:
            terminated = _terminate_group(
                int(row["pid"]), int(row["pgid"]), str(row["birth_token"]),
            )
            update = Store(self.config.state_database)
            try:
                update.finish_process(
                    str(row["process_key"]),
                    state="terminated" if terminated else "uncertain", exit_code=None,
                )
            except StateError:
                pass
            finally:
                update.close()
            complete = complete and terminated
        return complete
