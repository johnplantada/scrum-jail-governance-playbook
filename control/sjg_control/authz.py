"""Closed authorization matrix for every agent-reachable operation."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
import base64
import hashlib
import re

from .policy import OrganizationPolicy


READ_OPERATIONS = frozenset({
    "read.issue.get", "read.issue.list", "read.issue.children", "read.issue.comments",
    "read.pr.get", "read.pr.list", "read.pr.files", "read.pr.checks",
    "read.workflow.runs", "repo.status",
})
PM_OPERATIONS = frozenset({
    "pm.issue.create", "pm.issue.comment", "pm.issue.route", "pm.issue.move",
    "pm.issue.done", "pm.issue.drop",
})
PR_OPERATIONS = frozenset({"pr.create", "pr.update", "pr.comment", "pr.review"})
REPOSITORY_OPERATIONS = frozenset({"repo.submit"})
WORKER_OPERATIONS = frozenset({"worker.run"})
OPERATIONS = READ_OPERATIONS | PM_OPERATIONS | PR_OPERATIONS | REPOSITORY_OPERATIONS | WORKER_OPERATIONS

OPERATION_ARGUMENTS = {
    "read.issue.get": {"repository", "id", "issue"},
    "read.issue.list": {"repository", "all"},
    "read.issue.children": {"repository", "id", "issue"},
    "read.issue.comments": {"repository", "id", "issue"},
    "read.pr.get": {"repository", "id", "pr"},
    "read.pr.list": {"repository"},
    "read.pr.files": {"repository", "id", "pr"},
    "read.pr.checks": {"repository", "id", "pr"},
    "read.workflow.runs": {"repository"},
    "repo.status": {"repository"},
    "pm.issue.create": {"repository", "kind", "title", "body", "parent",
                        "target_role", "also_role"},
    "pm.issue.comment": {"repository", "id", "issue", "body"},
    "pm.issue.route": {"repository", "id", "issue", "target_role"},
    "pm.issue.move": {"repository", "id", "issue", "to"},
    "pm.issue.done": {"repository", "id", "issue", "pr", "demo"},
    "pm.issue.drop": {"repository", "id", "issue", "reason"},
    "pr.create": {"repository", "topic", "title", "body", "draft"},
    "pr.update": {"repository", "id", "pr", "title", "body"},
    "pr.comment": {"repository", "id", "pr", "body"},
    "pr.review": {"repository", "id", "pr", "body", "event"},
    "repo.submit": {"repository", "topic", "branch", "base_oid", "changes", "message"},
    "worker.run": {"repository", "type", "task", "readPaths", "writePaths"},
}

HUMAN_ONLY_OPERATIONS = frozenset({
    "objective.create", "objective.edit", "objective.close", "pr.merge",
    "pr.auto_merge", "workflow.dispatch", "deployment.execute", "spend.authorize",
    "spend.execute", "governance.activate", "repository.settings",
    "branch.force_update", "branch.delete",
})

ROLE_CAPABILITIES = {
    "project-orchestrator": READ_OPERATIONS | {
        "pm.issue.create", "pm.issue.comment", "pm.issue.route",
        "pr.create", "pr.update", "pr.comment", "pr.review",
    },
    "commercial-planner": READ_OPERATIONS | (PM_OPERATIONS - {"pm.issue.route"}) | {
        "pr.create", "pr.update", "pr.comment", "pr.review", "repo.submit", "worker.run",
    },
    "software-implementer": READ_OPERATIONS | (PM_OPERATIONS - {"pm.issue.route"}) | PR_OPERATIONS | {
        "repo.submit", "worker.run",
    },
    "governance-reviewer": READ_OPERATIONS | {
        "pm.issue.comment", "pm.issue.route", "pr.comment", "pr.review",
    },
    "policy-reviewer": READ_OPERATIONS | (PM_OPERATIONS - {"pm.issue.route"}) | {
        "pr.create", "pr.update", "pr.comment", "pr.review", "repo.submit", "worker.run",
    },
}

# These roles may leave non-authorizing review evidence on another canonical role's
# open pull request. Software delivery remains separate from independent judgment.
INDEPENDENT_REVIEW_ROLES = frozenset({
    "project-orchestrator", "commercial-planner", "governance-reviewer", "policy-reviewer",
})

TERMINAL_STAGES = frozenset({"Done", "Dropped"})
STAGE_ORDER = {"Todo": 0, "In Progress": 1, "Awaiting Merge": 2, "Demo": 3,
               "Awaiting Deploy": 4}
HOLDING_STAGES = frozenset({"Blocked", "On Hold"})
CLOSING_RE = re.compile(
    r"(?i)(?:^|\b)(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)(?:\s*:\s*|\s+)"
    r"(?:(?:[\w.-]+/[\w.-]+)?#\d+|https://github\.com/[\w.-]+/[\w.-]+/issues/\d+)"
)
OBJECTIVE_RE = re.compile(r"(?i)^\s*\[?objective\]?\b")
ACCEPTANCE_RE = re.compile(
    r"(?im)^\s*(?:(?:[-*]\s*)?(?:acceptance|done[- ]?when)\s*:|#{1,6}\s*(?:acceptance|done[- ]?when)\b)\s*\S+")


@dataclass(frozen=True)
class Session:
    session_id: str
    wake_id: str
    org_id: str
    role: str
    policy_digest: str
    workspace_aliases: frozenset[str]
    expires_at: str


@dataclass(frozen=True)
class Facts:
    complete: bool = True
    issue_kind: str | None = None
    parent_kind: str | None = None
    owner_role: str | None = None
    current_stage: str | None = None
    has_open_children: bool | None = None
    evidence_valid: bool | None = None
    pr_head_role: str | None = None
    pr_open: bool | None = None
    managed_by_monitor: bool = False


class AuthorizationError(PermissionError):
    pass


def _safe_topic(topic: Any) -> str:
    topic = str(topic or "")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62})", topic):
        raise AuthorizationError("topic must be lowercase kebab-case and at most 63 characters")
    return topic


def _safe_paths(paths: Any, protected: tuple[str, ...]) -> None:
    if not isinstance(paths, list) or not paths:
        raise AuthorizationError("repo.submit requires a non-empty changes list")
    if len(paths) > 200:
        raise AuthorizationError("repo.submit exceeds 200 changed paths")
    seen: set[str] = set()
    for change in paths:
        if not isinstance(change, dict):
            raise AuthorizationError("each repository change must be an object")
        raw = str(change.get("path") or "")
        path = PurePosixPath(raw)
        if (not raw or path.is_absolute() or ".." in path.parts or "\x00" in raw
                or "\\" in raw or any(ord(character) < 32 for character in raw)
                or str(path) != raw):
            raise AuthorizationError(f"unsafe repository path: {raw!r}")
        normalized = str(path)
        if normalized in seen:
            raise AuthorizationError(f"duplicate repository path: {raw}")
        seen.add(normalized)
        if normalized == ".gitmodules" or normalized.endswith("/action.yml"):
            raise AuthorizationError(f"protected repository path: {raw}")
        if any(normalized == prefix.rstrip("/") or normalized.startswith(prefix)
               for prefix in protected):
            raise AuthorizationError(f"protected repository path: {raw}")
        operation = str(change.get("operation") or "")
        if operation not in {"upsert", "delete"}:
            raise AuthorizationError("change operation must be upsert or delete")
        if change.get("mode", "100644") not in {"100644", "100755"}:
            raise AuthorizationError("only regular file modes are allowed")
        expected_keys = ({"operation", "path", "mode", "sha256", "content_base64"}
                         if operation == "upsert" else {"operation", "path", "mode"})
        if set(change) != expected_keys:
            raise AuthorizationError("repository change has missing or unknown fields")
        if operation == "upsert":
            encoded = change.get("content_base64")
            digest = str(change.get("sha256") or "")
            if (not isinstance(encoded, str) or len(encoded) > 2_800_000
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise AuthorizationError("repository content metadata is invalid")
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                raise AuthorizationError("repository content is not valid base64") from exc
            if len(decoded) > 2 * 1024 * 1024 or hashlib.sha256(decoded).hexdigest() != digest:
                raise AuthorizationError("repository content size or digest is invalid")


def _safe_worker_paths(values: Any, *, name: str) -> tuple[str, ...]:
    if not isinstance(values, list) or len(values) > 200:
        raise AuthorizationError(f"worker {name} paths must be an array of at most 200 entries")
    result: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise AuthorizationError(f"worker {name} paths must be strings")
        path = PurePosixPath(raw)
        if (not raw or len(raw.encode("utf-8")) > 255 or path.is_absolute()
                or ".." in path.parts or "\x00" in raw or "\\" in raw
                or any(ord(character) < 32 for character in raw) or str(path) != raw):
            raise AuthorizationError(f"unsafe worker {name} path: {raw!r}")
        result.append(raw)
    if len(set(result)) != len(result):
        raise AuthorizationError(f"worker {name} paths must not contain duplicates")
    return tuple(result)


def validate_request(policy: OrganizationPolicy, session: Session, operation: str,
                     arguments: dict[str, Any]) -> None:
    """Reject unauthorized request shape and scope before any external fact lookup."""
    if session.org_id != policy.org_id or session.policy_digest != policy.policy_digest:
        raise AuthorizationError("session is not bound to the active policy")
    if operation in HUMAN_ONLY_OPERATIONS or operation not in OPERATIONS:
        raise AuthorizationError(f"operation is not agent-reachable: {operation}")
    allowed = ROLE_CAPABILITIES.get(session.role, frozenset())
    if operation not in allowed:
        raise AuthorizationError(f"{session.role} may not perform {operation}")
    if not isinstance(arguments, dict):
        raise AuthorizationError("arguments must be an object")
    forbidden_keys = {"role", "actor", "token", "repository_url", "command", "argv", "method", "url"}
    bad = forbidden_keys & arguments.keys()
    if bad:
        raise AuthorizationError(f"caller-controlled authority fields are forbidden: {', '.join(sorted(bad))}")
    unknown = set(arguments) - OPERATION_ARGUMENTS[operation]
    if unknown:
        raise AuthorizationError(f"unknown arguments for {operation}: {', '.join(sorted(unknown))}")

    repository = str(arguments.get("repository") or "org")
    role_policy = policy.role(session.role)
    if repository not in session.workspace_aliases or repository not in role_policy.repositories:
        raise AuthorizationError(f"repository {repository!r} is outside this wake")
    if operation in PM_OPERATIONS and repository != "org":
        raise AuthorizationError("project-management operations are confined to the org repository")


def authorize(policy: OrganizationPolicy, session: Session, operation: str,
              arguments: dict[str, Any], facts: Facts | None = None) -> None:
    """Raise AuthorizationError unless the closed request is permitted.

    External facts are mandatory and complete for every mutation whose correctness
    depends on current GitHub state.  UNKNOWN is never converted into absence.
    """
    validate_request(policy, session, operation, arguments)
    role_policy = policy.role(session.role)

    if operation in READ_OPERATIONS:
        return

    facts = facts or Facts(complete=False)
    if operation.startswith(("pm.", "pr.")) and not facts.complete:
        raise AuthorizationError("authoritative GitHub facts are incomplete")

    if operation == "pm.issue.create":
        raw_kind = str(arguments.get("kind") or "")
        kind = raw_kind.lower()
        if raw_kind != kind:
            raise AuthorizationError("work-item kind must use its canonical lowercase id")
        title = str(arguments.get("title") or "").strip()
        if not title:
            raise AuthorizationError("issue title is required")
        labels = {str(v).lower() for v in arguments.get("labels") or []}
        if kind == "objective" or "objective" in labels or OBJECTIVE_RE.match(title):
            raise AuthorizationError("only the Human Principal creates objectives")
        allowed_kinds = ({"epic", "proposal"}
                         if session.role == "project-orchestrator"
                         else {"feature", "story", "proposal"})
        if kind not in allowed_kinds:
            raise AuthorizationError(f"{session.role} may not create {kind or 'untyped'} work")
        if kind in {"feature", "story"} and not ACCEPTANCE_RE.search(str(arguments.get("body") or "")):
            raise AuthorizationError(f"{kind} requires acceptance or done-when evidence text")
        required_parent = {"epic": "objective", "feature": "epic", "story": "feature"}.get(kind)
        if required_parent and facts.parent_kind != required_parent:
            raise AuthorizationError(f"{kind} requires a complete {required_parent} parent")
        target = str(arguments.get("target_role") or "")
        delivery_roles = {"commercial-planner", "software-implementer", "policy-reviewer"}
        if session.role == "project-orchestrator":
            if kind != "proposal" and target not in policy.roles.keys() & delivery_roles:
                raise AuthorizationError("project-created delivery work requires a canonical delivery owner")
            if target and target not in policy.roles.keys() & delivery_roles:
                raise AuthorizationError("create target is not a canonical delivery owner")
        elif target:
            raise AuthorizationError("only project-orchestrator may assign another role at creation")
        collaborators = arguments.get("also_role") or []
        if (not isinstance(collaborators, list)
                or any(not isinstance(value, str) for value in collaborators)
                or len(set(collaborators)) != len(collaborators)
                or any(value not in policy.roles.keys() & delivery_roles
                       for value in collaborators)):
            raise AuthorizationError("collaborators must be canonical delivery roles")
        return

    if operation == "pm.issue.comment":
        body = str(arguments.get("body") or "")
        if not body.strip():
            raise AuthorizationError("comment body is required")
        if re.search(r"(?im)^\s*\*\*human principal\s+—", body):
            raise AuthorizationError("agents may not forge the Human Principal banner")
        return

    if operation == "pm.issue.route":
        target = str(arguments.get("target_role") or "")
        allowed_targets = ({"project-orchestrator"} if facts.issue_kind == "objective" else
                           {"commercial-planner", "software-implementer", "policy-reviewer"})
        if target not in policy.roles.keys() & allowed_targets:
            raise AuthorizationError("route target is invalid for this work kind")
        if facts.owner_role is not None:
            raise AuthorizationError("agent routing is triage-only and cannot replace an owner")
        return

    if operation in {"pm.issue.move", "pm.issue.done", "pm.issue.drop"}:
        if facts.owner_role != session.role:
            raise AuthorizationError("only the owning role may mutate work state")
        if session.role == "project-orchestrator":
            raise AuthorizationError("project-orchestrator coordinates but does not move or close work")

    if operation == "pm.issue.move":
        target = str(arguments.get("to") or "")
        if target in TERMINAL_STAGES:
            raise AuthorizationError("terminal stages require the typed done/drop operation")
        if target in HOLDING_STAGES:
            return
        if facts.current_stage in HOLDING_STAGES and target in STAGE_ORDER:
            return
        if facts.current_stage not in STAGE_ORDER or target not in STAGE_ORDER:
            raise AuthorizationError("unknown current or target stage")
        if STAGE_ORDER[target] < STAGE_ORDER[facts.current_stage]:
            raise AuthorizationError("backward movement requires Human intervention")
        return

    if operation in {"pm.issue.done", "pm.issue.drop"}:
        if facts.issue_kind not in {"story", "feature"}:
            raise AuthorizationError("agents may complete or drop only stories and features")
        if facts.has_open_children is not False:
            raise AuthorizationError("complete child enumeration did not prove zero open children")
        if operation == "pm.issue.done" and facts.evidence_valid is not True:
            raise AuthorizationError("evidence is absent, stale, unrelated, or incomplete")
        if operation == "pm.issue.drop" and not str(arguments.get("reason") or "").strip():
            raise AuthorizationError("drop requires a non-empty reason")
        return

    if operation.startswith("pr."):
        text = f"{arguments.get('title', '')}\n{arguments.get('body', '')}"
        if CLOSING_RE.search(text):
            raise AuthorizationError("PR text may not contain issue-closing keywords")
        if operation == "pr.create":
            _safe_topic(arguments.get("topic"))
            if not str(arguments.get("title") or "").strip():
                raise AuthorizationError("PR title is required")
        else:
            if facts.pr_open is not True or facts.pr_head_role not in policy.roles:
                raise AuthorizationError("PR must be open and owned by a canonical agent role")
            if operation == "pr.update" and facts.pr_head_role != session.role:
                raise AuthorizationError("role may update only its own open PR")
            if operation == "pr.review":
                event = str(arguments.get("event") or "COMMENT")
                if session.role not in INDEPENDENT_REVIEW_ROLES or facts.pr_head_role == session.role:
                    raise AuthorizationError("PR review must be independent of the authoring role")
                if event not in {"COMMENT", "REQUEST_CHANGES"}:
                    raise AuthorizationError("agent review may only comment or request changes")
        if operation in {"pr.comment", "pr.review"} and not str(arguments.get("body") or "").strip():
            raise AuthorizationError("PR comment or review body is required")
        if operation == "pr.update" and not any(
                str(arguments.get(key) or "").strip() for key in ("title", "body")):
            raise AuthorizationError("PR update requires a title or body")
        return

    if operation == "repo.submit":
        topic = _safe_topic(arguments.get("topic"))
        expected = f"agent/{session.role}/{topic}"
        supplied = arguments.get("branch")
        if supplied is not None and supplied != expected:
            raise AuthorizationError(f"branch must be {expected}")
        if not re.fullmatch(r"[0-9a-f]{40}", str(arguments.get("base_oid") or "")):
            raise AuthorizationError("submission base_oid must be a full commit SHA")
        message = str(arguments.get("message") or "")
        if len(message) > 500 or any(ord(character) < 32 and character not in "\n\t" for character in message):
            raise AuthorizationError("submission message is invalid")
        if CLOSING_RE.search(message):
            raise AuthorizationError("commit message may not contain issue-closing keywords")
        _safe_paths(arguments.get("changes"), policy.protected_submission_paths)
        return

    if operation == "worker.run":
        worker_type = str(arguments.get("type") or "")
        if worker_type not in role_policy.worker_types:
            raise AuthorizationError(f"worker type {worker_type!r} is not allowed for {session.role}")
        task = arguments.get("task")
        if not isinstance(task, str) or not task.strip() or len(task.encode("utf-8")) > 32768:
            raise AuthorizationError("worker task must be non-empty and at most 32 KiB")
        read_paths = _safe_worker_paths(arguments.get("readPaths"), name="read")
        write_paths = _safe_worker_paths(arguments.get("writePaths"), name="write")
        if worker_type != "implementer" and write_paths:
            raise AuthorizationError(f"{worker_type} is a read-only worker")
        if worker_type == "implementer" and not write_paths:
            raise AuthorizationError("implementer worker requires declared write paths")
        if not set(write_paths) <= set(read_paths):
            raise AuthorizationError("worker write paths must also be declared read paths")
        if any(key in arguments for key in ("model", "provider", "role", "tools", "socket")):
            raise AuthorizationError("worker model, provider, role, tools, and authority are server-selected")
        return

    raise AuthorizationError(f"unhandled operation: {operation}")
