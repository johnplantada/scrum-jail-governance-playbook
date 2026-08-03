"""Operation-specific verification for ambiguous broker and reconciliation writes."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any
import json

from .github_backend import GitHubBackend, GitHubError
from .reconcile import source_marker
from .store import Store


class Proof(Enum):
    PRESENT = "present"
    ABSENT = "absent"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Verification:
    proof: Proof
    result: dict[str, Any] | None = None
    detail: str | None = None


class GitHubOperationVerifier:
    """Prove exact effects using complete fixed-scope reads; never infer from absence."""

    def __init__(self, backend: GitHubBackend):
        self.backend = backend

    @staticmethod
    def _marker(key: str) -> str:
        return f"<!-- sjg-idempotency:{key} -->"

    def _comments(self, repository: str, number: int) -> list[dict[str, Any]]:
        return self.backend._pages(  # noqa: SLF001 - verifier is part of the sealed backend.
            f"repos/{repository}/issues/{number}/comments?per_page=100")

    @staticmethod
    def _unique(matches: list[dict[str, Any]], result: dict[str, Any] | None = None) -> Verification:
        if not matches:
            return Verification(Proof.ABSENT)
        if len(matches) > 1:
            return Verification(Proof.CONFLICT, detail="multiple remote effects carry one idempotency marker")
        return Verification(Proof.PRESENT, result or matches[0])

    def verify_broker(self, operation: str, arguments: dict[str, Any], key: str) -> Verification:
        marker = self._marker(key)
        repository = self.backend.policy.repository(
            str(arguments.get("repository") or "org")).slug
        try:
            if operation == "pm.issue.create":
                issues = self.backend._pages(  # noqa: SLF001
                    f"repos/{repository}/issues?state=all&per_page=100")
                matches = [item for item in issues if not item.get("pull_request")
                           and marker in str(item.get("body") or "")]
                if len(matches) == 1:
                    return Verification(Proof.PRESENT, {
                        "number": matches[0].get("number"), "url": matches[0].get("html_url")})
                return self._unique(matches)
            if operation in {"pm.issue.comment", "pr.comment"}:
                number = int(arguments.get("id") or arguments.get("issue") or arguments.get("pr") or 0)
                matches = [item for item in self._comments(repository, number)
                           if marker in str(item.get("body") or "")]
                if len(matches) == 1:
                    return Verification(Proof.PRESENT, {"comment_id": matches[0].get("id")})
                return self._unique(matches)
            if operation == "pm.issue.route":
                issue = self.backend._issue(arguments)  # noqa: SLF001
                target = str(arguments.get("target_role") or "")
                labels = self.backend._labels(issue)  # noqa: SLF001
                metadata = self.backend._metadata(issue) or {}  # noqa: SLF001
                present = f"role:{target}" in labels and metadata.get("owner_role") == target
                return Verification(Proof.PRESENT if present else Proof.ABSENT,
                                    {"owner_role": target} if present else None)
            if operation == "pm.issue.move":
                stage = self.backend._stage(arguments)  # noqa: SLF001
                target = str(arguments.get("to") or "")
                return Verification(Proof.PRESENT if stage == target else Proof.ABSENT,
                                    {"stage": target} if stage == target else None)
            if operation in {"pm.issue.done", "pm.issue.drop"}:
                issue = self.backend._issue(arguments)  # noqa: SLF001
                comments = self._comments(repository, int(issue["number"]))
                marked = any(marker in str(item.get("body") or "") for item in comments)
                present = issue.get("state") == "closed" and marked
                return Verification(Proof.PRESENT if present else Proof.ABSENT,
                                    {"number": issue["number"], "state": issue.get("state")}
                                    if present else None)
            if operation == "pr.create":
                pulls = self.backend._pages(  # noqa: SLF001
                    f"repos/{repository}/pulls?state=all&per_page=100")
                branch = f"agent/{arguments.get('_role', '')}/{arguments.get('topic', '')}"
                matches = [item for item in pulls if marker in str(item.get("body") or "")
                           and (not arguments.get("_role")
                                or str((item.get("head") or {}).get("ref") or "") == branch)]
                if len(matches) == 1:
                    return Verification(Proof.PRESENT, {
                        "number": matches[0].get("number"), "url": matches[0].get("html_url")})
                return self._unique(matches)
            if operation == "pr.update":
                number = int(arguments.get("id") or arguments.get("pr") or 0)
                pull = self.backend._api(f"repos/{repository}/pulls/{number}")  # noqa: SLF001
                if not isinstance(pull, dict):
                    return Verification(Proof.UNKNOWN, detail="pull request read was incomplete")
                matches = all(str(pull.get(field) or "") == str(arguments[field])
                              for field in ("title", "body") if field in arguments)
                return Verification(Proof.PRESENT if matches else Proof.ABSENT,
                                    {"number": number, "url": pull.get("html_url")} if matches else None)
            if operation == "pr.review":
                number = int(arguments.get("id") or arguments.get("pr") or 0)
                reviews = self.backend._pages(  # noqa: SLF001
                    f"repos/{repository}/pulls/{number}/reviews?per_page=100")
                matches = [item for item in reviews if marker in str(item.get("body") or "")]
                if len(matches) == 1:
                    return Verification(Proof.PRESENT, {
                        "review_id": matches[0].get("id"), "state": matches[0].get("state")})
                return self._unique(matches)
            if operation == "repo.submit":
                role = str(arguments.get("_role") or "")
                topic = str(arguments.get("topic") or "")
                if not role:
                    return Verification(Proof.UNKNOWN, detail="persisted request lacks its bound role")
                ref = self.backend._api(  # noqa: SLF001
                    f"repos/{repository}/git/ref/heads/agent/{role}/{topic}")
                sha = str(((ref or {}).get("object") or {}).get("sha") or "")
                if not sha:
                    return Verification(Proof.ABSENT)
                commit = self.backend._api(f"repos/{repository}/commits/{sha}")  # noqa: SLF001
                message = str((((commit or {}).get("commit") or {}).get("message") or ""))
                present = f"SJG-Idempotency: {key}" in message
                return Verification(Proof.PRESENT if present else Proof.ABSENT,
                                    {"repository": repository, "sha": sha} if present else None)
        except (GitHubError, KeyError, TypeError, ValueError) as exc:
            return Verification(Proof.UNKNOWN, detail=str(exc))
        return Verification(Proof.UNKNOWN, detail=f"no verifier for operation {operation}")

    def verify_reconcile(self, operation: str, source_key: str,
                         precondition: dict[str, Any]) -> Verification:
        alias = str(precondition.get("repository") or "")
        if alias not in self.backend.policy.repositories:
            return Verification(Proof.CONFLICT, detail="reconciliation repository is absent")
        repository = self.backend.policy.repository(alias).slug
        marker = source_marker(source_key)
        try:
            if operation == "create":
                issues = self.backend._pages(  # noqa: SLF001
                    f"repos/{repository}/issues?state=all&per_page=100")
                matches = [item for item in issues
                           if marker in str(item.get("body") or "")]
                unique = self._unique(matches)
                queue = int(precondition.get("queue_issue") or 0)
                if unique.proof is not Proof.PRESENT or queue <= 0:
                    return unique
                children = self.backend._pages(  # noqa: SLF001
                    f"repos/{repository}/issues/{queue}/sub_issues?per_page=100")
                number = int(matches[0].get("number") or 0)
                attached = any(int(value.get("number") or 0) == number for value in children)
                return Verification(Proof.PRESENT if attached else Proof.ABSENT,
                                    {"number": number, "queue_issue": queue}
                                    if attached else None)
            number = int(precondition.get("issue") or 0)
            if number <= 0:
                return Verification(Proof.CONFLICT, detail="reconciliation issue is absent")
            issue = self.backend._api(f"repos/{repository}/issues/{number}")  # noqa: SLF001
            if not isinstance(issue, dict):
                return Verification(Proof.UNKNOWN, detail="reconciliation issue read is incomplete")
            if marker not in str(issue.get("body") or ""):
                return Verification(Proof.CONFLICT, detail="managed source marker is absent")
            if operation == "close":
                present = issue.get("state") == "closed"
                return Verification(Proof.PRESENT if present else Proof.ABSENT,
                                    {"number": number, "state": issue.get("state")} if present else None)
            if operation == "move":
                stage = self.backend._stage({"repository": alias, "issue": number})  # noqa: SLF001
                desired = precondition.get("to")
                return Verification(Proof.PRESENT if stage == desired else Proof.ABSENT,
                                    {"number": number, "stage": stage} if stage == desired else None)
        except (GitHubError, TypeError, ValueError) as exc:
            return Verification(Proof.UNKNOWN, detail=str(exc))
        return Verification(Proof.UNKNOWN, detail="unsupported reconciliation operation")


def resolve_pending(store: Store, verifier: GitHubOperationVerifier) -> dict[str, int]:
    counts = {proof.value: 0 for proof in Proof}
    for row in store.pending_broker_verifications():
        arguments = json.loads(row["arguments_json"])
        arguments["_role"] = store.connection.execute(
            "SELECT role FROM wake_session WHERE session_id=?", (row["session_id"],)
        ).fetchone()[0]
        result = verifier.verify_broker(str(row["operation"]), arguments,
                                        str(row["idempotency_key"]))
        counts[result.proof.value] += 1
        if result.proof is Proof.PRESENT:
            response = {"schema": "sjg.response/v1", "status": "ok",
                        "result": result.result or {"verified": True}}
            store.finish_request(str(row["idempotency_key"]), "succeeded", response)
        elif result.proof in {Proof.ABSENT, Proof.CONFLICT}:
            response = {"schema": "sjg.response/v1", "status": "denied",
                        "error": result.detail or f"remote effect proved {result.proof.value}; use a new request"}
            store.finish_request(str(row["idempotency_key"]), "failed", response)
    for row in store.pending_reconciliations():
        result = verifier.verify_reconcile(
            str(row["operation"]), str(row["source_key"]),
            json.loads(row["precondition_json"]),
        )
        counts[result.proof.value] += 1
        if result.proof is Proof.PRESENT:
            store.finish_reconcile(str(row["operation_id"]), state="succeeded",
                                   result=result.result or {"verified": True})
        elif result.proof in {Proof.ABSENT, Proof.CONFLICT}:
            store.finish_reconcile(str(row["operation_id"]), state="dead_letter",
                                   result={"proof": result.proof.value,
                                           "detail": result.detail})
    return counts
