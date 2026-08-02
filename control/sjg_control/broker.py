"""Request validation and idempotent dispatch for the installed control plane."""
from __future__ import annotations

from typing import Any, Protocol
import hashlib
import json
import re

from .authz import (AuthorizationError, Facts, READ_OPERATIONS, REPOSITORY_OPERATIONS,
                    WORKER_OPERATIONS, Session, authorize, validate_request)
from .policy import OrganizationPolicy
from .store import StateError, Store


REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")


class Backend(Protocol):
    def facts(self, operation: str, arguments: dict[str, Any]) -> Facts: ...
    def execute(self, operation: str, arguments: dict[str, Any], *, session: Session,
                idempotency_key: str) -> dict[str, Any]: ...


class Broker:
    def __init__(self, policy: OrganizationPolicy, store: Store, backend: Backend):
        self.policy = policy
        self.store = store
        self.backend = backend

    def handle(self, session_id: str, request: dict[str, Any]) -> dict[str, Any]:
        operation = str(request.get("operation") or "")
        key = str(request.get("idempotency_key") or "")
        arguments = request.get("arguments")
        if request.get("schema") != "sjg.request/v1" or not REQUEST_ID.fullmatch(key):
            return {"schema": "sjg.response/v1", "status": "denied",
                    "error": "invalid request schema or idempotency key"}
        if not isinstance(arguments, dict):
            return {"schema": "sjg.response/v1", "status": "denied",
                    "error": "arguments must be an object"}
        digest = hashlib.sha256(json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        applying = False
        try:
            session = self.store.session(session_id)
            validate_request(self.policy, session, operation, arguments)
            prior = self.store.prior_request(session_id, key, operation, digest)
            if prior is not None:
                return prior
            facts = (Facts() if operation in READ_OPERATIONS | REPOSITORY_OPERATIONS | WORKER_OPERATIONS
                     else self.backend.facts(operation, arguments))
            authorize(self.policy, session, operation, arguments, facts)
            prior = self.store.reserve_request(session_id, key, operation, digest)
            if prior is not None:
                return prior
            self.store.mark_request_applying(key)
            applying = True
            result = self.backend.execute(operation, arguments, session=session,
                                          idempotency_key=key)
            response = {"schema": "sjg.response/v1", "status": "ok", "result": result}
            self.store.finish_request(key, "succeeded", response)
            self.store.audit(session, operation, "allowed", {"result": result}, digest)
            return response
        except AuthorizationError as exc:
            try:
                self.store.audit(locals().get("session"), operation, "denied", {"error": str(exc)}, digest)
            except Exception:
                pass
            return {"schema": "sjg.response/v1", "status": "denied", "error": str(exc)}
        except StateError as exc:
            return {"schema": "sjg.response/v1", "status": "conflict", "error": str(exc)}
        except TimeoutError as exc:
            if applying:
                response = {"schema": "sjg.response/v1", "status": "indeterminate", "error": str(exc)}
                try:
                    self.store.finish_request(key, "verify_pending", response)
                except Exception:
                    pass
            else:
                response = {"schema": "sjg.response/v1", "status": "retryable", "error": str(exc)}
            return response
        except Exception as exc:
            if not applying or operation in READ_OPERATIONS or operation == "repo.status":
                response = {"schema": "sjg.response/v1", "status": "retryable", "error": str(exc)}
                try:
                    self.store.abandon_read_request(key)
                except Exception:
                    pass
            else:
                response = {"schema": "sjg.response/v1", "status": "indeterminate",
                            "error": f"mutation outcome requires verification: {exc}"}
                try:
                    self.store.finish_request(key, "verify_pending", response)
                except Exception:
                    pass
            return response
