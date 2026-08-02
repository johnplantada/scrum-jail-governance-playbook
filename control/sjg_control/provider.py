"""Subscription-only provider evidence and sealed launch construction."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
import hashlib
import json
import os
import re
import subprocess
import tempfile
import tomllib


PROVIDER_SECRET_KEYS = frozenset({
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY", "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS",
    "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "ANTHROPIC_BEDROCK_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "GH_TOKEN", "GITHUB_TOKEN",
    "CLAUDE_CODE_USE_FOUNDRY", "OPENAI_API_BASE", "GH_CONFIG_DIR", "CLAUDE_CONFIG_DIR",
    "CODEX_HOME", "BASH_ENV", "ENV", "ZDOTDIR", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
})
ENV_ALLOWLIST = frozenset({"LANG", "LC_ALL", "TERM"})
ADDITION_ALLOWLIST = frozenset({"SJG_WAKE_SOCKET", "SJG_SESSION_ID", "SJG_WAKE_ID", "AGENT_NAME"})
CODEX_EXEC_POLICY_SHA256 = "e43bd0521ddf78cec271de2f07638258f3ac4703dce852458d244b05df7b0a3c"


class ProviderPolicyError(RuntimeError):
    pass


CLAUDE_REQUIRED_DENIES = frozenset({
    "Agent", "Task", "WebSearch", "WebFetch", "Bash(sudo:*)", "Bash(git push:*)", "Bash(git fetch:*)",
    "Bash(gh:*)", "Bash(curl:*)", "Bash(wget:*)", "Bash(security:*)", "Read(**/.env)",
    "Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.config/gh/**)",
    "Read(~/.claude/**)", "Read(~/.config/claude/**)",
})
CLAUDE_SECRET_READS = frozenset({
    ".env", "**/.env", "~/.ssh", "~/.aws", "~/.config/gh", "~/.claude",
    "~/.config/claude", "~/Library/Application Support/Claude",
})
CLAUDE_SECRET_WRITES = frozenset({
    "~/.ssh", "~/.aws", "~/.config/gh", "~/.claude", "~/.config/claude",
    "~/Library/Application Support/Claude",
})


@dataclass(frozen=True)
class AuthEvidence:
    provider: str
    logged_in: bool
    method: str
    first_party: bool
    api_key_present: bool
    cli_version: str


def sanitized_environment(source: Mapping[str, str], *, managed_home: str,
                          trusted_path: str = "/usr/bin:/bin", shell: str = "/bin/sh",
                          managed_tmp: str | None = None,
                          additions: Mapping[str, str] | None = None) -> dict[str, str]:
    home = Path(managed_home)
    shell_path = Path(shell)
    path_entries = trusted_path.split(":")
    if (not home.is_absolute() or not shell_path.is_absolute() or not path_entries
            or any(not value or not Path(value).is_absolute() or ".." in Path(value).parts
                   for value in path_entries)):
        raise ProviderPolicyError("provider home, shell, and trusted PATH must be absolute")
    env = {key: value for key, value in source.items() if key in ENV_ALLOWLIST}
    env.update({"HOME": os.fspath(home), "PATH": trusted_path, "SHELL": os.fspath(shell_path)})
    if managed_tmp is not None:
        temporary = Path(managed_tmp)
        if not temporary.is_absolute():
            raise ProviderPolicyError("provider temporary directory must be absolute")
        env["TMPDIR"] = os.fspath(temporary)
    for key, value in (additions or {}).items():
        if key not in ADDITION_ALLOWLIST:
            raise ProviderPolicyError(f"provider environment addition is not allowlisted: {key}")
        if not isinstance(value, str) or "\x00" in value:
            raise ProviderPolicyError(f"provider environment addition is invalid: {key}")
        if key == "SJG_WAKE_SOCKET" and not Path(value).is_absolute():
            raise ProviderPolicyError("wake socket environment value must be absolute")
        if key in {"SJG_SESSION_ID", "SJG_WAKE_ID"} and not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value):
            raise ProviderPolicyError(f"provider environment identity is invalid: {key}")
        if key == "AGENT_NAME" and not re.fullmatch(
                r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", value):
            raise ProviderPolicyError("provider agent role is invalid")
        env[key] = value
    if PROVIDER_SECRET_KEYS & env.keys():
        raise ProviderPolicyError("sanitized environment retained a forbidden credential")
    return env


def parse_auth_evidence(provider: str, raw: str, *, cli_version: str) -> AuthEvidence:
    """Parse the pinned CLI's exact read-only status shape. Unknown output fails closed."""
    if provider == "codex-cli" and raw.strip() == "Logged in using ChatGPT":
        return AuthEvidence(provider, True, "chatgpt", True, False, cli_version)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderPolicyError(f"{provider}: authentication status is not JSON") from exc
    if not isinstance(data, dict):
        raise ProviderPolicyError(f"{provider}: authentication status must be an object")
    if provider == "claude-cli":
        required = {"loggedIn", "authMethod", "apiProvider"}
        optional = {"email", "orgId", "orgName", "subscriptionType"}
        if not required <= set(data) or set(data) - required - optional:
            raise ProviderPolicyError("claude-cli authentication status shape is unrecognized")
        method = str(data.get("authMethod") or "").lower()
        first_party = str(data.get("apiProvider") or "").lower() == "firstparty"
        evidence = AuthEvidence(provider, data.get("loggedIn") is True, method, first_party,
                                method in {"api_key", "apikey"}, cli_version)
        if not evidence.logged_in or method != "claude.ai":
            raise ProviderPolicyError("claude-cli requires a first-party Claude subscription session")
    elif provider == "codex-cli":
        raise ProviderPolicyError("codex-cli authentication status shape is unrecognized")
    else:
        raise ProviderPolicyError(f"unsupported provider: {provider}")
    if not evidence.first_party or evidence.api_key_present:
        raise ProviderPolicyError(f"{provider}: alternate providers and API keys are forbidden")
    return evidence


