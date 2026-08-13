"""Stable public error taxonomy and CLI exit status mapping."""

from __future__ import annotations

from typing import Any


class GateError(Exception):
    code = "GATE_ERROR"
    exit_code = 1

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class InputError(GateError):
    code = "INVALID_INPUT"
    exit_code = 2


class PolicyLoadError(GateError):
    code = "POLICY_LOAD_ERROR"
    exit_code = 2


class DeniedError(GateError):
    code = "DENIED"


class PersistenceError(GateError):
    code = "PERSISTENCE_ERROR"


class ReconciliationRequired(GateError):
    code = "RECONCILIATION_REQUIRED"
    exit_code = 3


class OutcomeSchemaError(ReconciliationRequired):
    code = "OUTCOME_SCHEMA_MISMATCH"


class AuditabilityLost(GateError):
    code = "AUDITABILITY_LOST"
