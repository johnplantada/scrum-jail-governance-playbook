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
CODEX_TEMPLATE_HOME = Path("/__SJG_MANAGED_HOME__")
CODEX_TEMPLATE_CONTROL_ROOT = Path("/__SJG_CONTROL_ROOT__")
CODEX_PARENT_PROFILE = "scrum-jail-parent"
CODEX_READONLY_PROFILE = "scrum-jail-worker-readonly"
CODEX_IMPLEMENTER_PROFILE = "scrum-jail-worker-implementer"


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
    env.update({"HOME": os.fspath(home), "PATH": trusted_path, "SHELL": os.fspath(shell_path),
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"})
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
                           environment: Mapping[str, str],
                           process_identity: Mapping[str, object] | None = None) -> AuthEvidence:
    """Read exact first-party CLI status without starting a model or spending tokens."""
    forbidden = PROVIDER_SECRET_KEYS & environment.keys()
    if forbidden:
        raise ProviderPolicyError("provider status environment contains forbidden overrides: "
                                  + ", ".join(sorted(forbidden)))
    binary = Path(executable)
    if not binary.is_absolute() or not binary.is_file():
        raise ProviderPolicyError("provider executable must be an existing absolute file")
    identity = dict(process_identity or {})
    if set(identity) - {"user", "group", "extra_groups", "umask"}:
        raise ProviderPolicyError("provider process identity contains unknown controls")
    for field in ("user", "group", "umask"):
        if field in identity and (not isinstance(identity[field], int) or identity[field] < 0):
            raise ProviderPolicyError("provider process identity is invalid")
    groups = identity.get("extra_groups", [])
    if (not isinstance(groups, list)
            or any(not isinstance(value, int) or value < 0 for value in groups)):
        raise ProviderPolicyError("provider supplementary groups are invalid")
    try:
        version_process = subprocess.run(
            [executable, "--version"], text=True, capture_output=True, timeout=15,
            env=dict(environment), check=False, **identity,
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
                                env=dict(environment), check=False, **identity)
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
    if not isinstance(filesystem, dict) or set(filesystem) != {
            "allowRead", "denyRead", "denyWrite"}:
        raise ProviderPolicyError("Claude filesystem policy is missing or widened")
    allow_read = filesystem.get("allowRead")
    if (not isinstance(allow_read, list)
            or any(not isinstance(value, str) or not Path(value).is_absolute()
                   or ".." in Path(value).parts for value in allow_read)):
        raise ProviderPolicyError("Claude read allowlist must contain normalized absolute paths")
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


def validate_codex_config(path: str | Path, *, managed_home: str | Path | None = None,
                          control_root: str | Path | None = None) -> dict[str, object]:
    try:
        with Path(path).open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProviderPolicyError(f"Codex config is unreadable or invalid: {path}") from exc
    expected = {"approval_policy", "allow_login_shell", "web_search", "default_permissions",
                "permissions", "history", "features", "tools", "shell_environment_policy"}
    if set(data) != expected:
        raise ProviderPolicyError("Codex config contains missing or unknown fields")
    if (data["approval_policy"] != "never" or data["allow_login_shell"] is not False
            or data["web_search"] != "disabled"
            or data["default_permissions"] != CODEX_PARENT_PROFILE):
        raise ProviderPolicyError("Codex top-level sandbox policy was weakened")
    home = Path(managed_home) if managed_home is not None else CODEX_TEMPLATE_HOME
    release = (Path(control_root) if control_root is not None else
               Path(__file__).resolve().parents[2] if managed_home is not None else
               CODEX_TEMPLATE_CONTROL_ROOT)
    if (not home.is_absolute() or ".." in home.parts or not release.is_absolute()
            or ".." in release.parts):
        raise ProviderPolicyError(
            "Codex managed home and control root must be absolute normalized paths")
    denied_root = os.fspath(home / ".codex")
    network = {"enabled": False, "allow_local_binding": False,
               "dangerously_allow_all_unix_sockets": False}
    profiles = data["permissions"]
    expected_profiles = {
        CODEX_PARENT_PROFILE: {
            "filesystem": {":minimal": "read", ":workspace_roots": {".": "write"},
                           denied_root: "deny", os.fspath(release): "read"},
            "network": network,
        },
        CODEX_READONLY_PROFILE: {
            "filesystem": {":minimal": "read", ":workspace_roots": {".": "read"},
                           denied_root: "deny"},
            "network": network,
        },
        CODEX_IMPLEMENTER_PROFILE: {
            "filesystem": {":minimal": "read", ":workspace_roots": {".": "write"},
                           denied_root: "deny"},
            "network": network,
        },
    }
    if not isinstance(profiles, dict) or profiles != expected_profiles:
        raise ProviderPolicyError("Codex named filesystem profiles differ from sealed policy")
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


def materialize_codex_policy(source_root: str | Path, managed_home: str | Path) -> Path:
    """Install a path-bound Codex policy beside auth without copying any credential."""
    source = Path(source_root)
    home = Path(managed_home)
    if not source.is_absolute() or not home.is_absolute() or ".." in home.parts:
        raise ProviderPolicyError("Codex policy source and managed home must be absolute")
    template = source / "config.toml"
    validate_codex_config(template)
    rules = source / "rules" / "default.rules"
    if (not rules.is_file() or rules.is_symlink()
            or hashlib.sha256(rules.read_bytes()).hexdigest() != CODEX_EXEC_POLICY_SHA256):
        raise ProviderPolicyError("Codex source execpolicy differs from the sealed policy")
    target = home / ".codex"
    if target.exists() and (target.is_symlink() or not target.is_dir()):
        raise ProviderPolicyError("Codex managed policy destination is unsafe")
    target.mkdir(parents=True, exist_ok=True)
    rendered = template.read_text(encoding="utf-8").replace(
        os.fspath(CODEX_TEMPLATE_HOME), os.fspath(home)).replace(
            os.fspath(CODEX_TEMPLATE_CONTROL_ROOT),
            os.fspath(Path(__file__).resolve().parents[2]))

    def install(destination: Path, content: bytes, mode: int) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.",
                                                  dir=destination.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, destination)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    install(target / "config.toml", rendered.encode("utf-8"), 0o444)
    rules_target = target / "rules"
    if rules_target.exists() and (rules_target.is_symlink() or not rules_target.is_dir()
                                  or rules_target.stat().st_uid != os.geteuid()):
        raise ProviderPolicyError("Codex managed rules directory is not supervisor-owned")
    rules_target.mkdir(parents=True, exist_ok=True)
    os.chmod(rules_target, 0o711)
    install(rules_target / "default.rules", rules.read_bytes(), 0o444)
    validate_codex_config(target / "config.toml", managed_home=home)
    return target