def read_subscription_auth(provider: str, executable: str, *, allowed_versions: set[str],
                           environment: Mapping[str, str]) -> AuthEvidence:
    """Read exact first-party CLI status without starting a model or spending tokens."""
    forbidden = PROVIDER_SECRET_KEYS & environment.keys()
    if forbidden:
        raise ProviderPolicyError("provider status environment contains forbidden overrides: "
                                  + ", ".join(sorted(forbidden)))
    binary = Path(executable)
    if not binary.is_absolute() or not binary.is_file():
        raise ProviderPolicyError("provider executable must be an existing absolute file")
    try:
        version_process = subprocess.run(
            [executable, "--version"], text=True, capture_output=True, timeout=15,
            env=dict(environment), check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderPolicyError(f"{provider}: version check failed") from exc
    if version_process.returncode:
        raise ProviderPolicyError(f"{provider}: version check returned failure")
    version_output = version_process.stdout.strip()
    match = (re.fullmatch(r"codex-cli ([0-9]+\.[0-9]+\.[0-9]+)", version_output)
             if provider == "codex-cli" else
             re.fullmatch(r"([0-9]+\.[0-9]+\.[0-9]+) \(Claude Code\)", version_output))
    if match is None or match.group(1) not in allowed_versions:
        raise ProviderPolicyError(f"{provider}: uncommissioned CLI version: {version_output!r}")
    command = ([executable, "login", "status"] if provider == "codex-cli" else
               [executable, "auth", "status", "--json"] if provider == "claude-cli" else None)
    if command is None:
        raise ProviderPolicyError(f"unsupported provider: {provider}")
    try:
        status = subprocess.run(command, text=True, capture_output=True, timeout=15,
                                env=dict(environment), check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderPolicyError(f"{provider}: authentication status failed") from exc
    if status.returncode:
        raise ProviderPolicyError(f"{provider}: authentication status returned failure")
    return parse_auth_evidence(provider, status.stdout,
                               cli_version=match.group(1))


def validate_claude_settings(data: Mapping[str, object], *, profile: str) -> None:
    """Validate the security-bearing subset; unknown or weakened shapes fail closed."""
    if profile not in {"parent", "worker-readonly", "worker-implementer"}:
        raise ProviderPolicyError("unknown Claude settings profile")
    if set(data) != {"permissions", "sandbox", "hooks", "disableRemoteControl"}:
        raise ProviderPolicyError("Claude settings contain missing or unknown top-level fields")
    permissions = data.get("permissions")
    sandbox = data.get("sandbox")
    if not isinstance(permissions, dict) or not isinstance(sandbox, dict):
        raise ProviderPolicyError("Claude settings require permissions and sandbox objects")
    if set(permissions) != {"disableBypassPermissionsMode", "disableAutoMode", "allow", "deny"}:
        raise ProviderPolicyError("Claude permissions contain missing or unknown fields")
    parent_sandbox = {"enabled", "failIfUnavailable", "autoAllowBashIfSandboxed",
                      "allowUnsandboxedCommands", "filesystem", "excludedCommands", "network"}
    worker_sandbox = {"enabled", "failIfUnavailable", "allowUnsandboxedCommands",
                      "filesystem", "excludedCommands", "network"}
    if set(sandbox) != (parent_sandbox if profile == "parent" else worker_sandbox):
        raise ProviderPolicyError("Claude sandbox contains missing or unknown fields")
    if permissions.get("disableBypassPermissionsMode") != "disable":
        raise ProviderPolicyError("Claude bypass permission mode must be disabled")
    if permissions.get("disableAutoMode") != "disable":
        raise ProviderPolicyError("Claude auto permission mode must be disabled")
    denies = permissions.get("deny")
    if not isinstance(denies, list) or not all(isinstance(value, str) for value in denies):
        raise ProviderPolicyError("Claude permission denies must be strings")
    required = ({"Agent", "Task", "WebSearch", "WebFetch"}
                if profile != "parent" else set(CLAUDE_REQUIRED_DENIES))
    if not required.issubset(denies):
        raise ProviderPolicyError("Claude permission deny set is incomplete")
    if sandbox.get("enabled") is not True or sandbox.get("failIfUnavailable") is not True:
        raise ProviderPolicyError("Claude sandbox must be enabled and fail closed")
    if sandbox.get("allowUnsandboxedCommands") is not False:
        raise ProviderPolicyError("Claude unsandboxed-command escape hatch must be disabled")
    if sandbox.get("excludedCommands") != []:
        raise ProviderPolicyError("Claude sandbox excludedCommands must be empty")
    network = sandbox.get("network")
    if not isinstance(network, dict) or set(network) != {
            "deniedDomains", "allowUnixSockets", "allowAllUnixSockets"}:
        raise ProviderPolicyError("Claude sandbox network fields differ from sealed policy")
    if network.get("deniedDomains") != ["*"]:
        raise ProviderPolicyError("Claude subprocess network must be deny-all")
    if network.get("allowAllUnixSockets") not in (None, False):
        raise ProviderPolicyError("Claude may not access arbitrary Unix sockets")
    sockets = network.get("allowUnixSockets")
    if not isinstance(sockets, list) or len(sockets) > (1 if profile == "parent" else 0):
        raise ProviderPolicyError("Claude Unix-socket allowlist exceeds its profile")
    if any(not isinstance(value, str) or not Path(value).is_absolute() for value in sockets):
        raise ProviderPolicyError("Claude Unix sockets must be absolute paths")
    filesystem = sandbox.get("filesystem")
    if not isinstance(filesystem, dict) or set(filesystem) != {"denyRead", "denyWrite"}:
        raise ProviderPolicyError("Claude filesystem policy is missing or widened")
    deny_read = set(filesystem.get("denyRead") or [])
    deny_write = set(filesystem.get("denyWrite") or [])
    if not CLAUDE_SECRET_READS.issubset(deny_read):
        raise ProviderPolicyError("Claude secret-read denylist is incomplete")
    if not CLAUDE_SECRET_WRITES.issubset(deny_write):
        raise ProviderPolicyError("Claude secret-write denylist is incomplete")
    if data.get("hooks") != {} or data.get("disableRemoteControl") is not True:
        raise ProviderPolicyError("Claude hooks must be empty and remote control disabled")

    allowed_by_profile = {
        "parent": {"Read", "Glob", "Grep", "Edit", "Write", "Bash"},
        "worker-readonly": {"Read", "Glob", "Grep"},
        "worker-implementer": {"Read", "Glob", "Grep", "Edit", "Write"},
    }
    allowed = permissions.get("allow")
    if not isinstance(allowed, list) or set(allowed) != allowed_by_profile[profile]:
        raise ProviderPolicyError(f"Claude {profile} tool allowlist differs from sealed policy")
    if profile == "worker-readonly" and not {"Bash", "Edit", "Write"}.issubset(denies):
        raise ProviderPolicyError("Claude read-only worker has a write or shell escape")
    if profile == "worker-implementer" and "Bash" not in denies:
        raise ProviderPolicyError("Claude implementer worker may not receive a shell")


def load_claude_settings(path: str | Path, *, profile: str = "parent") -> dict[str, object]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderPolicyError(f"Claude settings are unreadable or invalid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ProviderPolicyError("Claude settings root must be an object")
    validate_claude_settings(data, profile=profile)
    return data


def validate_codex_config(path: str | Path) -> dict[str, object]:
    try:
        with Path(path).open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProviderPolicyError(f"Codex config is unreadable or invalid: {path}") from exc
    expected = {"sandbox_mode", "approval_policy", "allow_login_shell", "web_search",
                "sandbox_workspace_write", "history", "features", "tools",
                "shell_environment_policy"}
    if set(data) != expected:
        raise ProviderPolicyError("Codex config contains missing or unknown fields")
    if (data["sandbox_mode"] != "workspace-write" or data["approval_policy"] != "never"
            or data["allow_login_shell"] is not False or data["web_search"] != "disabled"):
        raise ProviderPolicyError("Codex top-level sandbox policy was weakened")
    workspace = data["sandbox_workspace_write"]
    if not isinstance(workspace, dict) or workspace != {
            "network_access": False, "exclude_slash_tmp": True,
            "exclude_tmpdir_env_var": True, "writable_roots": []}:
        raise ProviderPolicyError("Codex workspace sandbox differs from sealed policy")
    if data["history"] != {"persistence": "none"}:
        raise ProviderPolicyError("Codex history persistence must be disabled")
    features = data["features"]
    expected_features = {"multi_agent", "apps", "remote_plugin", "hooks", "memories",
                         "goals", "network_proxy"}
    if not isinstance(features, dict) or set(features) != expected_features or any(features.values()):
        raise ProviderPolicyError("Codex optional features differ from sealed policy")
    if data["tools"] != {"web_search": False}:
        raise ProviderPolicyError("Codex web search must be disabled")
    if data["shell_environment_policy"] != {
            "inherit": "none", "ignore_default_excludes": False,
            "experimental_use_profile": False}:
        raise ProviderPolicyError("Codex shell environment policy differs from sealed policy")
    rules = Path(path).parent / "rules" / "default.rules"
    if not rules.is_file() or rules.is_symlink():
        raise ProviderPolicyError("Codex execpolicy rules are absent or unsafe")
    if hashlib.sha256(rules.read_bytes()).hexdigest() != CODEX_EXEC_POLICY_SHA256:
        raise ProviderPolicyError("Codex execpolicy rules differ from the sealed policy")
    return data


def validate_workspace(path: str | Path) -> Path:
    """Reject project-local authority, secrets, and links outside the commissioned tree."""
    try:
        root = Path(path).resolve(strict=True)
    except OSError as exc:
        raise ProviderPolicyError(f"provider workspace is absent: {path}") from exc
    if not root.is_dir():
        raise ProviderPolicyError("provider workspace must be a directory")
    forbidden = {
        ".env", ".git-credentials", ".npmrc", ".pypirc", ".mcp.json",
        ".codex/config.toml", ".claude/settings.json", ".claude/settings.local.json",
    }
    for directory, dirs, files in os.walk(root):
        base = Path(directory)
        for name in list(dirs):
            candidate = base / name
            relative = candidate.relative_to(root).as_posix()
            if candidate.is_symlink():
                raise ProviderPolicyError(f"provider workspace contains a symlink: {relative}")
            if relative == ".git":
                dirs.remove(name)
        for name in files:
            candidate = base / name
            relative = candidate.relative_to(root).as_posix()
            if candidate.is_symlink():
                raise ProviderPolicyError(f"provider workspace contains a symlink: {relative}")
            local_authority = (candidate.name == ".env" or candidate.name.startswith(".env.")
                               or relative in forbidden
                               or relative.endswith("/.codex/config.toml")
                               or relative.endswith("/.claude/settings.json")
                               or relative.endswith("/.claude/settings.local.json"))
            if local_authority:
                raise ProviderPolicyError(f"provider workspace contains forbidden local authority: {relative}")
    return root


def materialize_claude_wake_settings(base_path: str | Path, destination: str | Path,
                                      *, wake_socket: str | Path) -> Path:
    """Create a supervisor-owned per-wake settings file with one broker socket."""
    data = load_claude_settings(base_path)
    socket_path = Path(wake_socket)
    if not socket_path.is_absolute():
        raise ProviderPolicyError("Claude wake socket must be absolute")
    network = data["sandbox"]["network"]  # type: ignore[index]
    network["allowUnixSockets"] = [os.fspath(socket_path)]  # type: ignore[index]
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    load_claude_settings(target, profile="parent")
    return target


def parent_command(provider: str, executable: str, *, model: str, workspace: str,
                   managed_root: str, settings_path: str | None = None) -> list[str]:
    root = Path(managed_root)
    if not Path(executable).is_absolute() or not Path(workspace).is_absolute() or not root.is_absolute():
        raise ProviderPolicyError("provider executable, workspace, and managed root must be absolute")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model):
        raise ProviderPolicyError("provider model id is invalid")
    workspace_root = validate_workspace(workspace)
    if provider == "codex-cli":
        validate_codex_config(root / "config.toml")
        return [executable, "--ask-for-approval", "never", "exec", "--strict-config",
                "--ephemeral", "--sandbox", "workspace-write", "--json", "--cd",
                os.fspath(workspace_root), "--model", model, "-"]
    if provider == "claude-cli":
        if not settings_path or not Path(settings_path).is_relative_to(root):
            raise ProviderPolicyError("Claude settings must be inside the supervisor-owned root")
        load_claude_settings(settings_path, profile="parent")
        return [executable, "-p", "--output-format", "stream-json", "--verbose",
                "--model", model, "--permission-mode", "dontAsk", "--settings",
                settings_path, "--setting-sources", "user", "--strict-mcp-config",
                "--safe-mode", "--no-chrome", "--disable-slash-commands",
                "--no-session-persistence", "--tools",
                "Read,Glob,Grep,Edit,Write,Bash",
                "--disallowedTools", "Agent,Task,WebSearch,WebFetch"]
    raise ProviderPolicyError(f"unsupported provider: {provider}")


def worker_command(provider: str, executable: str, *, model: str, workspace: str,
                   managed_root: str, worker_type: str) -> list[str]:
    """Construct a non-recursive worker launch with no broker or network capability."""
    if worker_type not in {"researcher", "drafter", "implementer"}:
        raise ProviderPolicyError("unknown named worker type")
    root = Path(managed_root)
    if not Path(executable).is_absolute() or not root.is_absolute():
        raise ProviderPolicyError("worker executable and managed root must be absolute")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model):
        raise ProviderPolicyError("worker model id is invalid")
    scratch = validate_workspace(workspace)
    if provider == "codex-cli":
        validate_codex_config(root / "config.toml")
        sandbox = "workspace-write" if worker_type == "implementer" else "read-only"
        return [executable, "--ask-for-approval", "never", "exec", "--strict-config",
                "--ephemeral", "--sandbox", sandbox, "--json", "--cd", os.fspath(scratch),
                "--model", model, "-"]
    if provider == "claude-cli":
        profile = "worker-implementer" if worker_type == "implementer" else "worker-readonly"
        settings = root / f"{profile}-settings.json"
        load_claude_settings(settings, profile=profile)
        tools = "Read,Glob,Grep,Edit,Write" if worker_type == "implementer" else "Read,Glob,Grep"
        return [executable, "-p", "--output-format", "stream-json", "--verbose",
                "--model", model, "--permission-mode", "dontAsk", "--settings",
                os.fspath(settings), "--setting-sources", "user", "--strict-mcp-config",
                "--safe-mode", "--no-chrome", "--disable-slash-commands",
                "--no-session-persistence", "--tools", tools,
                "--disallowedTools", "Agent,Task,Bash,WebSearch,WebFetch"]
    raise ProviderPolicyError(f"unsupported provider: {provider}")
