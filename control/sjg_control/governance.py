"""Deterministic Human Principal queue audit and reconciliation."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import hashlib
import json
import re

import yaml

from .github_backend import GitHubBackend, GitHubError
from .policy import OrganizationPolicy
from .reconcile import (ManagedState, Observation, Truth, plan, report_hash,
                        source_marker)
from .store import Store


class GovernanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class DesiredQueueItem:
    source_key: str
    title: str
    body: str


MARKER = re.compile(r"(?m)^<!-- governance-monitor-source:([^\n]+) -->$")
BLOCKER_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,99}")


def _yaml_file(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise GovernanceError(f"governance input is absent or unsafe: {path.name}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise GovernanceError(f"governance input is unreadable: {path.name}") from exc


class GovernanceMonitor:
    """Reconcile only marker-owned queue children from complete fixed-scope reads."""

    def __init__(self, policy: OrganizationPolicy, backend: GitHubBackend,
                 store: Store, *, org_workspace: str):
        self.policy = policy
        self.backend = backend
        self.store = store
        self.workspace = Path(org_workspace)
        if not self.workspace.is_absolute() or self.workspace.is_symlink():
            raise GovernanceError("governance workspace is unsafe")

    def _commissioning(self) -> int:
        chart = _yaml_file(self.workspace / "org-chart.yaml")
        if not isinstance(chart, dict):
            raise GovernanceError("org-chart root must be a mapping")
        human = chart.get("human_principal")
        global_values = chart.get("global")
        if (not isinstance(human, dict)
                or human.get("github") != self.policy.principal_github):
            raise GovernanceError("org-chart Human Principal differs from organization policy")
        if not isinstance(global_values, dict):
            raise GovernanceError("org-chart global commissioning block is absent")
        queue = global_values.get("principal_queue_issue")
        project = global_values.get("pm_project_number")
        if isinstance(queue, bool) or not isinstance(queue, int) or queue <= 0:
            raise GovernanceError("global.principal_queue_issue is not commissioned")
        if self.backend.project is not None and project != self.backend.project.number:
            raise GovernanceError("org-chart Project number differs from supervisor config")
        return queue

    def _blockers(self) -> list[DesiredQueueItem]:
        document = _yaml_file(self.workspace / "blockers.yaml")
        if not isinstance(document, dict) or set(document) != {"blockers"}:
            raise GovernanceError("blockers.yaml must contain only the blockers list")
        rows = document["blockers"]
        if not isinstance(rows, list):
            raise GovernanceError("blockers must be a list")
        desired: list[DesiredQueueItem] = []
        seen: set[str] = set()
        required = {"id", "kind", "needs", "value", "effort_minutes", "summary", "action",
                    "blocks", "opened", "state"}
        optional = {"note", "gates_market_contact"}
        for row in rows:
            if not isinstance(row, dict) or not required <= set(row) or set(row) - required - optional:
                raise GovernanceError("blocker entry has missing or unknown fields")
            identifier = str(row.get("id") or "")
            if not BLOCKER_ID.fullmatch(identifier) or identifier in seen:
                raise GovernanceError("blocker ids must be unique lowercase kebab-case")
            seen.add(identifier)
            if row.get("state") not in {"open", "cleared"}:
                raise GovernanceError("blocker state must be open or cleared")
            if row.get("needs") != "human-principal":
                raise GovernanceError("blocker needs must be human-principal")
            if row.get("state") != "open":
                continue
            summary = str(row.get("summary") or "").strip()
            action = str(row.get("action") or "").strip()
            if not summary or len(summary) > 140 or not action or len(action) > 4000:
                raise GovernanceError("open blocker summary or action is invalid")
            source = f"blocker:{identifier}"
            desired.append(DesiredQueueItem(
                source, f"[HUMAN] {summary}",
                f"{source_marker(source)}\n\n**Required action**\n\n{action}\n\n"
                f"Blocks: `{json.dumps(row.get('blocks'), ensure_ascii=False)}`\n",
            ))
        return desired

    def _remote_desired(self) -> tuple[list[DesiredQueueItem], list[dict[str, Any]]]:
        desired: list[DesiredQueueItem] = []
        org = self.policy.repository("org").slug
        org_issues = self.backend._pages(  # noqa: SLF001 - same sealed control boundary.
            f"repos/{org}/issues?state=all&per_page=100")
        for issue in org_issues:
            title = str(issue.get("title") or "")
            if (issue.get("state") == "open" and not issue.get("pull_request")
                    and title.startswith("[PROPOSAL]")):
                number = int(issue["number"])
                source = f"proposal:org#{number}"
                desired.append(DesiredQueueItem(
                    source, f"[HUMAN] Decide proposal #{number}: {title[10:].strip()}",
                    f"{source_marker(source)}\n\nReview the open proposal and record the Human "
                    f"Principal decision.\n\nSource: {issue.get('html_url') or '#'}\n",
                ))
        for alias in sorted(self.policy.repositories):
            repository = self.policy.repository(alias).slug
            pulls = self.backend._pages(  # noqa: SLF001
                f"repos/{repository}/pulls?state=open&per_page=100")
            for pull in pulls:
                number = int(pull["number"])
                detail = self.backend._api(  # noqa: SLF001
                    f"repos/{repository}/pulls/{number}")
                if not isinstance(detail, dict) or detail.get("mergeable") is None:
                    raise GovernanceError("GitHub did not provide a complete PR mergeability fact")
                if (detail.get("draft") is True or detail.get("mergeable") is not True
                        or detail.get("mergeable_state") != "clean"):
                    continue
                source = f"pr:{alias}#{number}"
                title = str(detail.get("title") or "").strip()
                desired.append(DesiredQueueItem(
                    source, f"[HUMAN] Merge {alias} PR #{number}: {title}"[:160],
                    f"{source_marker(source)}\n\nThis pull request is open, non-draft, mergeable, "
                    f"and clean. Human review/merge remains required.\n\n"
                    f"Source: {detail.get('html_url') or '#'}\n",
                ))
        return desired, org_issues

    @staticmethod
    def _managed(issues: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for issue in issues:
            match = MARKER.search(str(issue.get("body") or ""))
            if match:
                source = match.group(1)
                try:
                    source_marker(source)
                except ValueError as exc:
                    raise GovernanceError("remote governance marker is invalid") from exc
                result.setdefault(source, []).append(issue)
        return result

    def snapshot(self) -> dict[str, Any]:
        queue = self._commissioning()
        queue_issue = self.backend._issue({"repository": "org", "id": queue})  # noqa: SLF001
        blockers = self._blockers()
        remote, all_issues = self._remote_desired()
        desired_items = blockers + remote
        desired = {item.source_key: item for item in desired_items}
        if len(desired) != len(desired_items):
            raise GovernanceError("governance desired-state source keys collide")
        children = self.backend._pages(  # noqa: SLF001
            f"repos/{self.policy.repository('org').slug}/issues/{queue}/sub_issues?per_page=100")
        child_numbers = {int(value["number"]) for value in children}
        managed = self._managed(all_issues)
        conflicts = sorted(source for source, values in managed.items() if len(values) != 1)
        actions: list[dict[str, Any]] = []
        for source, item in sorted(desired.items()):
            matches = managed.get(source, [])
            present = (len(matches) == 1 and matches[0].get("state") == "open"
                       and int(matches[0]["number"]) in child_numbers)
            state = ManagedState(
                source, Observation(Truth.TRUE if present else Truth.FALSE, True),
                Observation(Truth.TRUE, True), Observation(Truth.TRUE, True, "queue"),
                Observation(Truth.TRUE, True, "queue"),
            )
            if source not in conflicts and plan(state):
                existing = matches[0] if len(matches) == 1 else None
                actions.append({"operation": "create", "source_key": source,
                                "title": item.title, "body": item.body,
                                "existing_issue": int(existing["number"]) if existing else None,
                                "version": str(existing.get("updated_at") or "") if existing else "absent"})
        for source, matches in sorted(managed.items()):
            if source in desired or source in conflicts or len(matches) != 1:
                continue
            issue = matches[0]
            if issue.get("state") != "open" or int(issue["number"]) not in child_numbers:
                continue
            state = ManagedState(
                source, Observation(Truth.TRUE, True), Observation(Truth.FALSE, True),
                Observation(Truth.TRUE, True, "queue"), Observation(Truth.TRUE, True, "queue"),
            )
            if plan(state):
                actions.append({"operation": "close", "source_key": source,
                                "issue": int(issue["number"]),
                                "version": str(issue.get("updated_at") or "")})
        return {"complete": True, "queue_issue": int(queue_issue["number"]),
                "desired_count": len(desired), "managed_count": sum(map(len, managed.values())),
                "conflicts": conflicts, "unmanaged_children": sorted(
                    int(value["number"]) for value in children
                    if MARKER.search(str(value.get("body") or "")) is None),
                "actions": actions}

    def audit(self) -> dict[str, Any]:
        result = self.snapshot()
        result.update({"mutation": False, "report_sha256": report_hash(result)})
        return result

    def _apply(self, action: dict[str, Any], queue: int) -> str:
        source = str(action["source_key"])
        operation = str(action["operation"])
        precondition: dict[str, Any] = {"repository": "org", "queue_issue": queue}
        if operation == "create":
            precondition.update({"title": action["title"], "body": action["body"],
                                 "assignee": self.policy.principal_github,
                                 "existing_issue": action.get("existing_issue")})
        else:
            precondition["issue"] = int(action["issue"])
        digest = hashlib.sha256(json.dumps(
            [self.policy.policy_digest, source, operation, action.get("version"), precondition],
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        key = f"governance-{operation}-{digest[:40]}"
        row = self.store.reserve_reconcile(
            idempotency_key=key, source_key=source, operation=operation,
            precondition=precondition)
        if row["state"] != "planned":
            return str(row["state"])
        self.store.mark_reconcile_applying(str(row["operation_id"]))
        repository = self.policy.repository("org").slug
        try:
            if operation == "create":
                issue = None
                existing = action.get("existing_issue")
                if existing:
                    issue = self.backend._api(f"repos/{repository}/issues/{int(existing)}")  # noqa: SLF001
                    if not isinstance(issue, dict) or MARKER.search(str(issue.get("body") or "")) is None:
                        raise GovernanceError("existing managed queue issue changed before apply")
                    if issue.get("state") != "open":
                        issue = self.backend._api(  # noqa: SLF001
                            f"repos/{repository}/issues/{int(existing)}", method="PATCH",
                            body={"state": "open"})
                else:
                    issue = self.backend._api(  # noqa: SLF001
                        f"repos/{repository}/issues", method="POST", body={
                            "title": action["title"], "body": action["body"],
                            "labels": ["role:governance-reviewer"],
                            "assignees": [self.policy.principal_github],
                        })
                if not isinstance(issue, dict) or not isinstance(issue.get("id"), int):
                    raise GitHubError("GitHub issue creation/reopen response is incomplete")
                self.backend._api(  # noqa: SLF001
                    f"repos/{repository}/issues/{queue}/sub_issues", method="POST",
                    body={"sub_issue_id": issue["id"]})
                result = {"number": issue.get("number"), "queue_issue": queue}
            else:
                result = self.backend._api(  # noqa: SLF001
                    f"repos/{repository}/issues/{int(action['issue'])}", method="PATCH",
                    body={"state": "closed", "state_reason": "completed"})
                result = {"number": action["issue"], "state": "closed"}
        except (GitHubError, OSError) as exc:
            self.store.finish_reconcile(str(row["operation_id"]), state="verify_pending",
                                        result={"error": str(exc)})
            return "verify_pending"
        except (GovernanceError, KeyError, TypeError, ValueError) as exc:
            self.store.finish_reconcile(str(row["operation_id"]), state="dead_letter",
                                        result={"error": str(exc)})
            return "dead_letter"
        self.store.finish_reconcile(str(row["operation_id"]), state="succeeded", result=result)
        return "succeeded"

    def sync(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        if snapshot["conflicts"]:
            return {**snapshot, "mutation": False, "applied": {},
                    "error": "duplicate governance markers require Human repair"}
        counts: dict[str, int] = {}
        for action in snapshot["actions"]:
            state = self._apply(action, int(snapshot["queue_issue"]))
            counts[state] = counts.get(state, 0) + 1
        return {**snapshot, "mutation": bool(snapshot["actions"]), "applied": counts,
                "report_sha256": report_hash(snapshot)}
