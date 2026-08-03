"""Strict wake-rule compilation and deterministic event routing."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import hashlib
import os
import re

import yaml

from .delivery import SOURCE_IDS
from .policy import OrganizationPolicy


MATCH_FIELDS = frozenset({"kind", "repo", "label", "workflow", "conclusion", "pr_state"})
KINDS = frozenset({"issue", "pr", "comment", "run"})
PR_STATES = frozenset({"open", "merged", "closed"})


class RoutingError(ValueError):
    pass


@dataclass(frozen=True)
class WakeRule:
    match: dict[str, str]
    wake: str


@dataclass(frozen=True)
class RoutedEvent:
    kind: str
    repository: str
    labels: frozenset[str]
    workflow: str | None = None
    conclusion: str | None = None
    pr_state: str | None = None


def wake_rules_digest(path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_file() or candidate.is_symlink():
        raise RoutingError("wake rules must be a regular non-symlink file")
    return hashlib.sha256(candidate.read_bytes()).hexdigest()


def load_wake_rules(path: str | Path, policy: OrganizationPolicy, *, expected_sha256: str) -> tuple[WakeRule, ...]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise RoutingError("commissioned wake-rule digest is invalid")
    if wake_rules_digest(path) != expected_sha256:
        raise RoutingError("wake rules differ from the commissioned digest")
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RoutingError(f"wake rules are unreadable or invalid: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"rules"} or not isinstance(data["rules"], list):
        raise RoutingError("wake rules must contain only a rules list")
    compiled: list[WakeRule] = []
    for index, raw in enumerate(data["rules"]):
        if not isinstance(raw, dict) or set(raw) != {"match", "wake"}:
            raise RoutingError(f"wake rule {index} must contain exactly match and wake")
        match = raw["match"]
        if not isinstance(match, dict) or not match or not set(match) <= MATCH_FIELDS:
            raise RoutingError(f"wake rule {index} has invalid match fields")
        normalized: dict[str, str] = {}
        for field, value in match.items():
            if not isinstance(value, str) or not value or any(ord(character) < 32 for character in value):
                raise RoutingError(f"wake rule {index} has an invalid {field} value")
            normalized[field] = value
        if "kind" in normalized and normalized["kind"] not in KINDS:
            raise RoutingError(f"wake rule {index} has an invalid kind")
        if "repo" in normalized and normalized["repo"] not in policy.repositories:
            raise RoutingError(f"wake rule {index} has an unknown repository alias")
        if "pr_state" in normalized and normalized["pr_state"] not in PR_STATES:
            raise RoutingError(f"wake rule {index} has an invalid pull-request state")
        if "workflow" in normalized and not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", normalized["workflow"]):
            raise RoutingError(f"wake rule {index} has an invalid workflow filename")
        wake = str(raw["wake"])
        if wake != "from-label" and wake not in policy.roles:
            raise RoutingError(f"wake rule {index} targets an unknown role")
        compiled.append(WakeRule(normalized, wake))
    if not compiled:
        raise RoutingError("at least one wake rule is required")
    return tuple(compiled)


def _labels(value: Any) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    labels = []
    for item in value:
        if isinstance(item, dict):
            name = item.get("name")
        else:
            name = item
        if isinstance(name, str) and name:
            labels.append(name)
    return frozenset(labels)


def normalize_event(source_id: str, payload: dict[str, Any]) -> RoutedEvent:
    if source_id not in SOURCE_IDS:
        raise RoutingError(f"unknown event source: {source_id}")
    if not isinstance(payload, dict):
        raise RoutingError("event payload must be an object")
    repository = "org" if source_id.startswith("org-") else "product"
    if source_id.endswith("workflow-runs"):
        raw_path = str(payload.get("path") or payload.get("_sjg_workflow_path") or "")
        raw_path = raw_path.split("@", 1)[0]
        workflow = os.path.basename(raw_path) if raw_path else None
        return RoutedEvent("run", repository, frozenset(), workflow=workflow,
                           conclusion=str(payload.get("conclusion") or "") or None)
    if source_id.endswith("comments"):
        parent = payload.get("_sjg_parent")
        if not isinstance(parent, dict):
            raise RoutingError("comment event lacks complete parent enrichment")
        return RoutedEvent("comment", repository, _labels(parent.get("labels")))
    is_pr = bool(payload.get("pull_request"))
    if not is_pr:
        return RoutedEvent("issue", repository, _labels(payload.get("labels")))
    pull = payload.get("_sjg_pull_request")
    merged = isinstance(pull, dict) and pull.get("merged_at") is not None
    state = str(payload.get("state") or "")
    pr_state = "merged" if merged else "open" if state == "open" else "closed"
    return RoutedEvent("pr", repository, _labels(payload.get("labels")), pr_state=pr_state)


def route_event(event: RoutedEvent, rules: tuple[WakeRule, ...],
                policy: OrganizationPolicy) -> str | None:
    values = {
        "kind": event.kind, "repo": event.repository, "workflow": event.workflow,
        "conclusion": event.conclusion, "pr_state": event.pr_state,
    }
    for rule in rules:
        matched = True
        for field, expected in rule.match.items():
            if field == "label":
                matched = expected in event.labels
            else:
                matched = values.get(field) == expected
            if not matched:
                break
        if not matched:
            continue
        if rule.wake != "from-label":
            return rule.wake
        roles = sorted({label.removeprefix("role:") for label in event.labels
                        if label.startswith("role:") and label.removeprefix("role:") in policy.roles})
        return roles[0] if len(roles) == 1 else None
    return None
