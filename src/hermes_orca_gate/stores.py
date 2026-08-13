"""Durable append-only SQLite stores for gate records and recovery state."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import DeniedError, PersistenceError, ReconciliationRequired

SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_events (
    event_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS owner_authorizations (
    authorization_id TEXT PRIMARY KEY,
    basis_decision_event_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    issued_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resolution_decisions (
    event_id TEXT PRIMARY KEY,
    basis_decision_event_id TEXT NOT NULL,
    authorization_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mutation_intents (
    mutation_intent_id TEXT PRIMARY KEY,
    orca_request_id TEXT NOT NULL UNIQUE,
    method TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    credential_binding_id TEXT NOT NULL,
    target_instance_identity TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approved_invocations (
    operation_id TEXT PRIMARY KEY,
    decision_event_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consumption_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL UNIQUE,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exec_attempts (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outcomes (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    terminal INTEGER NOT NULL CHECK (terminal IN (0, 1)),
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recovery_records (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL,
    code TEXT NOT NULL,
    payload TEXT NOT NULL,
    terminal_closure INTEGER NOT NULL CHECK (terminal_closure IN (0, 1)),
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provenance_records (
    dispatch_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconciliation_state (
    scope TEXT PRIMARY KEY,
    required INTEGER NOT NULL CHECK (required IN (0, 1)),
    reason TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconciliation_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    event_type TEXT NOT NULL,
    authorization_id TEXT,
    payload TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observation_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    payload TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observation_state (
    scope TEXT PRIMARY KEY,
    consecutive_idle INTEGER NOT NULL,
    cursor_payload TEXT,
    in_flight INTEGER NOT NULL CHECK (in_flight IN (0, 1)),
    started_ms INTEGER NOT NULL,
    deadline_ms INTEGER NOT NULL,
    epoch_id TEXT NOT NULL,
    next_probe_ms INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""

IMMUTABLE_TABLES = (
    "decision_events",
    "owner_authorizations",
    "resolution_decisions",
    "mutation_intents",
    "approved_invocations",
    "consumption_events",
    "exec_attempts",
    "outcomes",
    "recovery_records",
    "provenance_records",
    "reconciliation_events",
    "observation_events",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class GateStore:
    """One logical store with separately injectable primary and recovery paths."""

    def __init__(
        self,
        path: str | Path,
        *,
        source_contract_sha: str = "403c60bbadb734d870a22c372aca8a988e348d21",
    ):
        self.path = str(Path(path))
        self.source_contract_sha = source_contract_sha
        self._failpoints: set[str] = set()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        database = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        database.row_factory = sqlite3.Row
        try:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("PRAGMA synchronous=FULL")
            database.execute("PRAGMA foreign_keys=ON")
            database.execute("PRAGMA busy_timeout=5000")
            yield database
        finally:
            database.close()

    def initialize(self) -> None:
        try:
            with self.connect() as database:
                database.executescript(SCHEMA)
                for table in IMMUTABLE_TABLES:
                    database.execute(
                        f"CREATE TRIGGER IF NOT EXISTS no_update_{table} "
                        f"BEFORE UPDATE ON {table} BEGIN "
                        "SELECT RAISE(ABORT, 'immutable table'); END"
                    )
                    database.execute(
                        f"CREATE TRIGGER IF NOT EXISTS no_delete_{table} "
                        f"BEFORE DELETE ON {table} BEGIN "
                        "SELECT RAISE(ABORT, 'immutable table'); END"
                    )
        except sqlite3.Error as exc:
            raise PersistenceError(str(exc)) from exc

    def inject_failure(self, name: str) -> None:
        self._failpoints.add(name)

    def clear_failure(self, name: str) -> None:
        self._failpoints.discard(name)

    def _check(self, name: str) -> None:
        if name in self._failpoints:
            raise PersistenceError(f"injected persistence failure: {name}")

    def pragmas(self) -> dict[str, Any]:
        with self.connect() as database:
            return {
                "journal_mode": database.execute("PRAGMA journal_mode").fetchone()[0],
                "synchronous": database.execute("PRAGMA synchronous").fetchone()[0],
                "foreign_keys": database.execute("PRAGMA foreign_keys").fetchone()[0],
            }

    def table_names(self) -> set[str]:
        with self.connect() as database:
            return {
                row[0]
                for row in database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }

    def _insert(self, failure_name: str, sql: str, values: tuple[Any, ...]) -> None:
        self._check(failure_name)
        try:
            with self.connect() as database:
                database.execute("BEGIN IMMEDIATE")
                database.execute(sql, values)
                database.execute("COMMIT")
        except sqlite3.Error as exc:
            raise PersistenceError(str(exc)) from exc

    def _one(self, sql: str, values: tuple[Any, ...]) -> dict[str, Any] | None:
        self._check("read")
        try:
            with self.connect() as database:
                row = database.execute(sql, values).fetchone()
                return dict(row) if row else None
        except sqlite3.Error as exc:
            raise PersistenceError(str(exc)) from exc

    def append_decision(self, event_id: str, payload: dict[str, Any]) -> None:
        self._insert(
            "decision",
            "INSERT INTO decision_events VALUES(?,?,?)",
            (event_id, _json(payload), _now()),
        )

    def get_decision(self, event_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT payload FROM decision_events WHERE event_id=?", (event_id,)
        )
        return json.loads(row["payload"]) if row else None

    def append_owner_authorization(
        self,
        authorization_id: str,
        basis_decision_event_id: str,
        payload: dict[str, Any],
    ) -> None:
        self._insert(
            "owner_authorization",
            "INSERT INTO owner_authorizations VALUES(?,?,?,?)",
            (
                authorization_id,
                basis_decision_event_id,
                _json(payload),
                payload.get("issued_at", _now()),
            ),
        )

    def get_owner_authorization(self, authorization_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT basis_decision_event_id,payload FROM owner_authorizations "
            "WHERE authorization_id=?",
            (authorization_id,),
        )
        if not row:
            return None
        result = json.loads(row["payload"])
        result["basis_decision_event_id"] = row["basis_decision_event_id"]
        return result

    def append_resolution_decision(
        self,
        event_id: str,
        basis_decision_event_id: str,
        authorization_id: str,
        payload: dict[str, Any],
    ) -> None:
        self._insert(
            "resolution_decision",
            "INSERT INTO resolution_decisions VALUES(?,?,?,?,?)",
            (event_id, basis_decision_event_id, authorization_id, _json(payload), _now()),
        )

    def get_resolution_decision(self, event_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT basis_decision_event_id,authorization_id,payload "
            "FROM resolution_decisions WHERE event_id=?",
            (event_id,),
        )
        if not row:
            return None
        result = json.loads(row["payload"])
        result.update(
            basis_decision_event_id=row["basis_decision_event_id"],
            authorization_id=row["authorization_id"],
        )
        return result

    def create_mutation_intent(
        self,
        *,
        intent_id: str,
        request_id: str,
        method: str,
        payload_digest: str,
        credential_binding_id: str,
        target_instance_identity: str,
    ) -> None:
        self._insert(
            "mutation_intent",
            "INSERT INTO mutation_intents VALUES(?,?,?,?,?,?,?)",
            (
                intent_id,
                request_id,
                method,
                payload_digest,
                credential_binding_id,
                target_instance_identity,
                _now(),
            ),
        )

    def get_mutation_intent(self, intent_id: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM mutation_intents WHERE mutation_intent_id=?", (intent_id,)
        )

    def recovery_request_id(
        self,
        intent_id: str,
        *,
        method: str,
        payload_digest: str,
        credential_binding_id: str | None,
        target_instance_identity: str,
    ) -> str:
        intent = self.get_mutation_intent(intent_id)
        if not intent:
            raise DeniedError("unknown mutation intent")
        if (
            intent["method"] != method
            or intent["payload_digest"] != payload_digest
            or intent["target_instance_identity"] != target_instance_identity
        ):
            raise DeniedError("receipt method/payload/target identity mismatch")
        if (
            credential_binding_id is None
            or intent["credential_binding_id"] != credential_binding_id
        ):
            self.mark_reconciliation(intent_scope := f"intent:{intent_id}", "RECEIPT_RECOVERY_UNAVAILABLE")
            raise ReconciliationRequired(
                "credential rotation or unavailability prevents receipt recovery",
                details={"scope": intent_scope},
            )
        return str(intent["orca_request_id"])

    def append_approved_invocation(self, invocation: Any) -> None:
        if is_dataclass(invocation):
            payload = asdict(invocation)
        elif isinstance(invocation, dict):
            payload = dict(invocation)
        else:
            raise TypeError("invocation must be a dataclass or dictionary")
        self._insert(
            "approved_invocation",
            "INSERT INTO approved_invocations VALUES(?,?,?,?)",
            (
                payload["operation_id"],
                payload["decision_event_id"],
                _json(payload),
                _now(),
            ),
        )

    def get_approved_invocation(self, operation_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT payload FROM approved_invocations WHERE operation_id=?",
            (operation_id,),
        )
        return json.loads(row["payload"]) if row else None

    def consume(self, operation_id: str) -> bool:
        self._check("consume")
        try:
            with self.connect() as database:
                database.execute("BEGIN IMMEDIATE")
                database.execute(
                    "INSERT INTO consumption_events(operation_id,recorded_at) VALUES(?,?)",
                    (operation_id, _now()),
                )
                database.execute("COMMIT")
                return True
        except sqlite3.IntegrityError:
            return False
        except sqlite3.Error as exc:
            raise PersistenceError(str(exc)) from exc

    def append_exec_attempt(
        self, operation_id: str, payload: dict[str, Any] | None = None
    ) -> None:
        self._insert(
            "exec_attempt",
            "INSERT INTO exec_attempts(operation_id,payload,recorded_at) VALUES(?,?,?)",
            (operation_id, _json(payload or {}), _now()),
        )

    def append_outcome(
        self, operation_id: str, payload: dict[str, Any], *, terminal: bool = True
    ) -> None:
        self._insert(
            "outcome",
            "INSERT INTO outcomes(operation_id,payload,terminal,recorded_at) VALUES(?,?,?,?)",
            (operation_id, _json(payload), int(terminal), _now()),
        )

    def append_outcome_with_provenance(
        self,
        operation_id: str,
        outcome: dict[str, Any],
        dispatch_id: str,
        provenance: dict[str, Any],
    ) -> None:
        if provenance.get("origin") != "local" or not provenance.get(
            "created_by_operation_id"
        ):
            raise DeniedError("provenance must be derived from a gate outcome")
        self._check("outcome")
        self._check("provenance")
        record = dict(provenance)
        record.setdefault("source_contract_sha", self.source_contract_sha)
        try:
            with self.connect() as database:
                database.execute("BEGIN IMMEDIATE")
                database.execute(
                    "INSERT INTO outcomes(operation_id,payload,terminal,recorded_at) "
                    "VALUES(?,?,1,?)",
                    (operation_id, _json(outcome), _now()),
                )
                database.execute(
                    "INSERT INTO provenance_records VALUES(?,?,?)",
                    (dispatch_id, _json(record), _now()),
                )
                database.execute("COMMIT")
        except sqlite3.Error as exc:
            raise PersistenceError(str(exc)) from exc

    def append_recovery(
        self,
        operation_id: str,
        code: str,
        payload: dict[str, Any],
        *,
        terminal_closure: bool = False,
    ) -> None:
        self._insert(
            "recovery_channel",
            "INSERT INTO recovery_records(operation_id,code,payload,terminal_closure,recorded_at) "
            "VALUES(?,?,?,?,?)",
            (operation_id, code, _json(payload), int(terminal_closure), _now()),
        )

    def record_provenance(self, dispatch_id: str, payload: dict[str, Any]) -> None:
        if payload.get("origin") != "local" or not payload.get(
            "created_by_operation_id"
        ):
            raise DeniedError("provenance must be derived from a gate outcome")
        record = dict(payload)
        record.setdefault("source_contract_sha", self.source_contract_sha)
        self._insert(
            "provenance",
            "INSERT INTO provenance_records VALUES(?,?,?)",
            (dispatch_id, _json(record), _now()),
        )

    def get_provenance(self, dispatch_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT payload FROM provenance_records WHERE dispatch_id=?", (dispatch_id,)
        )
        return json.loads(row["payload"]) if row else None

    def provenance_for_terminal(self, terminal_handle: str) -> dict[str, Any] | None:
        self._check("read")
        try:
            with self.connect() as database:
                database.execute("BEGIN IMMEDIATE")
                rows = database.execute(
                    "SELECT payload FROM provenance_records ORDER BY rowid"
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(str(exc)) from exc
        for row in rows:
            payload = json.loads(row["payload"])
            if payload.get("terminal_handle") == terminal_handle:
                return payload
        return None

    def require_local_provenance(self, dispatch_id: str) -> dict[str, Any]:
        try:
            record = self.get_provenance(dispatch_id)
        except PersistenceError:
            self.mark_reconciliation(f"dispatch:{dispatch_id}", "PROVENANCE_READ_FAILED")
            raise
        if not record or record.get("origin") != "local":
            scope = f"dispatch:{dispatch_id}"
            self.mark_reconciliation(scope, "CONTAINMENT_PATH_UNAVAILABLE")
            raise ReconciliationRequired(
                "durable known-local provenance required",
                details={"scope": scope, "code": "CONTAINMENT_PATH_UNAVAILABLE"},
            )
        if self.reconciliation_required(f"dispatch:{dispatch_id}"):
            raise ReconciliationRequired("dispatch requires reconciliation")
        return record

    def mark_reconciliation(self, scope: str, reason: str) -> None:
        self._check("reconciliation")
        try:
            with self.connect() as database:
                database.execute("BEGIN IMMEDIATE")
                database.execute(
                    "INSERT INTO reconciliation_events"
                    "(scope,event_type,authorization_id,payload,recorded_at) VALUES(?,?,?,?,?)",
                    (scope, "required", None, _json({"reason": reason}), _now()),
                )
                database.execute(
                    "INSERT INTO reconciliation_state VALUES(?,?,?,?) "
                    "ON CONFLICT(scope) DO UPDATE SET "
                    "required=excluded.required,reason=excluded.reason,updated_at=excluded.updated_at",
                    (scope, 1, reason, _now()),
                )
                database.execute("COMMIT")
        except sqlite3.Error as exc:
            raise PersistenceError(str(exc)) from exc

    def close_reconciliation(
        self, scope: str, authorization_id: str, evidence: dict[str, Any]
    ) -> None:
        self._check("reconciliation")
        try:
            with self.connect() as database:
                database.execute("BEGIN IMMEDIATE")
                database.execute(
                    "INSERT INTO reconciliation_events"
                    "(scope,event_type,authorization_id,payload,recorded_at) VALUES(?,?,?,?,?)",
                    (scope, "closed", authorization_id, _json(evidence), _now()),
                )
                updated = database.execute(
                    "UPDATE reconciliation_state SET required=0,reason=?,updated_at=? "
                    "WHERE scope=?",
                    ("closed", _now(), scope),
                )
                if updated.rowcount != 1:
                    raise sqlite3.IntegrityError("reconciliation scope does not exist")
                database.execute("COMMIT")
        except sqlite3.Error as exc:
            raise PersistenceError(str(exc)) from exc

    def reconciliation_required(self, scope: str) -> bool:
        row = self._one(
            "SELECT required FROM reconciliation_state WHERE scope=?", (scope,)
        )
        return bool(row and row["required"])

    def startup_sweep(self) -> list[str]:
        self._check("startup_sweep")
        try:
            with self.connect() as database:
                database.execute("BEGIN IMMEDIATE")
                rows = database.execute(
                    "SELECT consumption.operation_id, invocation.payload AS invocation_payload "
                    "FROM consumption_events AS consumption "
                    "LEFT JOIN approved_invocations AS invocation "
                    "ON invocation.operation_id=consumption.operation_id "
                    "WHERE NOT EXISTS (SELECT 1 FROM outcomes AS outcome "
                    "WHERE outcome.operation_id=consumption.operation_id AND outcome.terminal=1) "
                    "AND NOT EXISTS (SELECT 1 FROM recovery_records AS recovery "
                    "WHERE recovery.operation_id=consumption.operation_id "
                    "AND recovery.terminal_closure=1) ORDER BY consumption.operation_id"
                ).fetchall()
                provenance_rows = database.execute(
                    "SELECT dispatch_id,payload FROM provenance_records ORDER BY rowid"
                ).fetchall()
                abandoned_probes = database.execute(
                    "SELECT scope FROM observation_state WHERE in_flight=1 ORDER BY scope"
                ).fetchall()
                for probe in abandoned_probes:
                    database.execute(
                        "UPDATE observation_state SET in_flight=0,consecutive_idle=0,"
                        "updated_at=? WHERE scope=?",
                        (_now(), probe["scope"]),
                    )
                    database.execute(
                        "INSERT INTO observation_events(scope,payload,recorded_at) "
                        "VALUES(?,?,?)",
                        (
                            probe["scope"],
                            _json(
                                {
                                    "route": "RUNTIME_UNKNOWN",
                                    "reason": "startup_recovered_probe_lease",
                                }
                            ),
                            _now(),
                        ),
                    )
                database.execute("COMMIT")
        except sqlite3.Error as exc:
            raise PersistenceError(str(exc)) from exc
        operation_scopes = [f"operation:{row['operation_id']}" for row in rows]
        affected_scopes = set(operation_scopes)
        abandoned_scopes = {row["scope"] for row in abandoned_probes}
        operation_ids = {row["operation_id"] for row in rows}
        for row in rows:
            if row["invocation_payload"]:
                invocation = json.loads(row["invocation_payload"])
                if invocation.get("mutation_intent_id"):
                    affected_scopes.add(f"intent:{invocation['mutation_intent_id']}")
                affected_scopes.update(invocation.get("affected_scopes") or ())
        for row in provenance_rows:
            provenance = json.loads(row["payload"])
            if provenance.get("created_by_operation_id") not in operation_ids:
                continue
            affected_scopes.add(f"dispatch:{row['dispatch_id']}")
            if provenance.get("mutation_intent_id"):
                affected_scopes.add(f"intent:{provenance['mutation_intent_id']}")
            if provenance.get("run_id") and provenance.get("task_id"):
                affected_scopes.add(
                    f"task:{provenance['run_id']}:{provenance['task_id']}"
                )
        for scope in sorted(affected_scopes):
            if not self.reconciliation_required(scope):
                self.mark_reconciliation(scope, "CONSUMED_WITHOUT_OUTCOME")
        for scope in sorted(abandoned_scopes):
            if not self.reconciliation_required(scope):
                self.mark_reconciliation(scope, "OBSERVATION_EXECUTION_INTERRUPTED")
        return operation_scopes

    def append_observation(self, scope: str, payload: dict[str, Any]) -> None:
        self._insert(
            "observation",
            "INSERT INTO observation_events(scope,payload,recorded_at) VALUES(?,?,?)",
            (scope, _json(payload), _now()),
        )

    def begin_observation_probe(
        self,
        scope: str,
        *,
        started_ms: int,
        deadline_ms: int,
        epoch_id: str,
        now_ms: int | None = None,
    ) -> dict[str, Any] | None:
        self._check("observation")
        if self.reconciliation_required(scope):
            raise ReconciliationRequired(
                "observation scope requires reconciliation",
                details={"scope": scope},
            )
        try:
            with self.connect() as database:
                database.execute("BEGIN IMMEDIATE")
                row = database.execute(
                    "SELECT * FROM observation_state WHERE scope=?", (scope,)
                ).fetchone()
                if row is None:
                    database.execute(
                        "INSERT INTO observation_state VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            scope,
                            0,
                            None,
                            1,
                            started_ms,
                            deadline_ms,
                            epoch_id,
                            started_ms,
                            _now(),
                        ),
                    )
                    database.execute("COMMIT")
                    return {
                        "consecutive_idle": 0,
                        "cursor": None,
                        "started_ms": started_ms,
                        "deadline_ms": deadline_ms,
                        "epoch_id": epoch_id,
                    }
                state = dict(row)
                if state["epoch_id"] != epoch_id:
                    database.execute("ROLLBACK")
                    raise ReconciliationRequired(
                        "observation process epoch changed",
                        details={"scope": scope},
                    )
                effective_now = started_ms if now_ms is None else now_ms
                if state["in_flight"]:
                    if effective_now < state["deadline_ms"]:
                        database.execute("ROLLBACK")
                        return None
                    database.execute("COMMIT")
                    return {
                        "deadline_expired": True,
                        "consecutive_idle": state["consecutive_idle"],
                        "cursor": (
                            json.loads(state["cursor_payload"])
                            if state["cursor_payload"] is not None
                            else None
                        ),
                        "started_ms": state["started_ms"],
                        "deadline_ms": state["deadline_ms"],
                        "epoch_id": state["epoch_id"],
                    }
                if effective_now < state["next_probe_ms"]:
                    database.execute("ROLLBACK")
                    return {
                        "deferred": True,
                        "next_probe_ms": state["next_probe_ms"],
                        "deadline_ms": state["deadline_ms"],
                        "consecutive_idle": state["consecutive_idle"],
                        "cursor": (
                            json.loads(state["cursor_payload"])
                            if state["cursor_payload"] is not None
                            else None
                        ),
                    }
                database.execute(
                    "UPDATE observation_state SET in_flight=1,updated_at=? WHERE scope=?",
                    (_now(), scope),
                )
                database.execute("COMMIT")
                return {
                    "consecutive_idle": state["consecutive_idle"],
                    "cursor": (
                        json.loads(state["cursor_payload"])
                        if state["cursor_payload"] is not None
                        else None
                    ),
                    "started_ms": state["started_ms"],
                    "deadline_ms": state["deadline_ms"],
                    "epoch_id": state["epoch_id"],
                }
        except ReconciliationRequired:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError(str(exc)) from exc

    def finish_observation_probe(
        self,
        scope: str,
        *,
        consecutive_idle: int,
        cursor: Any,
        payload: dict[str, Any],
        next_probe_ms: int,
    ) -> None:
        self._check("observation")
        try:
            with self.connect() as database:
                database.execute("BEGIN IMMEDIATE")
                updated = database.execute(
                    "UPDATE observation_state SET consecutive_idle=?,cursor_payload=?,"
                    "in_flight=0,next_probe_ms=?,updated_at=? WHERE scope=? AND in_flight=1",
                    (consecutive_idle, _json(cursor), next_probe_ms, _now(), scope),
                )
                if updated.rowcount != 1:
                    raise sqlite3.IntegrityError("observation probe lease is not held")
                database.execute(
                    "INSERT INTO observation_events(scope,payload,recorded_at) VALUES(?,?,?)",
                    (scope, _json(payload), _now()),
                )
                database.execute("COMMIT")
        except sqlite3.Error as exc:
            raise PersistenceError(str(exc)) from exc

    def get_observation_state(self, scope: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM observation_state WHERE scope=?", (scope,))
        if row and row.get("cursor_payload") is not None:
            row["cursor"] = json.loads(row["cursor_payload"])
        return row

    def continue_observation(
        self,
        scope: str,
        *,
        authorization_id: str,
        new_deadline_ms: int,
        now_ms: int,
    ) -> None:
        self._check("observation")
        try:
            with self.connect() as database:
                database.execute("BEGIN IMMEDIATE")
                row = database.execute(
                    "SELECT deadline_ms,in_flight FROM observation_state WHERE scope=?",
                    (scope,),
                ).fetchone()
                if (
                    row is None
                    or row["deadline_ms"] > now_ms
                    or type(new_deadline_ms) is not int
                    or new_deadline_ms <= now_ms
                ):
                    raise sqlite3.IntegrityError(
                        "expired observation and new future deadline are required"
                    )
                database.execute(
                    "UPDATE observation_state SET consecutive_idle=0,in_flight=0,"
                    "started_ms=?,deadline_ms=?,next_probe_ms=?,updated_at=? WHERE scope=?",
                    (now_ms, new_deadline_ms, now_ms, _now(), scope),
                )
                database.execute(
                    "INSERT INTO observation_events(scope,payload,recorded_at) VALUES(?,?,?)",
                    (
                        scope,
                        _json(
                            {
                                "event": "owner_continuation",
                                "authorization_id": authorization_id,
                                "new_deadline_ms": new_deadline_ms,
                                "continued_at_mono_ms": now_ms,
                            }
                        ),
                        _now(),
                    ),
                )
                database.execute("COMMIT")
        except sqlite3.Error as exc:
            raise PersistenceError(str(exc)) from exc

    def events(self, table: str) -> list[dict[str, Any]]:
        allowed = set(IMMUTABLE_TABLES) | {"reconciliation_state"}
        if table not in allowed:
            raise ValueError("unknown event table")
        self._check("read")
        with self.connect() as database:
            return [
                dict(row)
                for row in database.execute(f"SELECT * FROM {table} ORDER BY rowid")
            ]

    def status(self) -> dict[str, Any]:
        with self.connect() as database:
            return {
                "decisions": database.execute(
                    "SELECT COUNT(*) FROM decision_events"
                ).fetchone()[0],
                "approved_invocations": database.execute(
                    "SELECT COUNT(*) FROM approved_invocations"
                ).fetchone()[0],
                "open_reconciliations": database.execute(
                    "SELECT COUNT(*) FROM reconciliation_state WHERE required=1"
                ).fetchone()[0],
            }