def materialize_claude_policy(source_root: str | Path, target_root: str | Path) -> Path:
    """Install only the three validated, sealed Claude settings profiles."""
    source = Path(source_root)
    target = Path(target_root)
    if not source.is_absolute() or not target.is_absolute():
        raise ProviderPolicyError("Claude policy source and destination must be absolute")
    if target.exists() and (target.is_symlink() or not target.is_dir()):
        raise ProviderPolicyError("Claude managed policy destination is unsafe")
    target.mkdir(parents=True, exist_ok=True)
    if target.stat().st_uid != os.geteuid():
        raise ProviderPolicyError("Claude managed policy destination is not supervisor-owned")
    os.chmod(target, 0o711)
    for profile in ("parent", "worker-readonly", "worker-implementer"):
        source_path = source / f"{profile}-settings.json"
        load_claude_settings(source_path, profile=profile)
        destination = target / source_path.name
        descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=target)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(source_path.read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o444)
            os.replace(temporary, destination)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        load_claude_settings(destination, profile=profile)
    return target


def codex_profile_override(profile: str, section: str, path: str | Path,
                           access: str) -> str:
    if profile not in {CODEX_PARENT_PROFILE, CODEX_READONLY_PROFILE,
                       CODEX_IMPLEMENTER_PROFILE}:
        raise ProviderPolicyError("unknown Codex permissions profile")
    if section != "network.unix_sockets":
        raise ProviderPolicyError("unknown Codex profile override section")
    candidate = Path(path)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise ProviderPolicyError("Codex profile override path must be absolute")
    if access != "allow":
        raise ProviderPolicyError("Codex profile override access is invalid")
    # Override the socket map as one TOML inline table. Codex's dotted override parser
    # otherwise splits dots inside quoted path keys (for example, `wake.sock`).
    return (f"permissions.{profile}.{section}={{"
            f"{json.dumps(os.fspath(candidate))}={json.dumps(access)}}}")


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


def materialize_claude_runtime_settings(
        base_path: str | Path, destination: str | Path, *, profile: str,
        workspace: str | Path, deny_read_roots: tuple[str | Path, ...],
        wake_socket: str | Path | None = None) -> Path:
    """Create one path-bound Claude settings file for a parent or named worker."""
    data = load_claude_settings(base_path, profile=profile)
    workspace_path = Path(workspace)
    if not workspace_path.is_absolute() or ".." in workspace_path.parts:
        raise ProviderPolicyError("Claude runtime workspace must be absolute")
    roots = [Path(value) for value in deny_read_roots]
    if any(not value.is_absolute() or ".." in value.parts for value in roots):
        raise ProviderPolicyError("Claude runtime deny roots must be absolute")
    filesystem = data["sandbox"]["filesystem"]  # type: ignore[index]
    filesystem["allowRead"] = [os.fspath(workspace_path)]  # type: ignore[index]
    filesystem["denyRead"] = sorted(set(filesystem["denyRead"])  # type: ignore[index]
                                    | {os.fspath(value) for value in roots})
    network = data["sandbox"]["network"]  # type: ignore[index]
    if wake_socket is not None:
        socket_path = Path(wake_socket)
        if not socket_path.is_absolute() or ".." in socket_path.parts:
            raise ProviderPolicyError("Claude wake socket must be absolute")
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
        os.chmod(temporary, 0o444)
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    load_claude_settings(target, profile=profile)
    return target


