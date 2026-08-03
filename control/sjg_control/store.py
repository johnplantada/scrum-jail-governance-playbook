"""Transactional state for sessions, idempotency, delivery, budgets, and workers."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
import datetime as dt
import json
import sqlite3
import uuid

from .authz import Session


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA synchronous=FULL;

CREATE TABLE IF NOT EXISTS wake_session (
  session_id TEXT PRIMARY KEY,
  wake_id TEXT NOT NULL UNIQUE,
  org_id TEXT NOT NULL,
  role TEXT NOT NULL,
  policy_digest TEXT NOT NULL,
  workspace_aliases_json TEXT NOT NULL,
  issued_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active','revoked','finished','expired'))
);

CREATE TABLE IF NOT EXISTS broker_request (
  idempotency_key TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES wake_session(session_id),
  operation TEXT NOT NULL,
  arguments_hash TEXT NOT NULL,
  arguments_json TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('reserved','applying','verify_pending','succeeded','failed')),
  response_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_attempt (
  attempt_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES wake_session(session_id),
  sequence INTEGER NOT NULL,
  worker_type TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('accepted','running','succeeded','failed','rejected')),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  artifact_sha256 TEXT,
  UNIQUE(session_id, sequence)
);

CREATE TABLE IF NOT EXISTS budget_bucket (
  scope TEXT NOT NULL,
  subject TEXT NOT NULL,
  epoch TEXT NOT NULL,
  token_limit INTEGER NOT NULL,
  consumed_tokens INTEGER NOT NULL DEFAULT 0,
  reserved_tokens INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(scope, subject, epoch)
);

CREATE TABLE IF NOT EXISTS budget_reservation (
  reservation_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES wake_session(session_id),
  epoch TEXT NOT NULL,
  reserved_tokens INTEGER NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active','settled','released','uncertain')),
  UNIQUE(session_id)
);

CREATE TABLE IF NOT EXISTS source_checkpoint (
  source_id TEXT PRIMARY KEY,
  watermark TEXT NOT NULL,
  overlap_seconds INTEGER NOT NULL DEFAULT 300,
  query_fingerprint TEXT,
  etag TEXT,
  last_complete_snapshot_id TEXT
);

CREATE TABLE IF NOT EXISTS source_snapshot (
  snapshot_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('collecting','complete','incomplete')),
  started_at TEXT NOT NULL,
  completed_at TEXT,
  page_count INTEGER NOT NULL DEFAULT 0,
  maximum_event_time TEXT,
  error_detail TEXT
);

CREATE TABLE IF NOT EXISTS snapshot_event (
  snapshot_id TEXT NOT NULL REFERENCES source_snapshot(snapshot_id),
  event_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  external_id TEXT NOT NULL,
  version_key TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(snapshot_id,event_id)
);

CREATE TABLE IF NOT EXISTS observed_event (
  event_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  external_id TEXT NOT NULL,
  version_key TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  enrichment_state TEXT NOT NULL DEFAULT 'pending',
  UNIQUE(source_id, external_id, version_key)
);

CREATE TABLE IF NOT EXISTS delivery (
  delivery_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('queued','leased','running','retry_wait','budget_hold','succeeded','dead_letter','uncertain')),
  attempt INTEGER NOT NULL DEFAULT 0,
  eligible_at TEXT NOT NULL,
  lease_owner TEXT,
  lease_until TEXT,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS route_target (
  event_id TEXT NOT NULL REFERENCES observed_event(event_id),
  role TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending','queued','leased','delivered','manual_review','dead_letter')),
  delivery_id TEXT REFERENCES delivery(delivery_id),
  PRIMARY KEY(event_id, role)
);

CREATE TABLE IF NOT EXISTS delivery_event (
  delivery_id TEXT NOT NULL REFERENCES delivery(delivery_id),
  event_id TEXT NOT NULL REFERENCES observed_event(event_id),
  role TEXT NOT NULL,
  PRIMARY KEY(delivery_id, event_id, role)
);

CREATE TABLE IF NOT EXISTS reconcile_operation (
  operation_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  source_key TEXT NOT NULL,
  operation TEXT NOT NULL,
  precondition_json TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('planned','applying','verify_pending','succeeded','dead_letter')),
  external_result_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supervisor_state (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wake_runtime (
  wake_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL UNIQUE REFERENCES wake_session(session_id),
  delivery_id TEXT REFERENCES delivery(delivery_id),
  reservation_id TEXT NOT NULL UNIQUE REFERENCES budget_reservation(reservation_id),
  role TEXT NOT NULL,
  reason TEXT NOT NULL,
  socket_path TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL CHECK (state IN ('prepared','running','succeeded','failed','uncertain','terminated')),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  output_sha256 TEXT,
  error_detail TEXT
);

CREATE TABLE IF NOT EXISTS process_runtime (
  process_key TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES wake_session(session_id),
  worker_attempt_id TEXT REFERENCES worker_attempt(attempt_id),
  kind TEXT NOT NULL CHECK (kind IN ('parent','worker')),
  pid INTEGER NOT NULL,
  pgid INTEGER NOT NULL,
  birth_token TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('running','exited','terminated','uncertain')),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  exit_code INTEGER,
  UNIQUE(pid,birth_token)
);

CREATE TABLE IF NOT EXISTS audit_entry (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  recorded_at TEXT NOT NULL,
  session_id TEXT,
  role TEXT,
  policy_digest TEXT,
  operation TEXT NOT NULL,
  request_hash TEXT,
  decision TEXT NOT NULL,
  detail_json TEXT NOT NULL
);
"""


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


