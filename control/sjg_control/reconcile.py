"""Tri-state reconciliation and mutation-journal helpers."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any
import hashlib
import json


class Truth(Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Observation:
    truth: Truth
    complete: bool
    value: Any = None


@dataclass(frozen=True)
class ManagedState:
    source_key: str
    exists: Observation
    qualifies: Observation
    current_stage: Observation
    desired_stage: Observation


@dataclass(frozen=True)
class PlannedOperation:
    source_key: str
    operation: str
    precondition: dict[str, Any]


def plan(state: ManagedState) -> list[PlannedOperation]:
    """UNKNOWN always preserves remote state and emits no mutation."""
    observations = (state.exists, state.qualifies, state.current_stage, state.desired_stage)
    if any(not value.complete or value.truth is Truth.UNKNOWN for value in observations):
        return []
    if state.exists.truth is Truth.FALSE and state.qualifies.truth is Truth.TRUE:
        return [PlannedOperation(state.source_key, "create", {"proved_absent": True})]
    if state.exists.truth is Truth.TRUE and state.qualifies.truth is Truth.FALSE:
        return [PlannedOperation(state.source_key, "close", {"proved_present": True})]
    if (state.exists.truth is Truth.TRUE and state.qualifies.truth is Truth.TRUE
            and state.current_stage.value != state.desired_stage.value):
        return [PlannedOperation(state.source_key, "move", {
            "from": state.current_stage.value, "to": state.desired_stage.value})]
    return []


def report_hash(model: dict[str, Any]) -> str:
    canonical = json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def source_marker(source_key: str) -> str:
    if not source_key or "--" in source_key or "\n" in source_key:
        raise ValueError("invalid reconciliation source key")
    return f"<!-- governance-monitor-source:{source_key} -->"