def materialize_claude_wake_settings(
        base_path: str | Path, destination: str | Path, *, wake_socket: str | Path,
        workspace: str | Path,
        deny_read_roots: tuple[str | Path, ...] = ()) -> Path:
    """Create the sealed path-bound parent settings file."""
    return materialize_claude_runtime_settings(
        base_path, destination, profile="parent",
        workspace=workspace,
        deny_read_roots=deny_read_roots, wake_socket=wake_socket)


def parent_command(provider: str, executable: str, *, model: str, workspace: str,
                   managed_root: str, settings_path: str | None = None,
                   wake_socket: str | None = None) -> list[str]:
    root = Path(managed_root)
    if not Path(executable).is_absolute() or not Path(workspace).is_absolute() or not root.is_absolute():
        raise ProviderPolicyError("provider executable, workspace, and managed root must be absolute")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model):
        raise ProviderPolicyError("provider model id is invalid")
    workspace_root = validate_workspace(workspace)
    if provider == "codex-cli":
        validate_codex_config(root / "config.toml", managed_home=root.parent)
        socket_path = Path(wake_socket or "")
        if not socket_path.is_absolute() or ".." in socket_path.parts:
            raise ProviderPolicyError("Codex parent requires an absolute wake socket")
        socket_override = codex_profile_override(
            CODEX_PARENT_PROFILE, "network.unix_sockets", socket_path, "allow")
        return [executable, "--ask-for-approval", "never", "-c",
                f'default_permissions="{CODEX_PARENT_PROFILE}"', "-c", socket_override,
                "exec", "--strict-config", "--ephemeral", "--json", "--cd",
                os.fspath(workspace_root), "--model", model, "-"]
    if provider == "claude-cli":
        if not settings_path or not Path(settings_path).is_relative_to(root):
            raise ProviderPolicyError("Claude settings must be inside the supervisor-owned root")
        load_claude_settings(settings_path, profile="parent")
        return [executable, "-p", "--output-format", "stream-json", "--verbose",
                "--model", model, "--permission-mode", "dontAsk", "--settings",
                settings_path, "--setting-sources", "", "--strict-mcp-config",
                "--safe-mode", "--no-chrome", "--disable-slash-commands",
                "--no-session-persistence", "--tools",
                "Read,Glob,Grep,Edit,Write,Bash",
                "--disallowedTools", "Agent,Task,WebSearch,WebFetch"]
    raise ProviderPolicyError(f"unsupported provider: {provider}")


def worker_command(provider: str, executable: str, *, model: str, workspace: str,
                   managed_root: str, worker_type: str,
                   settings_path: str | None = None) -> list[str]:
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
        validate_codex_config(root / "config.toml", managed_home=root.parent)
        profile = (CODEX_IMPLEMENTER_PROFILE if worker_type == "implementer"
                   else CODEX_READONLY_PROFILE)
        return [executable, "--ask-for-approval", "never", "-c",
                f'default_permissions="{profile}"', "exec", "--strict-config",
                "--ephemeral", "--json", "--cd", os.fspath(scratch),
                "--model", model, "-"]
    if provider == "claude-cli":
        profile = "worker-implementer" if worker_type == "implementer" else "worker-readonly"
        settings = Path(settings_path) if settings_path is not None else root / f"{profile}-settings.json"
        if not settings.is_relative_to(root):
            raise ProviderPolicyError("Claude worker settings must be inside its managed root")
        load_claude_settings(settings, profile=profile)
        tools = "Read,Glob,Grep,Edit,Write" if worker_type == "implementer" else "Read,Glob,Grep"
        return [executable, "-p", "--output-format", "stream-json", "--verbose",
                "--model", model, "--permission-mode", "dontAsk", "--settings",
                os.fspath(settings), "--setting-sources", "", "--strict-mcp-config",
                "--safe-mode", "--no-chrome", "--disable-slash-commands",
                "--no-session-persistence", "--tools", tools,
                "--disallowedTools", "Agent,Task,Bash,WebSearch,WebFetch"]
    raise ProviderPolicyError(f"unsupported provider: {provider}")
