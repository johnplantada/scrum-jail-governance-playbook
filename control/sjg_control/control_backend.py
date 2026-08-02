"""Compose GitHub and worker capabilities without widening either interface."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .authz import Facts, PM_OPERATIONS, PR_OPERATIONS, Session, authorize
from .github_backend import GitHubBackend
from .workers import WorkerSupervisor


class ControlBackend:
    def __init__(self, github: GitHubBackend, workers: WorkerSupervisor,
                 workspace_for: Callable[[Session, str], str | Path]):
        self.github = github
        self.workers = workers
        self.workspace_for = workspace_for

    def facts(self, operation: str, arguments: dict[str, Any]) -> Facts:
        if operation == "worker.run":
            return Facts()
        return self.github.facts(operation, arguments)

    def execute(self, operation: str, arguments: dict[str, Any], *, session: Session,
                idempotency_key: str) -> dict[str, Any]:
        if operation == "worker.run":
            repository = str(arguments.get("repository") or "org")
            result = self.workers.run(session, arguments,
                                      parent_workspace=self.workspace_for(session, repository))
            return {"attempt_id": result.attempt_id, "worker_type": result.worker_type,
                    "output": result.output, "changed_paths": list(result.changed_paths)}
        if operation in PM_OPERATIONS | PR_OPERATIONS:
            # GitHub is not transactional with the local authorization journal. Re-read
            # every state-bearing precondition as close as possible to the first write so
            # a Human ownership/evidence/state change cannot be overwritten by stale facts.
            authorize(self.github.policy, session, operation, arguments,
                      self.github.facts(operation, arguments))
        return self.github.execute(operation, arguments, session=session,
                                   idempotency_key=idempotency_key)