class StateError(RuntimeError):
    pass


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        broker_fields = {row[1] for row in self.connection.execute("PRAGMA table_info(broker_request)")}
        if "arguments_json" not in broker_fields:
            self.connection.execute(
                "ALTER TABLE broker_request ADD COLUMN arguments_json TEXT NOT NULL DEFAULT '{}'"
            )

    @contextmanager
    def immediate(self) -> Iterator[sqlite3.Connection]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def create_session(self, *, org_id: str, role: str, policy_digest: str,
                       workspace_aliases: set[str], wake_id: str | None = None,
                       ttl_seconds: int = 3600, global_cap: int) -> Session:
        if global_cap <= 0 or ttl_seconds <= 0:
            raise StateError("global agent cap and session TTL must be positive")
        now = dt.datetime.now(dt.timezone.utc)
        expires = now + dt.timedelta(seconds=ttl_seconds)
        session = Session(
            session_id=str(uuid.uuid4()), wake_id=wake_id or str(uuid.uuid4()),
            org_id=org_id, role=role, policy_digest=policy_digest,
            workspace_aliases=frozenset(workspace_aliases),
            expires_at=expires.isoformat().replace("+00:00", "Z"),
        )
        now_text = now.isoformat().replace("+00:00", "Z")
        with self.immediate() as db:
            same_role = db.execute(
                "SELECT count(*) FROM wake_session WHERE org_id=? AND role=? "
                "AND state='active'", (org_id, role)
            ).fetchone()[0]
            if same_role:
                raise StateError("role already has an active parent wake")
            parents = db.execute(
                "SELECT count(*) FROM wake_session WHERE org_id=? AND state='active'",
                (org_id,),
            ).fetchone()[0]
            workers = db.execute(
                "SELECT count(*) FROM worker_attempt wa JOIN wake_session ws "
                "ON ws.session_id=wa.session_id WHERE ws.org_id=? "
                "AND wa.state IN ('accepted','running')", (org_id,)
            ).fetchone()[0]
            if parents + workers >= global_cap:
                raise StateError("global active-agent cap reached")
            db.execute(
                "INSERT INTO wake_session VALUES (?,?,?,?,?,?,?,?,?)",
                (session.session_id, session.wake_id, session.org_id, session.role,
                 session.policy_digest, json.dumps(sorted(session.workspace_aliases)),
                 now_text, session.expires_at, "active"),
            )
        return session

    def session(self, session_id: str) -> Session:
        row = self.connection.execute(
            "SELECT * FROM wake_session WHERE session_id=? AND state='active'", (session_id,)
        ).fetchone()
        if row is None:
            raise StateError("wake session is absent or inactive")
        if row["expires_at"] <= utcnow():
            raise StateError("wake session expired")
        return Session(row["session_id"], row["wake_id"], row["org_id"], row["role"],
                       row["policy_digest"], frozenset(json.loads(row["workspace_aliases_json"])),
                       row["expires_at"])

    def revoke_session(self, session_id: str) -> None:
        with self.immediate() as db:
            active_workers = db.execute(
                "SELECT count(*) FROM worker_attempt WHERE session_id=? "
                "AND state IN ('accepted','running')", (session_id,)
            ).fetchone()[0]
            if active_workers:
                raise StateError("cannot revoke a wake while its workers are active")
            cursor = db.execute(
                "UPDATE wake_session SET state='revoked' WHERE session_id=? AND state='active'",
                (session_id,),
            )
            if cursor.rowcount != 1:
                raise StateError("wake session is absent or inactive")

    def finish_session(self, session_id: str) -> None:
        """Finish a normally exited wake after every worker and reservation is settled."""
        with self.immediate() as db:
            active_workers = db.execute(
                "SELECT count(*) FROM worker_attempt WHERE session_id=? "
                "AND state IN ('accepted','running')", (session_id,)
            ).fetchone()[0]
            active_budget = db.execute(
                "SELECT count(*) FROM budget_reservation WHERE session_id=? AND state='active'",
                (session_id,),
            ).fetchone()[0]
            if active_workers or active_budget:
                raise StateError("cannot finish a wake with active workers or budget")
            cursor = db.execute(
                "UPDATE wake_session SET state='finished' WHERE session_id=? AND state='active'",
                (session_id,),
            )
            if cursor.rowcount != 1:
                raise StateError("wake session is absent or inactive")

    def force_terminate_session(self, session_id: str, *, expired: bool = False) -> None:
        """Close host-proved-dead process state without releasing unknown provider usage."""
        with self.immediate() as db:
            session = db.execute(
                "SELECT state FROM wake_session WHERE session_id=?", (session_id,)
            ).fetchone()
            if session is None or session["state"] != "active":
                raise StateError("wake session is absent or inactive")
            now = utcnow()
            db.execute(
                "UPDATE worker_attempt SET state='failed',finished_at=? "
                "WHERE session_id=? AND state IN ('accepted','running')", (now, session_id),
            )
            db.execute(
                "UPDATE budget_reservation SET state='uncertain' "
                "WHERE session_id=? AND state='active'", (session_id,),
            )
            db.execute(
                "UPDATE wake_session SET state=? WHERE session_id=? AND state='active'",
                ("expired" if expired else "revoked", session_id),
            )
            db.execute(
                "UPDATE wake_runtime SET state='terminated',finished_at=?,"
                "error_detail=COALESCE(error_detail,'terminated by supervisor') "
                "WHERE session_id=? AND state IN ('prepared','running')", (now, session_id),
            )

    def reap_expired_session(self, session_id: str) -> None:
        """Recover an expired wake only after the host has terminated its process group."""
        now = utcnow()
        with self.immediate() as db:
            session = db.execute(
                "SELECT state,expires_at FROM wake_session WHERE session_id=?", (session_id,)
            ).fetchone()
            if session is None or session["state"] != "active" or session["expires_at"] > now:
                raise StateError("wake session is absent, inactive, or not expired")
            db.execute(
                "UPDATE worker_attempt SET state='failed',finished_at=? "
                "WHERE session_id=? AND state IN ('accepted','running')", (now, session_id)
            )
            # Provider usage is unknowable after forced termination; preserve its reservation.
            db.execute(
                "UPDATE budget_reservation SET state='uncertain' "
                "WHERE session_id=? AND state='active'", (session_id,)
            )
            db.execute(
                "UPDATE wake_session SET state='expired' WHERE session_id=? AND state='active'",
                (session_id,),
            )

    def reserve_request(self, session_id: str, key: str, operation: str,
                        arguments_hash: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        now = utcnow()
        canonical_arguments = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
        with self.immediate() as db:
            row = db.execute("SELECT * FROM broker_request WHERE idempotency_key=?", (key,)).fetchone()
            if row:
                if (row["session_id"], row["operation"], row["arguments_hash"],
                        row["arguments_json"]) != (
                        session_id, operation, arguments_hash, canonical_arguments):
                    raise StateError("idempotency key reused with different request")
                return json.loads(row["response_json"]) if row["response_json"] else {
                    "status": "indeterminate", "error": "request is already in progress"}
            db.execute(
                "INSERT INTO broker_request(idempotency_key,session_id,operation,arguments_hash,"
                "arguments_json,state,response_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,'reserved',NULL,?,?)",
                (key, session_id, operation, arguments_hash, canonical_arguments, now, now),
            )
        return None

    def prior_request(self, session_id: str, key: str, operation: str,
                      arguments_hash: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM broker_request WHERE idempotency_key=?", (key,)
        ).fetchone()
        if row is None:
            return None
        if (row["session_id"], row["operation"], row["arguments_hash"]) != (
                session_id, operation, arguments_hash):
            raise StateError("idempotency key reused with different request")
        if row["response_json"]:
            return json.loads(row["response_json"])
        return {"schema": "sjg.response/v1", "status": "indeterminate",
                "error": "request is already in progress or its outcome is unknown"}

    def mark_request_applying(self, key: str) -> None:
        cursor = self.connection.execute(
            "UPDATE broker_request SET state='applying',updated_at=? "
            "WHERE idempotency_key=? AND state='reserved'", (utcnow(), key)
        )
        if cursor.rowcount != 1:
            raise StateError("request is not reserved")

    def abandon_read_request(self, key: str) -> None:
        """Only read failures may be forgotten so the same idempotency key can retry."""
        cursor = self.connection.execute(
            "DELETE FROM broker_request WHERE idempotency_key=? "
            "AND operation IN ('read.issue.get','read.issue.list','read.issue.children',"
            "'read.issue.comments',"
            "'read.pr.get','read.pr.list','read.pr.files','read.pr.checks',"
            "'read.workflow.runs','repo.status')",
            (key,),
        )
        if cursor.rowcount not in {0, 1}:
            raise StateError("unexpected read-request cleanup result")

    def finish_request(self, key: str, state: str, response: dict[str, Any]) -> None:
        if state not in {"succeeded", "failed", "verify_pending"}:
            raise StateError(f"invalid request terminal state: {state}")
        self.connection.execute(
            "UPDATE broker_request SET state=?, response_json=?, updated_at=? WHERE idempotency_key=?",
            (state, json.dumps(response, sort_keys=True), utcnow(), key),
        )

    def reserve_worker(self, session_id: str, worker_type: str,
                       *, cumulative_cap: int, concurrent_cap: int, global_cap: int) -> str:
        with self.immediate() as db:
            session = db.execute(
                "SELECT org_id FROM wake_session WHERE session_id=? AND state='active' "
                "AND expires_at>?", (session_id, utcnow())
            ).fetchone()
            if session is None:
                raise StateError("worker requires an active parent session")
            total = db.execute("SELECT count(*) FROM worker_attempt WHERE session_id=?", (session_id,)).fetchone()[0]
            active = db.execute(
                "SELECT count(*) FROM worker_attempt WHERE session_id=? AND state IN ('accepted','running')",
                (session_id,),
            ).fetchone()[0]
            if total >= cumulative_cap:
                raise StateError("cumulative worker cap reached")
            if active >= concurrent_cap:
                raise StateError("concurrent worker cap reached")
            parents = db.execute(
                "SELECT count(*) FROM wake_session WHERE org_id=? AND state='active'",
                (session["org_id"],),
            ).fetchone()[0]
            workers = db.execute(
                "SELECT count(*) FROM worker_attempt wa JOIN wake_session ws "
                "ON ws.session_id=wa.session_id WHERE ws.org_id=? "
                "AND wa.state IN ('accepted','running')",
                (session["org_id"],),
            ).fetchone()[0]
            if parents + workers >= global_cap:
                raise StateError("global active-agent cap reached")
            attempt_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO worker_attempt(attempt_id,session_id,sequence,worker_type,state,started_at) VALUES (?,?,?,?,?,?)",
                (attempt_id, session_id, total + 1, worker_type, "accepted", utcnow()),
            )
        return attempt_id

    def audit(self, session: Session | None, operation: str, decision: str,
              detail: dict[str, Any], request_hash: str | None = None) -> None:
        self.connection.execute(
            "INSERT INTO audit_entry(recorded_at,session_id,role,policy_digest,operation,request_hash,decision,detail_json) VALUES (?,?,?,?,?,?,?,?)",
            (utcnow(), session.session_id if session else None, session.role if session else None,
             session.policy_digest if session else None, operation, request_hash, decision,
             json.dumps(detail, sort_keys=True)),
        )

    def supervisor_value(self, key: str, default: Any = None) -> Any:
        row = self.connection.execute(
            "SELECT value_json FROM supervisor_state WHERE key=?", (key,)
        ).fetchone()
        return default if row is None else json.loads(row["value_json"])

    def set_supervisor_value(self, key: str, value: Any) -> None:
        if not key or len(key) > 100:
            raise StateError("supervisor-state key is invalid")
        self.connection.execute(
            "INSERT INTO supervisor_state(key,value_json,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
            (key, json.dumps(value, sort_keys=True), utcnow()),
        )

    def unrouted_events(self, *, limit: int = 500) -> list[sqlite3.Row]:
        if not 1 <= limit <= 5000:
            raise StateError("event routing batch limit is invalid")
        return list(self.connection.execute(
            "SELECT * FROM observed_event WHERE enrichment_state='pending' "
            "ORDER BY occurred_at,event_id LIMIT ?", (limit,),
        ))

    def finish_event_routing(self, event_id: str, role: str | None) -> None:
        with self.immediate() as db:
            row = db.execute(
                "SELECT enrichment_state FROM observed_event WHERE event_id=?", (event_id,)
            ).fetchone()
            if row is None or row["enrichment_state"] != "pending":
                raise StateError("event is absent or already routed")
            if role is not None:
                db.execute(
                    "INSERT INTO route_target(event_id,role,state) VALUES (?,?,'pending')",
                    (event_id, role),
                )
            db.execute(
                "UPDATE observed_event SET enrichment_state=? WHERE event_id=?",
                ("routed" if role is not None else "ignored", event_id),
            )

    def pending_routes(self, role: str, *, limit: int = 50) -> list[sqlite3.Row]:
        """Return unassigned routes oldest-first so a crash cannot strand routed events."""
        if not role or not 1 <= limit <= 5000:
            raise StateError("pending-route batch limit is invalid")
        return list(self.connection.execute(
            "SELECT rt.event_id,rt.role,oe.occurred_at FROM route_target rt "
            "JOIN observed_event oe ON oe.event_id=rt.event_id "
            "WHERE rt.state='pending' AND rt.delivery_id IS NULL AND rt.role=? "
            "ORDER BY oe.occurred_at,rt.event_id LIMIT ?", (role, limit),
        ))

    def delivery_events(self, delivery_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT oe.* FROM delivery_event de JOIN observed_event oe ON oe.event_id=de.event_id "
            "WHERE de.delivery_id=? ORDER BY oe.occurred_at,oe.event_id", (delivery_id,),
        )
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def prepare_wake(self, session: Session, *, delivery_id: str | None,
                     reservation_id: str, socket_path: str, reason: str) -> None:
        if not reason or not Path(socket_path).is_absolute():
            raise StateError("wake runtime requires a reason and absolute socket path")
        self.connection.execute(
            "INSERT INTO wake_runtime(wake_id,session_id,delivery_id,reservation_id,role,reason,"
            "socket_path,state,started_at) VALUES (?,?,?,?,?,?,?,'prepared',?)",
            (session.wake_id, session.session_id, delivery_id, reservation_id, session.role,
             reason, socket_path, utcnow()),
        )

    def mark_wake_running(self, wake_id: str) -> None:
        cursor = self.connection.execute(
            "UPDATE wake_runtime SET state='running' WHERE wake_id=? AND state='prepared'",
            (wake_id,),
        )
        if cursor.rowcount != 1:
            raise StateError("wake runtime is not prepared")

    def finish_wake(self, wake_id: str, *, state: str, output_sha256: str | None = None,
                    error: str | None = None) -> None:
        if state not in {"succeeded", "failed", "uncertain", "terminated"}:
            raise StateError("wake runtime terminal state is invalid")
        cursor = self.connection.execute(
            "UPDATE wake_runtime SET state=?,finished_at=?,output_sha256=?,error_detail=? "
            "WHERE wake_id=? AND state IN ('prepared','running')",
            (state, utcnow(), output_sha256, error, wake_id),
        )
        if cursor.rowcount != 1:
            raise StateError("wake runtime is not active")

    def record_process(self, *, process_key: str, session_id: str,
                       worker_attempt_id: str | None, kind: str, pid: int, pgid: int,
                       birth_token: str) -> None:
        if kind not in {"parent", "worker"} or pid <= 0 or pgid <= 0 or not birth_token:
            raise StateError("process runtime identity is invalid")
        self.connection.execute(
            "INSERT INTO process_runtime(process_key,session_id,worker_attempt_id,kind,pid,pgid,"
            "birth_token,state,started_at) VALUES (?,?,?,?,?,?,?,'running',?)",
            (process_key, session_id, worker_attempt_id, kind, pid, pgid, birth_token, utcnow()),
        )

    def finish_process(self, process_key: str, *, state: str, exit_code: int | None) -> None:
        if state not in {"exited", "terminated", "uncertain"}:
            raise StateError("process runtime terminal state is invalid")
        cursor = self.connection.execute(
            "UPDATE process_runtime SET state=?,finished_at=?,exit_code=? "
            "WHERE process_key=? AND state='running'",
            (state, utcnow(), exit_code, process_key),
        )
        if cursor.rowcount != 1:
            raise StateError("process runtime is absent or inactive")

    def active_processes(self, session_id: str | None = None) -> list[sqlite3.Row]:
        if session_id is None:
            return list(self.connection.execute(
                "SELECT * FROM process_runtime WHERE state='running' ORDER BY kind DESC,started_at"
            ))
        return list(self.connection.execute(
            "SELECT * FROM process_runtime WHERE state='running' AND session_id=? "
            "ORDER BY kind DESC,started_at", (session_id,),
        ))

    def active_wakes(self) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            "SELECT wr.*,ws.expires_at FROM wake_runtime wr JOIN wake_session ws "
            "ON ws.session_id=wr.session_id WHERE wr.state IN ('prepared','running') "
            "ORDER BY wr.started_at"
        ))

    def orphan_sessions(self) -> list[sqlite3.Row]:
        """Return active sessions that crashed before their wake runtime was persisted."""
        return list(self.connection.execute(
            "SELECT ws.* FROM wake_session ws LEFT JOIN wake_runtime wr "
            "ON wr.session_id=ws.session_id WHERE ws.state='active' AND wr.session_id IS NULL "
            "ORDER BY ws.issued_at"
        ))

    @staticmethod
    def _release_reservation_for(db: sqlite3.Connection, session_id: str) -> None:
        reservation = db.execute(
            "SELECT * FROM budget_reservation WHERE session_id=?", (session_id,)
        ).fetchone()
        if reservation is None:
            return
        if reservation["state"] != "active":
            raise StateError("unstarted session budget is not active")
        session = db.execute(
            "SELECT org_id,role FROM wake_session WHERE session_id=?", (session_id,)
        ).fetchone()
        for scope, subject in (("org", session["org_id"]), ("role", session["role"])):
            cursor = db.execute(
                "UPDATE budget_bucket SET reserved_tokens=reserved_tokens-? "
                "WHERE scope=? AND subject=? AND epoch=? AND reserved_tokens>=?",
                (reservation["reserved_tokens"], scope, subject, reservation["epoch"],
                 reservation["reserved_tokens"]),
            )
            if cursor.rowcount != 1:
                raise StateError("unstarted session budget bucket is inconsistent")
        db.execute(
            "UPDATE budget_reservation SET state='released' WHERE reservation_id=?",
            (reservation["reservation_id"],),
        )

    @staticmethod
    def _retry_leased_delivery(db: sqlite3.Connection, delivery_id: str,
                               *, eligible_at: str, error: str) -> None:
        delivery = db.execute(
            "SELECT state FROM delivery WHERE delivery_id=?", (delivery_id,)
        ).fetchone()
        if delivery is None or delivery["state"] != "leased":
            raise StateError("unstarted delivery is not safely leased")
        db.execute(
            "UPDATE delivery SET state='retry_wait',eligible_at=?,lease_owner=NULL,"
            "lease_until=NULL,last_error=? WHERE delivery_id=?",
            (eligible_at, error, delivery_id),
        )
        db.execute(
            "UPDATE route_target SET state='queued' WHERE delivery_id=?", (delivery_id,),
        )

    def recover_orphan_session(self, session_id: str, *, delivery_id: str | None = None,
                               eligible_at: str | None = None,
                               error: str = "supervisor stopped before wake preparation") -> None:
        """Atomically release a session proved to have stopped before wake preparation."""
        with self.immediate() as db:
            session = db.execute(
                "SELECT state FROM wake_session WHERE session_id=?", (session_id,)
            ).fetchone()
            wake_count = db.execute(
                "SELECT count(*) FROM wake_runtime WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            process_count = db.execute(
                "SELECT count(*) FROM process_runtime WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            worker_count = db.execute(
                "SELECT count(*) FROM worker_attempt WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            if (session is None or session["state"] != "active" or wake_count
                    or process_count or worker_count):
                raise StateError("session is not a recoverable pre-wake orphan")
            self._release_reservation_for(db, session_id)
            if delivery_id is not None:
                if eligible_at is None:
                    raise StateError("orphan delivery recovery requires an eligibility time")
                self._retry_leased_delivery(
                    db, delivery_id, eligible_at=eligible_at, error=error)
            db.execute(
                "UPDATE wake_session SET state='revoked' WHERE session_id=? AND state='active'",
                (session_id,),
            )

    def recover_prepared_wake(self, wake_id: str, *, eligible_at: str, error: str) -> None:
        """Atomically release a wake proved never to have created a provider process."""
        with self.immediate() as db:
            wake = db.execute(
                "SELECT * FROM wake_runtime WHERE wake_id=?", (wake_id,)
            ).fetchone()
            if wake is None or wake["state"] != "prepared":
                raise StateError("wake is not prepared for safe recovery")
            session_id = str(wake["session_id"])
            session = db.execute(
                "SELECT state FROM wake_session WHERE session_id=?", (session_id,)
            ).fetchone()
            process_count = db.execute(
                "SELECT count(*) FROM process_runtime WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            worker_count = db.execute(
                "SELECT count(*) FROM worker_attempt WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            if (session is None or session["state"] != "active" or process_count or worker_count):
                raise StateError("prepared wake is not proved process-free")
            self._release_reservation_for(db, session_id)
            if wake["delivery_id"]:
                self._retry_leased_delivery(
                    db, str(wake["delivery_id"]), eligible_at=eligible_at, error=error)
            db.execute(
                "UPDATE wake_runtime SET state='failed',finished_at=?,error_detail=? WHERE wake_id=?",
                (utcnow(), error, wake_id),
            )
            db.execute(
                "UPDATE wake_session SET state='revoked' WHERE session_id=? AND state='active'",
                (session_id,),
            )

    def pending_reconciliations(self) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            "SELECT * FROM reconcile_operation WHERE state='verify_pending' ORDER BY created_at,operation_id"
        ))

    def pending_broker_verifications(self) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            "SELECT * FROM broker_request WHERE state='verify_pending' ORDER BY created_at,idempotency_key"
        ))

    def checkpoint(self, source_id: str, *, initial_watermark: str) -> sqlite3.Row:
        self.connection.execute(
            "INSERT OR IGNORE INTO source_checkpoint(source_id,watermark) VALUES (?,?)",
            (source_id, initial_watermark),
        )
        return self.connection.execute(
            "SELECT * FROM source_checkpoint WHERE source_id=?", (source_id,)
        ).fetchone()

    def begin_snapshot(self, source_id: str) -> str:
        snapshot_id = str(uuid.uuid4())
        self.connection.execute(
            "INSERT INTO source_snapshot(snapshot_id,source_id,state,started_at) VALUES (?,?,?,?)",
            (snapshot_id, source_id, "collecting", utcnow()),
        )
        return snapshot_id

    def stage_observation(self, snapshot_id: str, source_id: str, external_id: str,
                          version_key: str, occurred_at: str,
                          payload: dict[str, Any]) -> bool:
        snapshot = self.connection.execute(
            "SELECT source_id,state FROM source_snapshot WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
        if snapshot is None or snapshot["state"] != "collecting":
            raise StateError("observation requires a collecting snapshot")
        if snapshot["source_id"] != source_id:
            raise StateError("observation source does not match its snapshot")
        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_id}:{external_id}:{version_key}"))
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO snapshot_event(snapshot_id,event_id,source_id,external_id,"
            "version_key,occurred_at,payload_json) VALUES (?,?,?,?,?,?,?)",
            (snapshot_id, event_id, source_id, external_id, version_key, occurred_at,
             json.dumps(payload, sort_keys=True)),
        )
        return cursor.rowcount == 1

    def finish_snapshot(self, snapshot_id: str, *, complete: bool, page_count: int,
                        maximum_event_time: str | None, next_watermark: str | None = None,
                        query_fingerprint: str | None = None, etag: str | None = None,
                        error: str | None = None) -> int:
        with self.immediate() as db:
            row = db.execute("SELECT source_id,state FROM source_snapshot WHERE snapshot_id=?",
                             (snapshot_id,)).fetchone()
            if row is None:
                raise StateError("unknown source snapshot")
            if row["state"] != "collecting":
                raise StateError("source snapshot is already finalized")
            state = "complete" if complete else "incomplete"
            published = 0
            if complete:
                if next_watermark is None:
                    raise StateError("complete snapshot requires a next watermark")
                db.execute(
                    "INSERT OR IGNORE INTO observed_event(event_id,source_id,external_id,"
                    "version_key,occurred_at,payload_json) SELECT event_id,source_id,external_id,"
                    "version_key,occurred_at,payload_json FROM snapshot_event WHERE snapshot_id=?",
                    (snapshot_id,),
                )
                published = int(db.execute("SELECT changes()").fetchone()[0])
                cursor = db.execute(
                    "UPDATE source_checkpoint SET watermark=?,query_fingerprint=?,etag=?,last_complete_snapshot_id=? WHERE source_id=?",
                    (next_watermark, query_fingerprint, etag, snapshot_id, row["source_id"]),
                )
                if cursor.rowcount != 1:
                    raise StateError("complete snapshot has no source checkpoint")
            db.execute(
                "UPDATE source_snapshot SET state=?,completed_at=?,page_count=?,maximum_event_time=?,error_detail=? WHERE snapshot_id=?",
                (state, utcnow(), page_count, maximum_event_time, error, snapshot_id),
            )
            return published

    def reserve_budget(self, session_id: str, *, epoch: str, requested_tokens: int,
                       buckets: list[tuple[str, str, int]]) -> str:
        if requested_tokens <= 0:
            raise StateError("budget reservation must be positive")
        reservation_id = str(uuid.uuid4())
        with self.immediate() as db:
            session = db.execute(
                "SELECT org_id,role FROM wake_session WHERE session_id=? AND state='active' "
                "AND expires_at>?", (session_id, utcnow()),
            ).fetchone()
            if session is None:
                raise StateError("budget reservation requires an active session")
            if len(buckets) != 2 or len({(scope, subject) for scope, subject, _ in buckets}) != 2:
                raise StateError("budget reservation requires exactly one organization and role bucket")
            if {(scope, subject) for scope, subject, _ in buckets} != {
                    ("org", session["org_id"]), ("role", session["role"])}:
                raise StateError("budget buckets do not match the bound session")
            for scope, subject, limit in buckets:
                if limit <= 0:
                    raise StateError("budget bucket limit must be positive")
                db.execute(
                    "INSERT OR IGNORE INTO budget_bucket(scope,subject,epoch,token_limit) VALUES (?,?,?,?)",
                    (scope, subject, epoch, limit),
                )
                row = db.execute(
                    "SELECT * FROM budget_bucket WHERE scope=? AND subject=? AND epoch=?",
                    (scope, subject, epoch),
                ).fetchone()
                if int(row["token_limit"]) != limit:
                    raise StateError(f"budget policy changed during epoch: {scope}/{subject}")
                if row["consumed_tokens"] + row["reserved_tokens"] + requested_tokens > row["token_limit"]:
                    raise StateError(f"budget hold: {scope}/{subject}")
            for scope, subject, _limit in buckets:
                db.execute(
                    "UPDATE budget_bucket SET reserved_tokens=reserved_tokens+? WHERE scope=? AND subject=? AND epoch=?",
                    (requested_tokens, scope, subject, epoch),
                )
            db.execute(
                "INSERT INTO budget_reservation VALUES (?,?,?,?,?)",
                (reservation_id, session_id, epoch, requested_tokens, "active"),
            )
        return reservation_id

    def settle_budget(self, reservation_id: str, *, consumed_tokens: int) -> None:
        """Settle every bucket atomically; overage becomes uncertain, never silently free."""
        if consumed_tokens < 0:
            raise StateError("consumed token count must not be negative")
        uncertain = False
        with self.immediate() as db:
            reservation = db.execute(
                "SELECT * FROM budget_reservation WHERE reservation_id=?", (reservation_id,)
            ).fetchone()
            if reservation is None or reservation["state"] != "active":
                raise StateError("budget reservation is absent or inactive")
            reserved = int(reservation["reserved_tokens"])
            uncertain = consumed_tokens > reserved
            session = db.execute(
                "SELECT org_id,role FROM wake_session WHERE session_id=?",
                (reservation["session_id"],),
            ).fetchone()
            if session is None:
                raise StateError("budget reservation session is absent")
            buckets = (("org", session["org_id"]), ("role", session["role"]))
            for scope, subject in buckets:
                row = db.execute(
                    "SELECT * FROM budget_bucket WHERE scope=? AND subject=? AND epoch=?",
                    (scope, subject, reservation["epoch"]),
                ).fetchone()
                if row is None or int(row["reserved_tokens"]) < reserved:
                    raise StateError("budget bucket does not cover the reservation")
                if int(row["consumed_tokens"]) + consumed_tokens > int(row["token_limit"]):
                    uncertain = True
            # Actual usage is known, so charge it even when it crossed a reservation
            # or limit.  Retaining only the smaller reservation would fail open.
            for scope, subject in buckets:
                db.execute(
                    "UPDATE budget_bucket SET reserved_tokens=reserved_tokens-?, "
                    "consumed_tokens=consumed_tokens+? WHERE scope=? AND subject=? AND epoch=?",
                    (reserved, consumed_tokens, scope, subject, reservation["epoch"]),
                )
            db.execute(
                "UPDATE budget_reservation SET state=? WHERE reservation_id=?",
                ("uncertain" if uncertain else "settled", reservation_id),
            )
        if uncertain:
            raise StateError("actual usage exceeded a budget boundary; manual review required")

    def mark_budget_uncertain(self, reservation_id: str) -> None:
        """Preserve an ambiguous reservation against both limits until a human resolves it."""
        cursor = self.connection.execute(
            "UPDATE budget_reservation SET state='uncertain' "
            "WHERE reservation_id=? AND state='active'", (reservation_id,)
        )
        if cursor.rowcount != 1:
            raise StateError("budget reservation is absent or inactive")

    def release_budget(self, reservation_id: str) -> None:
        """Release a reservation only when the provider was proved not to have started."""
        with self.immediate() as db:
            reservation = db.execute(
                "SELECT * FROM budget_reservation WHERE reservation_id=?", (reservation_id,)
            ).fetchone()
            if reservation is None or reservation["state"] != "active":
                raise StateError("budget reservation is absent or inactive")
            session = db.execute(
                "SELECT org_id,role FROM wake_session WHERE session_id=?",
                (reservation["session_id"],),
            ).fetchone()
            if session is None:
                raise StateError("budget reservation session is absent")
            for scope, subject in (("org", session["org_id"]), ("role", session["role"])):
                cursor = db.execute(
                    "UPDATE budget_bucket SET reserved_tokens=reserved_tokens-? "
                    "WHERE scope=? AND subject=? AND epoch=? AND reserved_tokens>=?",
                    (reservation["reserved_tokens"], scope, subject, reservation["epoch"],
                     reservation["reserved_tokens"]),
                )
                if cursor.rowcount != 1:
                    raise StateError("budget bucket does not cover the reservation")
            db.execute(
                "UPDATE budget_reservation SET state='released' WHERE reservation_id=?",
                (reservation_id,),
            )

    def add_route(self, event_id: str, role: str) -> None:
        """Record one durable event-to-role target without duplicating an existing route."""
        try:
            self.connection.execute(
                "INSERT OR IGNORE INTO route_target(event_id,role,state) VALUES (?,?,'pending')",
                (event_id, role),
            )
        except sqlite3.IntegrityError as exc:
            raise StateError("route references an unknown observed event") from exc

    def enqueue_delivery(self, role: str, event_ids: list[str], *, idempotency_key: str,
                         eligible_at: str | None = None) -> str:
        """Atomically bind pending routes to one delivery; a route can never be double queued."""
        unique = sorted(set(event_ids))
        if not role or not unique or not idempotency_key:
            raise StateError("delivery requires a role, events, and idempotency key")
        delivery_id = str(uuid.uuid4())
        with self.immediate() as db:
            existing = db.execute(
                "SELECT delivery_id,role FROM delivery WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if existing["role"] != role:
                    raise StateError("delivery idempotency key is bound to another role")
                bound = [row[0] for row in db.execute(
                    "SELECT event_id FROM delivery_event WHERE delivery_id=? ORDER BY event_id",
                    (existing["delivery_id"],),
                )]
                if bound != unique:
                    raise StateError("delivery idempotency key is bound to different events")
                return str(existing["delivery_id"])
            for event_id in unique:
                route = db.execute(
                    "SELECT state,delivery_id FROM route_target WHERE event_id=? AND role=?",
                    (event_id, role),
                ).fetchone()
                if route is None or route["state"] != "pending" or route["delivery_id"] is not None:
                    raise StateError("route is absent or already assigned")
            db.execute(
                "INSERT INTO delivery(delivery_id,idempotency_key,role,state,eligible_at) "
                "VALUES (?,?,?,'queued',?)",
                (delivery_id, idempotency_key, role, eligible_at or utcnow()),
            )
            for event_id in unique:
                db.execute(
                    "INSERT INTO delivery_event(delivery_id,event_id,role) VALUES (?,?,?)",
                    (delivery_id, event_id, role),
                )
                db.execute(
                    "UPDATE route_target SET state='queued',delivery_id=? "
                    "WHERE event_id=? AND role=?",
                    (delivery_id, event_id, role),
                )
        return delivery_id

    def lease_delivery(self, owner: str, *, now: str | None = None,
                       lease_seconds: int = 300, max_attempts: int = 3) -> sqlite3.Row | None:
        """Lease one eligible delivery and quarantine any expired running attempt."""
        if not owner or lease_seconds <= 0 or max_attempts <= 0:
            raise StateError("delivery lease requires an owner and positive limits")
        instant = dt.datetime.fromisoformat((now or utcnow()).replace("Z", "+00:00"))
        timestamp = instant.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        lease_until = (instant + dt.timedelta(seconds=lease_seconds)).astimezone(
            dt.timezone.utc).isoformat().replace("+00:00", "Z")
        with self.immediate() as db:
            expired_running = [row[0] for row in db.execute(
                "SELECT delivery_id FROM delivery WHERE state='running' AND lease_until<=?",
                (timestamp,),
            )]
            for delivery_id in expired_running:
                db.execute(
                    "UPDATE delivery SET state='uncertain',last_error='running lease expired',"
                    "lease_owner=NULL,lease_until=NULL WHERE delivery_id=?", (delivery_id,)
                )
                db.execute(
                    "UPDATE route_target SET state='manual_review' WHERE delivery_id=?",
                    (delivery_id,),
                )
            db.execute(
                "UPDATE delivery SET state='retry_wait',last_error='lease expired',"
                "lease_owner=NULL,lease_until=NULL "
                "WHERE state='leased' AND lease_until<=?", (timestamp,)
            )
            exhausted = [row[0] for row in db.execute(
                "SELECT delivery_id FROM delivery WHERE state IN ('queued','retry_wait') "
                "AND attempt>=?", (max_attempts,)
            )]
            for delivery_id in exhausted:
                db.execute(
                    "UPDATE delivery SET state='dead_letter',last_error='retry ceiling reached' "
                    "WHERE delivery_id=?", (delivery_id,)
                )
                db.execute(
                    "UPDATE route_target SET state='dead_letter' WHERE delivery_id=?",
                    (delivery_id,),
                )
            row = db.execute(
                "SELECT * FROM delivery WHERE state IN ('queued','retry_wait') "
                "AND eligible_at<=? ORDER BY eligible_at,delivery_id LIMIT 1", (timestamp,)
            ).fetchone()
            if row is None:
                return None
            cursor = db.execute(
                "UPDATE delivery SET state='leased',attempt=attempt+1,lease_owner=?,lease_until=? "
                "WHERE delivery_id=? AND state IN ('queued','retry_wait')",
                (owner, lease_until, row["delivery_id"]),
            )
            if cursor.rowcount != 1:
                raise StateError("delivery lease race")
            db.execute(
                "UPDATE route_target SET state='leased' WHERE delivery_id=?",
                (row["delivery_id"],),
            )
            return db.execute("SELECT * FROM delivery WHERE delivery_id=?",
                              (row["delivery_id"],)).fetchone()

    def start_delivery(self, delivery_id: str, owner: str, *, now: str | None = None) -> None:
        timestamp = now or utcnow()
        cursor = self.connection.execute(
            "UPDATE delivery SET state='running' WHERE delivery_id=? AND state='leased' "
            "AND lease_owner=? AND lease_until>?", (delivery_id, owner, timestamp)
        )
        if cursor.rowcount != 1:
            raise StateError("delivery lease is absent, expired, or owned elsewhere")

    def finish_delivery(self, delivery_id: str, owner: str, *, outcome: str,
                        error: str | None = None, eligible_at: str | None = None) -> None:
        """Complete a leased/running delivery with explicit retry and ambiguity semantics."""
        targets = {
            "succeeded": ("succeeded", "delivered"),
            "retry": ("retry_wait", "queued"),
            "budget_hold": ("budget_hold", "queued"),
            "dead_letter": ("dead_letter", "dead_letter"),
            "uncertain": ("uncertain", "manual_review"),
        }
        if outcome not in targets:
            raise StateError(f"invalid delivery outcome: {outcome}")
        delivery_state, route_state = targets[outcome]
        with self.immediate() as db:
            row = db.execute("SELECT * FROM delivery WHERE delivery_id=?", (delivery_id,)).fetchone()
            if row is None or row["state"] not in {"leased", "running"} or row["lease_owner"] != owner:
                raise StateError("delivery is not held by this owner")
            allowed = ({"retry", "budget_hold", "dead_letter", "uncertain"}
                       if row["state"] == "leased"
                       else {"succeeded", "dead_letter", "uncertain"})
            if outcome not in allowed:
                raise StateError(f"delivery outcome {outcome!r} is unsafe from {row['state']}")
            when = eligible_at or utcnow()
            attempt_adjustment = -1 if row["state"] == "leased" and outcome == "budget_hold" else 0
            db.execute(
                "UPDATE delivery SET state=?,eligible_at=?,lease_owner=NULL,lease_until=NULL,last_error=?,"
                "attempt=attempt+? "
                "WHERE delivery_id=?",
                (delivery_state, when, error, attempt_adjustment, delivery_id),
            )
            db.execute(
                "UPDATE route_target SET state=? WHERE delivery_id=?", (route_state, delivery_id)
            )

    def release_budget_hold(self, delivery_id: str, *, eligible_at: str | None = None) -> None:
        with self.immediate() as db:
            cursor = db.execute(
                "UPDATE delivery SET state='retry_wait',eligible_at=?,last_error=NULL "
                "WHERE delivery_id=? AND state='budget_hold'",
                (eligible_at or utcnow(), delivery_id),
            )
            if cursor.rowcount != 1:
                raise StateError("delivery is not budget-held")
            db.execute(
                "UPDATE route_target SET state='queued' WHERE delivery_id=?", (delivery_id,)
            )

    def force_delivery_uncertain(self, delivery_id: str, *, error: str) -> None:
        """Quarantine a delivery only after the host has stopped its process groups."""
        with self.immediate() as db:
            cursor = db.execute(
                "UPDATE delivery SET state='uncertain',lease_owner=NULL,lease_until=NULL,last_error=? "
                "WHERE delivery_id=? AND state IN ('leased','running')", (error, delivery_id),
            )
            if cursor.rowcount != 1:
                raise StateError("delivery is absent or not active")
            db.execute(
                "UPDATE route_target SET state='manual_review' WHERE delivery_id=?",
                (delivery_id,),
            )

    def reserve_reconcile(self, *, idempotency_key: str, source_key: str, operation: str,
                          precondition: dict[str, Any]) -> sqlite3.Row:
        """Journal a deterministic governance write before any remote side effect."""
        if operation not in {"create", "move", "close"}:
            raise StateError("unsupported reconciliation operation")
        canonical = json.dumps(precondition, sort_keys=True, separators=(",", ":"))
        now = utcnow()
        with self.immediate() as db:
            existing = db.execute(
                "SELECT * FROM reconcile_operation WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if existing:
                if (existing["source_key"], existing["operation"],
                        existing["precondition_json"]) != (source_key, operation, canonical):
                    raise StateError("reconciliation idempotency key was reused")
                return existing
            operation_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO reconcile_operation(operation_id,idempotency_key,source_key,operation,"
                "precondition_json,state,created_at,updated_at) VALUES (?,?,?,?,?,'planned',?,?)",
                (operation_id, idempotency_key, source_key, operation, canonical, now, now),
            )
            return db.execute(
                "SELECT * FROM reconcile_operation WHERE operation_id=?", (operation_id,)
            ).fetchone()

    def mark_reconcile_applying(self, operation_id: str) -> None:
        cursor = self.connection.execute(
            "UPDATE reconcile_operation SET state='applying',updated_at=? "
            "WHERE operation_id=? AND state='planned'",
            (utcnow(), operation_id),
        )
        if cursor.rowcount != 1:
            raise StateError("reconciliation operation is not applicable")

    def finish_reconcile(self, operation_id: str, *, state: str,
                         result: dict[str, Any] | None = None) -> None:
        if state not in {"verify_pending", "succeeded", "dead_letter"}:
            raise StateError("invalid reconciliation terminal state")
        with self.immediate() as db:
            row = db.execute(
                "SELECT state FROM reconcile_operation WHERE operation_id=?", (operation_id,)
            ).fetchone()
            allowed = {
                "applying": {"verify_pending", "succeeded", "dead_letter"},
                "verify_pending": {"succeeded", "dead_letter"},
            }
            if row is None or state not in allowed.get(str(row["state"]), set()):
                raise StateError("reconciliation operation cannot transition from its current state")
            db.execute(
                "UPDATE reconcile_operation SET state=?,external_result_json=?,updated_at=? "
                "WHERE operation_id=? AND state=?",
                (state, json.dumps(result, sort_keys=True) if result is not None else None,
                 utcnow(), operation_id, row["state"]),
            )
