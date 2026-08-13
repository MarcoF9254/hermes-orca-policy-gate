from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_orca_gate.errors import PersistenceError, ReconciliationRequired
from hermes_orca_gate.models import ApprovedInvocation


def _invocation(operation_id="Op-A"):
    return ApprovedInvocation(
        operation_id=operation_id,
        approval_kind="policy_allow",
        decision_event_id="Dec-A",
        normalized_command="orchestration.dispatch",
        args_digest="args",
        policy_sha256="policy",
        source_contract_sha="403c60bbadb734d870a22c372aca8a988e348d21",
        evidence_digest="evidence",
        evidence_observed_mono_ms=1000,
        evidence_process_epoch_id="epoch",
        cli_executable_identity="orca@1",
        credential_binding_id="cred-v1",
        mutation_intent_id="Intent-A",
        orca_request_id="Req-A",
        affected_scopes=("intent:Intent-A", "task:Run-A:Task-A"),
    )


def test_sqlite_schema_separates_immutable_records_and_uses_durable_pragmas(store):
    pragmas = store.pragmas()
    assert pragmas["journal_mode"] == "wal"
    assert pragmas["synchronous"] == 2
    tables = store.table_names()
    assert {
        "decision_events",
        "owner_authorizations",
        "resolution_decisions",
        "approved_invocations",
        "consumption_events",
        "exec_attempts",
        "outcomes",
        "recovery_records",
        "provenance_records",
        "reconciliation_events",
        "mutation_intents",
    } <= tables


def test_owner_authorization_and_basis_decision_are_append_only(store):
    store.append_decision("Dec-A", {"action": "require_approval"})
    store.append_owner_authorization(
        "Auth-A", "Dec-A", {"authorization_kind": "owner_approval"}
    )
    with pytest.raises(PersistenceError):
        store.append_decision("Dec-A", {"action": "allow"})
    with pytest.raises(PersistenceError):
        store.append_owner_authorization(
            "Auth-A", "Dec-A", {"authorization_kind": "owner_approval"}
        )
    assert store.get_decision("Dec-A")["action"] == "require_approval"


def test_atomic_single_use_consumption_has_exactly_one_winner(store):
    store.append_decision("Dec-A", {"action": "allow"})
    store.append_approved_invocation(_invocation())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.consume("Op-A"), range(16)))
    assert results.count(True) == 1
    assert results.count(False) == 15


def test_startup_sweep_marks_consumed_without_terminal_record(store):
    store.append_decision("Dec-A", {"action": "allow"})
    store.append_approved_invocation(_invocation())
    assert store.consume("Op-A")
    scopes = store.startup_sweep()
    assert scopes == ["operation:Op-A"]
    assert store.reconciliation_required("operation:Op-A")
    assert store.reconciliation_required("intent:Intent-A")
    assert store.reconciliation_required("task:Run-A:Task-A")


def test_outcome_and_new_provenance_commit_atomically(store):
    store.inject_failure("provenance")
    with pytest.raises(PersistenceError):
        store.append_outcome_with_provenance(
            "Op-A",
            {"mechanical_success": True},
            "Dispatch-A",
            {"origin": "local", "created_by_operation_id": "Op-A"},
        )
    store.clear_failure("provenance")
    assert store.events("outcomes") == []
    assert store.get_provenance("Dispatch-A") is None


def test_startup_sweep_recovers_abandoned_observation_lease(store):
    assert store.begin_observation_probe(
        "dispatch:Dispatch-A",
        started_ms=0,
        deadline_ms=100,
        epoch_id="epoch-A",
        now_ms=1,
    ) is not None
    store.startup_sweep()
    assert store.reconciliation_required("dispatch:Dispatch-A")
    with pytest.raises(ReconciliationRequired, match="reconciliation"):
        store.begin_observation_probe(
            "dispatch:Dispatch-A",
            started_ms=0,
            deadline_ms=100,
            epoch_id="epoch-A",
            now_ms=2,
        )
    event = __import__("json").loads(store.events("observation_events")[-1]["payload"])
    assert event["route"] == "RUNTIME_UNKNOWN"
    assert event["reason"] == "startup_recovered_probe_lease"


def test_startup_sweep_propagates_reconciliation_to_known_intent_dispatch_and_task(store):
    store.append_decision("Dec-A", {"action": "allow"})
    store.append_approved_invocation(_invocation())
    store.record_provenance(
        "Dispatch-A",
        {
            "origin": "local",
            "created_by_operation_id": "Op-A",
            "mutation_intent_id": "Intent-A",
            "run_id": "Run-A",
            "task_id": "Task-A",
        },
    )
    assert store.consume("Op-A")
    assert store.startup_sweep() == ["operation:Op-A"]
    for scope in (
        "operation:Op-A",
        "intent:Intent-A",
        "dispatch:Dispatch-A",
        "task:Run-A:Task-A",
    ):
        assert store.reconciliation_required(scope)


def test_mutation_recovery_requires_exact_binding_and_credential(store):
    store.create_mutation_intent(
        intent_id="Intent-A",
        request_id="Req-A",
        method="orchestration.dispatch",
        payload_digest="payload",
        credential_binding_id="cred-v1",
        target_instance_identity="instance-A",
    )
    assert (
        store.recovery_request_id(
            "Intent-A",
            method="orchestration.dispatch",
            payload_digest="payload",
            credential_binding_id="cred-v1",
            target_instance_identity="instance-A",
        )
        == "Req-A"
    )
    for credential in ("cred-v2", None):
        with pytest.raises(ReconciliationRequired):
            store.recovery_request_id(
                "Intent-A",
                method="orchestration.dispatch",
                payload_digest="payload",
                credential_binding_id=credential,
                target_instance_identity="instance-A",
            )
    assert store.reconciliation_required("intent:Intent-A")


def test_provenance_is_gate_derived_case_sensitive_and_fail_closed(store):
    store.record_provenance(
        "Dispatch-A",
        {
            "origin": "local",
            "created_by_operation_id": "Op-A",
            "terminal_handle": "Term-A",
        },
    )
    assert store.require_local_provenance("Dispatch-A")["terminal_handle"] == "Term-A"
    with pytest.raises(ReconciliationRequired):
        store.require_local_provenance("dispatch-a")
    assert store.reconciliation_required("dispatch:dispatch-a")


def test_reconciliation_persists_until_explicit_durable_closure(store):
    store.mark_reconciliation("dispatch:D", "unknown_outcome")
    assert store.reconciliation_required("dispatch:D")
    store.close_reconciliation("dispatch:D", "Auth-Reconcile", {"evidence": "receipt"})
    assert not store.reconciliation_required("dispatch:D")
