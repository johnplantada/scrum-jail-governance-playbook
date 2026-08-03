"""Strict host-owned configuration for the privileged 1.0 supervisor."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import os
import re
import stat

from .policy import OrganizationPolicy


class HostConfigError(ValueError):
    """Host integration configuration is absent, unsafe, or ambiguous."""


@dataclass(frozen=True)
class GitHubHostConfig:
    executable: str
    token_file: str
    home: str
    project_owner: str | None
    project_number: int | None


@dataclass(frozen=True)
class ProviderHostConfig:
    provider: str
    executable: str
    managed_home: str
    policy_root: str
    allowed_versions: frozenset[str]
    role_models: dict[str, str]
    worker_models: dict[str, str]
    timeout_seconds: int


@dataclass(frozen=True)
class SupervisorConfig:
    organization: str
    wake_rules: str
    wake_rules_sha256: str
    state_database: str
    admin_socket: str
    wake_socket_directory: str
    scratch_directory: str
    workspaces: dict[str, dict[str, str]]
    github: GitHubHostConfig
    provider: ProviderHostConfig
    operator_uid: int
    agent_uid: int
    agent_gid: int
    mode: str
    poll_interval_seconds: int
    session_ttl_seconds: int
    delivery_lease_seconds: int
    role_token_reservations: dict[str, int]
    initial_watermark: str


def _exact(value: Any, fields: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        actual = set(value) if isinstance(value, dict) else set()
        missing = sorted(fields - actual)
        extra = sorted(actual - fields)
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unknown " + ", ".join(extra))
        raise HostConfigError(f"{path} has invalid fields: {'; '.join(detail) or 'not an object'}")
    return value


def _absolute(value: Any, path: str, *, must_exist: bool = False,
              directory: bool | None = None) -> str:
    candidate = Path(str(value or ""))
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise HostConfigError(f"{path} must be an absolute normalized path")
    if must_exist:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise HostConfigError(f"{path} does not exist") from exc
        if candidate.is_symlink():
            raise HostConfigError(f"{path} must not be a symlink")
        if directory is True and not resolved.is_dir():
            raise HostConfigError(f"{path} must be a directory")
        if directory is False and not resolved.is_file():
            raise HostConfigError(f"{path} must be a regular file")
    return os.fspath(candidate)


def validate_secret_file(path: str | Path) -> Path:
    """Require a service-owned, non-link credential inaccessible to group/other."""
    candidate = Path(path)
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise HostConfigError(f"credential file is unavailable: {candidate}") from exc
    if not stat.S_ISREG(metadata.st_mode) or candidate.is_symlink():
        raise HostConfigError("credential path must be a regular non-symlink file")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise HostConfigError("credential file must not be accessible to group or other")
    if metadata.st_uid != os.geteuid():
        raise HostConfigError("credential file must be owned by the supervisor identity")
    return candidate


def read_github_token(config: GitHubHostConfig) -> str:
    token = validate_secret_file(config.token_file).read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{19,511}", token):
        raise HostConfigError("GitHub service credential has an invalid shape")
    return token


def load_supervisor_config(path: str | Path, policy: OrganizationPolicy) -> SupervisorConfig:
    config_path = Path(path)
    try:
        metadata = config_path.lstat()
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HostConfigError(f"supervisor configuration is unreadable or invalid: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode) or config_path.is_symlink():
        raise HostConfigError("supervisor configuration must be a regular non-symlink file")
    if metadata.st_uid != os.geteuid():
        raise HostConfigError("supervisor configuration must be owned by the supervisor identity")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise HostConfigError("supervisor configuration must not be group/world writable")
    root = _exact(data, {
        "organization", "wakeRules", "wakeRulesSha256", "stateDatabase", "adminSocket", "wakeSocketDirectory",
        "scratchDirectory", "workspaces", "github", "provider", "operatorUid", "agentUid",
        "agentGid", "mode", "pollIntervalSeconds", "sessionTtlSeconds",
        "deliveryLeaseSeconds", "roleTokenReservations", "initialWatermark",
    }, "supervisor")

    github_data = _exact(root["github"], {
        "executable", "tokenFile", "home", "projectOwner", "projectNumber",
    }, "github")
    executable = _absolute(github_data["executable"], "github.executable", must_exist=True,
                           directory=False)
    token_file = _absolute(github_data["tokenFile"], "github.tokenFile")
    home = _absolute(github_data["home"], "github.home", must_exist=True, directory=True)
    project_owner = github_data["projectOwner"]
    project_number = github_data["projectNumber"]
    if (project_owner is None) != (project_number is None):
        raise HostConfigError("GitHub Project owner and number must be configured together")
    if project_owner is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", str(project_owner)):
        raise HostConfigError("github.projectOwner is invalid")
    if project_number is not None and (not isinstance(project_number, int) or project_number <= 0):
        raise HostConfigError("github.projectNumber must be positive")
    github = GitHubHostConfig(executable, token_file, home,
                              str(project_owner) if project_owner is not None else None,
                              project_number)

    provider_data = _exact(root["provider"], {
        "kind", "executable", "managedHome", "policyRoot", "allowedVersions",
        "roleModels", "workerModels", "timeoutSeconds",
    }, "provider")
    provider_kind = str(provider_data["kind"])
    if provider_kind not in {"codex-cli", "claude-cli"}:
        raise HostConfigError("provider.kind must be codex-cli or claude-cli")
    provider_executable = _absolute(provider_data["executable"], "provider.executable",
                                    must_exist=True, directory=False)
    managed_home = _absolute(provider_data["managedHome"], "provider.managedHome",
                             must_exist=True, directory=True)
    policy_root = _absolute(provider_data["policyRoot"], "provider.policyRoot",
                            must_exist=False)
    policy_candidate = Path(policy_root)
    if policy_candidate.exists() and (policy_candidate.is_symlink()
                                      or not policy_candidate.is_dir()):
        raise HostConfigError("provider.policyRoot must be a non-symlink directory when present")
    versions = provider_data["allowedVersions"]
    if (not isinstance(versions, list) or not versions
            or any(not isinstance(value, str)
                   or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value)
                   for value in versions)
            or len(versions) != len(set(versions))):
        raise HostConfigError("provider.allowedVersions must be non-empty semantic versions")
    role_models = provider_data["roleModels"]
    worker_models = provider_data["workerModels"]
    if not isinstance(role_models, dict) or set(role_models) != set(policy.roles):
        raise HostConfigError("provider.roleModels must map every and only configured role")
    if not isinstance(worker_models, dict) or set(worker_models) != {
            "researcher", "drafter", "implementer"}:
        raise HostConfigError("provider.workerModels must map all three named worker types")
    model_pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
    if any(not isinstance(value, str) or not model_pattern.fullmatch(value) for value in
           [*role_models.values(), *worker_models.values()]):
        raise HostConfigError("provider model ids must use the sealed model-id grammar")
    timeout = provider_data["timeoutSeconds"]
    if not isinstance(timeout, int) or not 30 <= timeout <= 86400:
        raise HostConfigError("provider.timeoutSeconds must be between 30 and 86400")
    provider = ProviderHostConfig(
        provider_kind, provider_executable, managed_home, policy_root,
        frozenset(str(value) for value in versions),
        {str(key): str(value) for key, value in role_models.items()},
        {str(key): str(value) for key, value in worker_models.items()}, timeout,
    )

    workspaces = root["workspaces"]
    if not isinstance(workspaces, dict) or set(workspaces) != set(policy.roles):
        raise HostConfigError("workspaces must map every and only configured role")
    compiled_workspaces: dict[str, dict[str, str]] = {}
    for role, aliases in workspaces.items():
        expected = set(policy.role(role).repositories)
        if not isinstance(aliases, dict) or set(aliases) != expected:
            raise HostConfigError(f"workspaces.{role} must map its exact repository scope")
        compiled_workspaces[role] = {
            alias: _absolute(value, f"workspaces.{role}.{alias}", must_exist=True,
                             directory=True)
            for alias, value in aliases.items()
        }

    reservations = root["roleTokenReservations"]
    if not isinstance(reservations, dict) or set(reservations) != set(policy.roles):
        raise HostConfigError("roleTokenReservations must map every and only configured role")
    compiled_reservations: dict[str, int] = {}
    for role, value in reservations.items():
        if not isinstance(value, int) or value <= 0 or value > policy.role(role).daily_token_budget:
            raise HostConfigError(f"roleTokenReservations.{role} exceeds its daily role envelope")
        if value > policy.organization_daily_token_budget:
            raise HostConfigError(f"roleTokenReservations.{role} exceeds the organization envelope")
        compiled_reservations[role] = value

    integers = {
        "operatorUid": root["operatorUid"], "agentUid": root["agentUid"],
        "agentGid": root["agentGid"], "pollIntervalSeconds": root["pollIntervalSeconds"],
        "sessionTtlSeconds": root["sessionTtlSeconds"],
        "deliveryLeaseSeconds": root["deliveryLeaseSeconds"],
    }
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
           for value in integers.values()):
        raise HostConfigError("supervisor UID and interval fields must be non-negative integers")
    if integers["operatorUid"] == integers["agentUid"]:
        raise HostConfigError("operator and agent identities must use different OS UIDs")
    if integers["agentUid"] == 0 or integers["agentGid"] == 0:
        raise HostConfigError("agent UID and GID must be unprivileged")
    if integers["pollIntervalSeconds"] < 5:
        raise HostConfigError("pollIntervalSeconds must be at least 5")
    if integers["sessionTtlSeconds"] < 60 or integers["deliveryLeaseSeconds"] < 60:
        raise HostConfigError("session and delivery leases must be at least 60 seconds")
    if (integers["sessionTtlSeconds"] < timeout + 60
            or integers["deliveryLeaseSeconds"] < timeout + 60):
        raise HostConfigError(
            "session and delivery leases must exceed the provider timeout by at least 60 seconds")
    if root["mode"] not in {"shadow", "live"}:
        raise HostConfigError("mode must be shadow or live")
    watermark = str(root["initialWatermark"])
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", watermark):
        raise HostConfigError("initialWatermark must be UTC RFC3339 seconds")
    wake_rules_sha256 = str(root["wakeRulesSha256"])
    if not re.fullmatch(r"[0-9a-f]{64}", wake_rules_sha256):
        raise HostConfigError("wakeRulesSha256 must be a SHA-256 digest")

    authority_paths = [
        config_path.resolve(), Path(token_file).resolve(strict=False),
        Path(home).resolve(), Path(managed_home).resolve(), Path(policy_root).resolve(strict=False),
        Path(str(root["stateDatabase"])).resolve(strict=False),
        Path(str(root["adminSocket"])).resolve(strict=False),
        Path(str(root["wakeSocketDirectory"])).resolve(strict=False),
        Path(str(root["scratchDirectory"])).resolve(strict=False),
    ]
    for aliases in compiled_workspaces.values():
        for workspace in aliases.values():
            workspace_root = Path(workspace).resolve()
            if any(candidate == workspace_root or workspace_root in candidate.parents
                   or candidate in workspace_root.parents
                   for candidate in authority_paths):
                raise HostConfigError("host authority or mutable state must remain outside agent workspaces")

    return SupervisorConfig(
        _absolute(root["organization"], "organization", must_exist=True, directory=False),
        _absolute(root["wakeRules"], "wakeRules", must_exist=True, directory=False),
        wake_rules_sha256,
        _absolute(root["stateDatabase"], "stateDatabase"),
        _absolute(root["adminSocket"], "adminSocket"),
        _absolute(root["wakeSocketDirectory"], "wakeSocketDirectory"),
        _absolute(root["scratchDirectory"], "scratchDirectory"),
        compiled_workspaces, github, provider, integers["operatorUid"], integers["agentUid"],
        integers["agentGid"], str(root["mode"]), integers["pollIntervalSeconds"],
        integers["sessionTtlSeconds"], integers["deliveryLeaseSeconds"],
        compiled_reservations, watermark,
    )
