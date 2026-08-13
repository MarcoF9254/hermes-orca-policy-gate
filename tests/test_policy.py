import json
from pathlib import Path
import pytest
from hermes_orca_gate.errors import PolicyLoadError
from hermes_orca_gate.policy import Policy


def policy_data():
    commands = ['orchestration.dispatch', 'orchestration.workerStart', 'orchestration.workerStop', 'orchestration.workerAbandon', 'orchestration.workerShow']
    return {
        'version': 1, 'default': {'action': 'block'},
        'source_contract_sha': '403c60bbadb734d870a22c372aca8a988e348d21',
        'freshness_max_age_ms': {command: 30000 for command in commands},
        'observation': {'interval_ms': 60000, 'qualifying_checkpoints': 3, 'probe_timeout_ms': 60000, 'default_ceiling_ms': 2700000},
        'rules': [{'id': 'mutation', 'match': {'normalized_command': 'orchestration.dispatch', 'state_class': 'OK', 'target_instance_identity': 'local-instance', 'repo_id': 'Repo-A', 'context_digest': 'ctx', 'approved_action': 'mutate'}, 'action': 'require_approval'}],
    }


def test_policy_load_match_default_and_reconciliation_precedence():
    policy = Policy.from_bytes(json.dumps(policy_data()).encode())
    facts = {'normalized_command': 'orchestration.dispatch', 'state_class': 'OK', 'target_instance_identity': 'local-instance', 'repo_id': 'Repo-A', 'context_digest': 'ctx', 'approved_action': 'mutate'}
    assert policy.decide(facts) == 'require_approval'
    assert policy.decide({'normalized_command': 'unknown'}) == 'block'
    assert policy.decide(facts, reconciliation_required=True) == 'reconciliation_required'


@pytest.mark.parametrize('state', ['STARTING', 'STOPPING', 'OBSERVATION_PENDING'])
def test_in_progress_states_route_only_to_observation(state):
    loaded = Policy.from_bytes(json.dumps(policy_data()).encode())
    assert loaded.decide(
        {'normalized_command': 'orchestration.dispatch', 'state_class': state}
    ) == 'observe'


@pytest.mark.parametrize('mutation', [
    lambda p: p['default'].update(action='allow'), lambda p: p.update(version=2),
    lambda p: p.update(version=True),
    lambda p: p.update(extra=True), lambda p: p['freshness_max_age_ms'].update({'orchestration.workerShow': 0}),
    lambda p: p['freshness_max_age_ms'].update({'orchestration.workerShow': 60001}),
    lambda p: p['observation'].update(interval_ms=0), lambda p: p['rules'][0].update(action='permit'),
    lambda p: p['observation'].update(default_ceiling_ms=2700001),
    lambda p: p['rules'][0]['match'].update(operator='wildcard'),
])
def test_policy_load_errors_are_fail_closed(mutation):
    data = policy_data(); mutation(data)
    with pytest.raises(PolicyLoadError) as caught:
        Policy.from_bytes(json.dumps(data).encode())
    assert caught.value.code == 'POLICY_LOAD_ERROR'


def test_duplicate_rule_ids_and_missing_freshness_fail():
    data = policy_data(); data['rules'].append(data['rules'][0].copy())
    with pytest.raises(PolicyLoadError): Policy.from_bytes(json.dumps(data).encode())


def test_example_policy_defaults_block_and_never_auto_allows_mutation():
    policy = Policy.from_path("config/policy.example.json")
    assert policy.data["default"] == {"action": "block"}
    assert policy.decide({"normalized_command": "orchestration.dispatchDryRun", "state_class": "OK"}) == "allow"
    for command in (
        "orchestration.dispatch",
        "orchestration.workerStart",
        "orchestration.workerStop",
        "orchestration.workerAbandon",
        "orchestration.workerShow",
    ):
        assert policy.decide({"normalized_command": command, "state_class": "OK"}) == "require_approval"


def test_policy_schema_and_fixtures_match_closed_runtime_contract():
    schema = json.loads(Path("schemas/policy.schema.json").read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "version",
        "default",
        "source_contract_sha",
        "freshness_max_age_ms",
        "observation",
        "rules",
    }
    assert Policy.from_path("tests/fixtures/policy.valid.json").data["version"] == 1
    with pytest.raises(PolicyLoadError):
        Policy.from_path("tests/fixtures/policy.invalid.json")


def test_source_identity_and_duplicate_json_fields_are_strictly_rejected():
    data = policy_data()
    data["source_contract_sha"] = "z" * 40
    with pytest.raises(PolicyLoadError, match="source contract"):
        Policy.from_bytes(json.dumps(data).encode())
    duplicate = b'{"version":1,"version":1}'
    with pytest.raises(PolicyLoadError, match="duplicate JSON field"):
        Policy.from_bytes(duplicate)
    data = policy_data(); del data['freshness_max_age_ms']['orchestration.workerStop']
    with pytest.raises(PolicyLoadError): Policy.from_bytes(json.dumps(data).encode())
