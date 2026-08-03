"""Typed, clean-break organization policy compilation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import hashlib
import json
import re


ROLE_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
CANONICAL_ROLES = (
    "human-principal",
    "portfolio-orchestrator",
    "project-orchestrator",
    "commercial-planner",
    "software-implementer",
    "governance-reviewer",
    "policy-reviewer",
)
REQUIRED_EXECUTING_ROLES = frozenset({
    "project-orchestrator",
    "commercial-planner",
    "software-implementer",
    "governance-reviewer",
})
OPTIONAL_EXECUTING_ROLES = frozenset({"policy-reviewer"})
EXECUTING_ROLES = REQUIRED_EXECUTING_ROLES | OPTIONAL_EXECUTING_ROLES


class PolicyError(ValueError):
    """The desired organization cannot be compiled into enforceable policy."""


@dataclass(frozen=True)
class Repository:
    alias: str
    slug: str
    default_branch: str = "main"


@dataclass(frozen=True)
class RolePolicy:
    role: str
    worker_types: frozenset[str] = frozenset()
    max_workers_per_wake: int = 0
    max_workers_concurrent: int = 0
    repositories: frozenset[str] = frozenset({"org"})
    daily_token_budget: int = 1


@dataclass(frozen=True)
class OrganizationPolicy:
    org_id: str
    principal_github: str
    repositories: dict[str, Repository]
    roles: dict[str, RolePolicy]
    source_commit: str
    control_version: str
    source_tree_sha256: str
    organization_daily_token_budget: int
    global_max_agents: int
    policy_digest: str = field(compare=False)
    protected_submission_paths: tuple[str, ...] = (
        ".github/workflows/",
        ".github/actions/",
        ".gitmodules",
    )

    def role(self, role: str) -> RolePolicy:
        try:
            return self.roles[role]
        except KeyError as exc:
            raise PolicyError(f"unknown executing role: {role}") from exc

    def repository(self, alias: str) -> Repository:
        try:
            return self.repositories[alias]
        except KeyError as exc:
            raise PolicyError(f"unknown repository alias: {alias}") from exc


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _exact_keys(value: Any, expected: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError(f"{path} must be a mapping")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unknown " + ", ".join(extra))
        raise PolicyError(f"{path} has invalid fields: {'; '.join(detail)}")
    return value


def compile_policy(data: dict[str, Any], *, source_commit: str) -> OrganizationPolicy:
    """Compile portable desired state into a closed, deterministic policy.

    Compatibility role aliases and arbitrary custom roles are intentionally rejected.
    Human Principal and Portfolio Orchestrator remain canonical identities but are not
    silently converted into ordinary project-agent sessions.
    """
    if not isinstance(data, dict):
        raise PolicyError("organization policy must be a mapping")
    _exact_keys(data, {"apiVersion", "kind", "metadata", "authority", "repositories",
                       "controlRelease", "budgets", "limits", "agents", "workflows"},
                "organization")
    if data.get("apiVersion") != "scrum-jail.dev/v1" or data.get("kind") != "Organization":
        raise PolicyError("organization apiVersion or kind is unsupported")
    metadata = _exact_keys(data.get("metadata"), {"id"}, "metadata")
    authority = _exact_keys(data.get("authority"),
                            {"principalRole", "principalGitHub", "upstreamRole"}, "authority")
    if authority.get("principalRole") != "human-principal":
        raise PolicyError("authority.principalRole must be human-principal")
    if authority.get("upstreamRole") != "portfolio-orchestrator":
        raise PolicyError("authority.upstreamRole must be portfolio-orchestrator")
    org_id = str(metadata.get("id") or "").strip()
    principal = str(authority.get("principalGitHub") or "").strip()
    control = _exact_keys(data.get("controlRelease"), {"version", "sourceTreeSha256"},
                          "controlRelease")
    control_version = str(control.get("version") or "")
    source_tree_sha256 = str(control.get("sourceTreeSha256") or "")
    budgets = _exact_keys(data.get("budgets"), {"organizationDailyTokens"}, "budgets")
    organization_daily_token_budget = int(budgets.get("organizationDailyTokens") or 0)
    limits = _exact_keys(data.get("limits"), {"globalMaxAgents"}, "limits")
    global_max_agents = int(limits.get("globalMaxAgents") or 0)
    if not ROLE_ID.fullmatch(org_id):
        raise PolicyError("metadata.id must be lowercase kebab-case")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", principal):
        raise PolicyError("authority.principalGitHub is not a valid GitHub login")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", control_version):
        raise PolicyError("controlRelease.version must be semantic x.y.z")
    if not re.fullmatch(r"[0-9a-f]{64}", source_tree_sha256):
        raise PolicyError("controlRelease.sourceTreeSha256 must be a SHA-256 digest")
    if organization_daily_token_budget <= 0:
        raise PolicyError("budgets.organizationDailyTokens must be positive")
    if global_max_agents <= 0:
        raise PolicyError("limits.globalMaxAgents must be positive")

    repository_data = _exact_keys(data.get("repositories"), {"org", "product"}, "repositories")
    repos: dict[str, Repository] = {}
    for alias in ("org", "product"):
        raw = _exact_keys(repository_data.get(alias), {"slug", "defaultBranch"},
                          f"repositories.{alias}")
        slug = str(raw.get("slug") or "").strip()
        default = str(raw.get("defaultBranch") or "main").strip()
        if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug)
                or any(component in {".", ".."} for component in slug.split("/"))):
            raise PolicyError(f"repositories.{alias}.slug must be OWNER/REPO")
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", default) or ".." in default:
            raise PolicyError(f"repositories.{alias}.defaultBranch is invalid")
        repos[alias] = Repository(alias, slug, default)

    roles: dict[str, RolePolicy] = {}
    raw_roles = data.get("agents")
    if not isinstance(raw_roles, list):
        raise PolicyError("agents must be a list")
    for raw in raw_roles:
        if not isinstance(raw, dict):
            raise PolicyError("each agent must be a mapping")
        _exact_keys(raw, {"id", "repositories", "dailyTokenBudget", "workers"}, "agent")
        role = str(raw.get("id") or "").strip()
        if role not in EXECUTING_ROLES:
            raise PolicyError(f"unknown or non-executing canonical role: {role!r}")
        if role in roles:
            raise PolicyError(f"duplicate role: {role}")
        workers = _exact_keys(raw.get("workers"),
                              {"allowed", "maxPerWake", "maxConcurrent"}, f"{role}.workers")
        allowed = frozenset(str(v) for v in workers.get("allowed") or [])
        if not allowed <= {"researcher", "drafter", "implementer"}:
            raise PolicyError(f"{role}: unknown worker type")
        max_per_wake = int(workers.get("maxPerWake") or 0)
        max_concurrent = int(workers.get("maxConcurrent") or 0)
        if min(max_per_wake, max_concurrent) < 0 or max_concurrent > max_per_wake:
            raise PolicyError(f"{role}: invalid worker limits")
        if bool(allowed) != bool(max_per_wake) or bool(allowed) != bool(max_concurrent):
            raise PolicyError(f"{role}: worker types and nonzero limits must be declared together")
        expected_workers = {
            "project-orchestrator": frozenset(),
            "governance-reviewer": frozenset(),
            "commercial-planner": frozenset({"researcher", "drafter"}),
            "policy-reviewer": frozenset({"researcher", "drafter"}),
            "software-implementer": frozenset({"researcher", "drafter", "implementer"}),
        }[role]
        if allowed != expected_workers:
            raise PolicyError(f"{role}: worker capabilities differ from the canonical role")
        repo_aliases = frozenset(str(v) for v in raw.get("repositories") or ["org"])
        expected_repositories = (frozenset({"org", "product"})
                                 if role == "software-implementer" else frozenset({"org"}))
        if repo_aliases != expected_repositories:
            raise PolicyError(f"{role}: repository scope differs from the canonical role")
        daily_token_budget = int(raw.get("dailyTokenBudget") or 0)
        if daily_token_budget <= 0 or daily_token_budget > organization_daily_token_budget:
            raise PolicyError(f"{role}: dailyTokenBudget must be positive and within the organization budget")
        roles[role] = RolePolicy(role, allowed, max_per_wake, max_concurrent,
                                 repo_aliases, daily_token_budget)

    missing = REQUIRED_EXECUTING_ROLES - roles.keys()
    if missing:
        raise PolicyError(f"missing required roles: {', '.join(sorted(missing))}")
    expected_global = len(roles) + sum(role.max_workers_concurrent for role in roles.values())
    if global_max_agents != expected_global:
        raise PolicyError("limits.globalMaxAgents must equal parents plus concurrent worker caps")
    workflows = _exact_keys(data.get("workflows"), {"hostedExecution"}, "workflows")
    if workflows.get("hostedExecution") != "manual-only":
        raise PolicyError("workflows.hostedExecution must be manual-only")
    digest_input = {
        "source_commit": source_commit,
        "org_id": org_id,
        "principal": principal,
        "control_version": control_version,
        "source_tree_sha256": source_tree_sha256,
        "organization_daily_token_budget": organization_daily_token_budget,
        "global_max_agents": global_max_agents,
        "repositories": {k: vars(v) for k, v in sorted(repos.items())},
        "roles": {
            k: {
                "workers": sorted(v.worker_types),
                "max_per_wake": v.max_workers_per_wake,
                "max_concurrent": v.max_workers_concurrent,
                "repositories": sorted(v.repositories),
                "daily_token_budget": v.daily_token_budget,
            }
            for k, v in sorted(roles.items())
        },
    }
    digest = "sha256:" + hashlib.sha256(_canonical_json(digest_input)).hexdigest()
    return OrganizationPolicy(org_id, principal, repos, roles, source_commit,
                              control_version, source_tree_sha256,
                              organization_daily_token_budget, global_max_agents, digest)


def load_policy(path: str | Path, *, source_commit: str) -> OrganizationPolicy:
    import yaml

    with Path(path).open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return compile_policy(data, source_commit=source_commit)
