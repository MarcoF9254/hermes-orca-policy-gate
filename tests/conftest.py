import pytest


@pytest.fixture
def store(tmp_path):
    from hermes_orca_gate.stores import GateStore

    value = GateStore(tmp_path / 'gate.db')
    value.initialize()
    return value


@pytest.fixture
def invocation_dict():
    return {'operation_id': 'Op-A', 'approval_kind': 'policy_allow', 'decision_event_id': 'Dec-A', 'normalized_command': 'orchestration.dispatch', 'args_digest': 'args', 'policy_sha256': 'policy', 'source_contract_sha': '403c60bbadb734d870a22c372aca8a988e348d21', 'evidence_digest': 'evidence', 'evidence_observed_mono_ms': 1000, 'evidence_process_epoch_id': 'epoch', 'cli_executable_identity': 'orca@1', 'credential_binding_id': 'cred-v1', 'owner_authorization_id': None, 'approval_resolution_event_id': None, 'mutation_intent_id': 'Intent-A', 'orca_request_id': 'Req-A', 'reconciliation_scope': None, 'decision': 'allow', 'issued_at': '2026-08-10T00:00:00Z'}
