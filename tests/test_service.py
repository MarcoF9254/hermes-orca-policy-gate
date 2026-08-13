import json

import pytest
from hermes_orca_gate.errors import (
    AuditabilityLost,
    DeniedError,
    InputError,
    PersistenceError,
    ReconciliationRequired,
)
from hermes_orca_gate.models import TypedRequest
from hermes_orca_gate.orca_cli import DiscoverySnapshot, OrcaAdapter
from hermes_orca_gate.policy import Policy
from hermes_orca_gate.service import (
    GateExecutor,
    GateService,
    ObservationController,
    ObservationSession,
    StaticIdentityResolver,
)


def context():
    return {'cli_executable_identity': 'C:/orca/orca.exe@1', 'source_contract_sha': '403c60bbadb734d870a22c372aca8a988e348d21', 'effective_target_identity': 'target', 'fixed_or_bound_cwd': 'C:/repo', 'execution_relevant_env': {'ORCA_USER_DATA_PATH': 'C:/orca-data', 'ORCA_DEV_CLI_INVOCATION': '0'}, 'target_instance_identity': 'local-instance', 'dev_mode': False, 'credential_binding_id': 'cred-v1'}


def dispatch_data():
    return {'command': 'orchestration.dispatch', 'args': {'run_id': 'R', 'task_id': 'T', 'to_handle': 'To', 'from_handle': 'From', 'inject': False, 'return_preamble': False, 'dry_run': False}, 'context': context()}


def test_closed_dispatch_request_and_case_preservation():
    data = dispatch_data(); data['args'].update(run_id='Run-A', task_id='Task-A', to_handle='Agent-X', from_handle='Hermes-X')
    request = TypedRequest.from_dict(data)
    assert request.args['run_id'] == 'Run-A'
    assert request.canonical_invocation()['structured_args']['to_handle'] == 'Agent-X'


@pytest.mark.parametrize('change', [lambda d: d.update(raw_argv=['orca']), lambda d: d['args'].update(on='remote'), lambda d: d['args'].update(unknown=True), lambda d: d['context'].update(env_override={'X': 'Y'}), lambda d: d['args'].pop('run_id'), lambda d: d['args'].pop('from_handle')])
def test_closed_request_rejects_unapproved_authority(change):
    data = dispatch_data(); change(data)
    with pytest.raises(InputError): TypedRequest.from_dict(data)


@pytest.mark.parametrize("field", ["run_id", "task_id", "to_handle", "from_handle"])
def test_required_string_arguments_reject_explicit_null(field):
    data = dispatch_data()
    data["args"][field] = None
    with pytest.raises(InputError, match=field):
        TypedRequest.from_dict(data)


def test_dry_run_is_separate_schema_and_omitted_is_distinct():
    data = {'command': 'orchestration.dispatchDryRun', 'args': {'run_id': 'R', 'task_id': 'T', 'from_handle': 'From', 'dry_run': True, 'return_preamble': False}, 'context': context()}
    request = TypedRequest.from_dict(data)
    explicit = {**data, 'args': {**data['args'], 'to_handle': None}}
    assert 'to_handle' not in request.args
    assert request.canonical_invocation() != TypedRequest.from_dict(explicit).canonical_invocation()


def test_context_and_mutation_identity_are_closed_and_dev_mode_is_derived():
    data = dispatch_data()
    data["context"]["mutation_intent_identity_or_null"] = {"mutation_intent_id": "I"}
    with pytest.raises(InputError, match="mutation intent"):
        TypedRequest.from_dict(data)
    data = dispatch_data()
    data["context"]["execution_relevant_env"]["ORCA_DEV_CLI_INVOCATION"] = "1"
    with pytest.raises(InputError, match="dev_mode"):
        TypedRequest.from_dict(data)


@pytest.mark.parametrize("field", ["repo_id", "base_branch", "name", "parent_worktree_id"])
def test_new_child_topology_identifiers_are_nonempty_strings(field):
    args = {
        "run_id": "R",
        "task_id": "T",
        "from_handle": "F",
        "worktree_mode": "new-child",
        "setup": "inherit",
        "parent_worktree_id": "Parent",
        "repo_id": "Repo",
        "base_branch": "Main",
        "name": "Worker",
    }
    args[field] = ""
    with pytest.raises(InputError, match=field):
        TypedRequest.from_dict(
            {"command": "orchestration.workerStart", "args": args, "context": context()}
        )


@pytest.mark.parametrize("field", ["parent_worktree_id", "repo_id", "base_branch", "name"])
def test_new_child_topology_identifiers_reject_explicit_null(field):
    args = {
        "run_id": "R",
        "task_id": "T",
        "from_handle": "F",
        "worktree_mode": "new-child",
        "setup": "inherit",
        "parent_worktree_id": "Parent",
        "repo_id": "Repo",
        "base_branch": "Main",
        "name": "Worker",
    }
    args[field] = None
    with pytest.raises(InputError, match=field):
        TypedRequest.from_dict(
            {"command": "orchestration.workerStart", "args": args, "context": context()}
        )


@pytest.mark.parametrize(
    ("mode", "extra"),
    [
        ("Worktree-A", {"parent_worktree_id": "Parent"}),
        ("Worktree-A", {"repo_id": "Repo"}),
        ("Worktree-A", {"base_branch": "Main"}),
        ("Worktree-A", {"name": "Worker"}),
        ("new-child", {"resolved_worktree_id": "Unexpected"}),
        ("new-top-level", {"resolved_worktree_id": "Unexpected"}),
        ("new-child", {"terminal_handle": "Existing-Terminal"}),
    ],
)
def test_worker_start_rejects_mode_conflicting_topology_fields(mode, extra):
    args = {
        "run_id": "R",
        "task_id": "T",
        "from_handle": "F",
        "worktree_mode": mode,
        "setup": "inherit",
    }
    if mode == "Worktree-A":
        args["resolved_worktree_id"] = "Worktree-A"
    else:
        args.update(repo_id="Repo", base_branch="Main", name="Worker")
        if mode == "new-child":
            args["parent_worktree_id"] = "Parent"
    args.update(extra)
    with pytest.raises(InputError, match="forbids|conflict"):
        TypedRequest.from_dict(
            {"command": "orchestration.workerStart", "args": args, "context": context()}
        )


def test_existing_worktree_requires_terminal_for_deterministic_relationship_check():
    with pytest.raises(InputError, match="terminal_handle"):
        TypedRequest.from_dict(
            {
                "command": "orchestration.workerStart",
                "args": {
                    "run_id": "R",
                    "task_id": "T",
                    "from_handle": "F",
                    "worktree_mode": "Worktree-A",
                    "resolved_worktree_id": "Worktree-A",
                    "setup": "inherit",
                },
                "context": context(),
            }
        )


def test_observation_probe_taxonomy_serial_and_ceiling():
    session = ObservationSession(started_ms=0, deadline_ms=2700000, qualifying_checkpoints=3)
    assert session.begin_probe() is True and session.begin_probe() is False
    assert session.finish_probe({'satisfied': True}, new_output=False, now_ms=1) == 'observe'
    session.begin_probe(); assert session.finish_probe({'error': {'code': 'timeout'}}, new_output=False, now_ms=2) == 'observe'
    assert session.consecutive_idle == 0
    session.begin_probe(); assert session.finish_probe({'satisfied': False, 'blockedReason': 'permission'}, new_output=False, now_ms=3) == 'require_approval'
    session.begin_probe(); assert session.finish_probe({'error': {'code': 'other'}}, new_output=False, now_ms=4) == 'RUNTIME_UNKNOWN'
    session.begin_probe(); assert session.finish_probe({'satisfied': True}, new_output=False, now_ms=2700000) == 'require_approval'


def test_observation_three_idle_and_new_owner_deadline():
    session = ObservationSession(started_ms=0, deadline_ms=100, qualifying_checkpoints=3)
    for now in (1, 2):
        session.begin_probe(); assert session.finish_probe({'satisfied': True}, new_output=False, now_ms=now) == 'observe'
    session.begin_probe(); assert session.finish_probe({'satisfied': True}, new_output=False, now_ms=3) == 'require_approval'
    with pytest.raises(InputError): session.continue_after_ceiling(None, 101)
    session.continue_after_ceiling(200, 101)
    assert session.deadline_ms == 200 and session.consecutive_idle == 0


def test_observation_controller_executes_provenance_bound_serial_probe(store):
    store.record_provenance(
        "Dispatch-A",
        {
            "origin": "local",
            "created_by_operation_id": "Op-A",
            "terminal_handle": "Terminal-A",
            "worker_started_mono_ms": 0,
            "evidence_process_epoch_id": "epoch-A",
            "target_instance_identity": context()["target_instance_identity"],
            "source_contract_sha": context()["source_contract_sha"],
        },
    )
    calls = []

    def runner(argv, *, cwd, env, shell):
        calls.append(argv)
        if argv[1:3] == ["terminal", "wait"]:
            return 0, '{"ok":true,"result":{"satisfied":true}}', ""
        return 0, '{"ok":true,"result":{"cursor":1,"output":""}}', ""

    controller = ObservationController(
        store,
        policy(),
        OrcaAdapter(runner),
        resolver=StaticIdentityResolver(context()),
        epoch_id="epoch-A",
        clock=lambda: 1,
    )
    result = controller.observe("Dispatch-A")
    assert result == {
        "route": "observe",
        "consecutive_idle": 1,
        "cursor": 1,
        "deadline_ms": 2_700_000,
    }
    assert [call[1:3] for call in calls] == [["terminal", "wait"], ["terminal", "read"]]
    wait = calls[0]
    assert wait[wait.index("--for") + 1] == "tui-idle"
    assert wait[wait.index("--timeout-ms") + 1] == "60000"
    assert wait.count("--json") == 1


def test_observation_counts_output_that_arrives_during_wait(store):
    store.record_provenance(
        "Dispatch-A",
        {
            "origin": "local",
            "created_by_operation_id": "Op-A",
            "terminal_handle": "Terminal-A",
            "worker_started_mono_ms": 0,
            "evidence_process_epoch_id": "epoch-A",
            "target_instance_identity": context()["target_instance_identity"],
            "source_contract_sha": context()["source_contract_sha"],
        },
    )
    calls = []

    def runner(argv, *, cwd, env, shell):
        calls.append(argv)
        result = (
            {"satisfied": True}
            if argv[1:3] == ["terminal", "wait"]
            else {"cursor": 2, "output": "arrived-during-wait"}
        )
        return 0, json.dumps({"ok": True, "result": result}), ""

    controller = ObservationController(
        store,
        policy(),
        OrcaAdapter(runner),
        resolver=StaticIdentityResolver(context()),
        epoch_id="epoch-A",
        clock=lambda: 1,
    )
    result = controller.observe("Dispatch-A")
    assert result["route"] == "observe"
    assert result["consecutive_idle"] == 0
    assert [call[1:3] for call in calls] == [["terminal", "wait"], ["terminal", "read"]]


def test_observation_controller_enforces_interval_between_completed_probes(store):
    store.record_provenance(
        "Dispatch-A",
        {
            "origin": "local",
            "created_by_operation_id": "Op-A",
            "terminal_handle": "Terminal-A",
            "worker_started_mono_ms": 0,
            "evidence_process_epoch_id": "epoch-A",
            "target_instance_identity": context()["target_instance_identity"],
            "source_contract_sha": context()["source_contract_sha"],
        },
    )
    calls = []

    def runner(argv, *, cwd, env, shell):
        calls.append(argv)
        result = (
            {"cursor": 1, "output": ""}
            if argv[1:3] == ["terminal", "read"]
            else {"satisfied": True}
        )
        return 0, json.dumps({"ok": True, "result": result}), ""

    controller = ObservationController(
        store,
        policy(),
        OrcaAdapter(runner),
        resolver=StaticIdentityResolver(context()),
        epoch_id="epoch-A",
        clock=lambda: 1,
    )
    assert controller.observe("Dispatch-A")["route"] == "observe"
    assert controller.observe("Dispatch-A")["route"] == "interval_pending"
    assert len(calls) == 2


def test_observation_deadline_is_rechecked_after_each_external_probe(store):
    store.record_provenance(
        "Dispatch-A",
        {
            "origin": "local",
            "created_by_operation_id": "Op-A",
            "terminal_handle": "Terminal-A",
            "worker_started_mono_ms": 0,
            "evidence_process_epoch_id": "epoch-A",
            "target_instance_identity": context()["target_instance_identity"],
            "source_contract_sha": context()["source_contract_sha"],
        },
    )
    calls = []
    times = iter((1, 2_700_001))

    def runner(argv, *, cwd, env, shell):
        calls.append(argv)
        return 0, '{"ok":true,"result":{"cursor":1,"output":""}}', ""

    controller = ObservationController(
        store,
        policy(),
        OrcaAdapter(runner),
        resolver=StaticIdentityResolver(context()),
        epoch_id="epoch-A",
        clock=lambda: next(times),
    )
    assert controller.observe("Dispatch-A")["route"] == "require_approval"
    assert len(calls) == 1


def test_near_ceiling_probe_keeps_canonical_wait_timeout(store):
    store.record_provenance(
        "Dispatch-A",
        {
            "origin": "local",
            "created_by_operation_id": "Op-A",
            "terminal_handle": "Terminal-A",
            "worker_started_mono_ms": 0,
            "evidence_process_epoch_id": "epoch-A",
            "target_instance_identity": context()["target_instance_identity"],
            "source_contract_sha": context()["source_contract_sha"],
        },
    )
    calls = []
    times = iter((2_699_000, 2_699_000, 2_700_001))

    def runner(argv, *, cwd, env, shell):
        calls.append(argv)
        result = (
            {"cursor": 1, "output": ""}
            if argv[1:3] == ["terminal", "read"]
            else {"satisfied": True}
        )
        return 0, json.dumps({"ok": True, "result": result}), ""

    controller = ObservationController(
        store,
        policy(),
        OrcaAdapter(runner),
        resolver=StaticIdentityResolver(context()),
        epoch_id="epoch-A",
        clock=lambda: next(times),
    )
    result = controller.observe("Dispatch-A")
    assert result["route"] == "require_approval"
    wait = next(call for call in calls if call[1:3] == ["terminal", "wait"])
    assert wait[wait.index("--timeout-ms") + 1] == "60000"


def test_observation_store_lease_prevents_overlapping_probe(store):
    first = store.begin_observation_probe(
        "dispatch:Dispatch-A", started_ms=0, deadline_ms=100, epoch_id="epoch-A"
    )
    assert first is not None
    assert (
        store.begin_observation_probe(
            "dispatch:Dispatch-A", started_ms=0, deadline_ms=100, epoch_id="epoch-A"
        )
        is None
    )


def test_expired_ceiling_preempts_stale_in_flight_probe_lease(store):
    assert store.begin_observation_probe(
        "dispatch:Dispatch-A",
        started_ms=0,
        deadline_ms=100,
        epoch_id="epoch-A",
        now_ms=1,
    ) is not None
    state = store.begin_observation_probe(
        "dispatch:Dispatch-A",
        started_ms=0,
        deadline_ms=100,
        epoch_id="epoch-A",
        now_ms=100,
    )
    assert state is not None
    assert state["deadline_expired"] is True


def test_ceiling_route_creates_owner_authorizable_continuation_decision(store):
    store.record_provenance(
        "Dispatch-A",
        {
            "origin": "local",
            "created_by_operation_id": "Op-A",
            "terminal_handle": "Terminal-A",
            "worker_started_mono_ms": 0,
            "evidence_process_epoch_id": "epoch-A",
            "target_instance_identity": context()["target_instance_identity"],
            "source_contract_sha": context()["source_contract_sha"],
        },
    )
    controller = ObservationController(
        store,
        policy(),
        OrcaAdapter(lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError())),
        resolver=StaticIdentityResolver(context()),
        epoch_id="epoch-A",
        clock=lambda: 2_700_000,
    )
    result = controller.observe("Dispatch-A")
    decision = store.get_decision(result["decision_event_id"])
    assert result["route"] == "require_approval"
    assert decision["normalized_command"] == "observation.continue"
    assert decision["observation_scope"] == "dispatch:Dispatch-A"
    assert decision["expired_deadline_ms"] == 2_700_000


def test_observation_continuation_uses_owner_bound_future_deadline(store):
    store.begin_observation_probe(
        "dispatch:Dispatch-A",
        started_ms=0,
        deadline_ms=100,
        epoch_id="epoch-A",
        now_ms=1,
    )
    store.append_owner_authorization(
        "Auth-A",
        "Decision-A",
        {
            "authorization_kind": "owner_approval",
            "exact_action": "observation.continue",
            "observation_scope": "dispatch:Dispatch-A",
            "observation_deadline_ms": 200,
        },
    )
    controller = ObservationController(
        store,
        policy(),
        OrcaAdapter(),
        resolver=StaticIdentityResolver(context()),
        epoch_id="epoch-A",
        clock=lambda: 100,
    )
    controller.continue_observation("Dispatch-A", "Auth-A")
    state = store.get_observation_state("dispatch:Dispatch-A")
    assert state["deadline_ms"] == 200
    event = json.loads(store.events("observation_events")[-1]["payload"])
    assert event["authorization_id"] == "Auth-A"


def test_observation_run_schedules_until_non_observe_route(store):
    controller = ObservationController(
        store,
        policy(),
        OrcaAdapter(),
        resolver=StaticIdentityResolver(context()),
        epoch_id="epoch-A",
        clock=lambda: 0,
    )
    routes = iter((
        {"route": "observe", "deadline_ms": 10_000},
        {"route": "interval_pending", "next_probe_ms": 120_000, "deadline_ms": 30_000},
        {"route": "require_approval"},
    ))
    controller.observe = lambda dispatch_id: next(routes)
    sleeps = []
    result = controller.run("Dispatch-A", sleeper=lambda seconds: sleeps.append(seconds))
    assert result["route"] == "require_approval"
    assert sleeps == [10.0, 30.0]


def policy(action="require_approval"):
    freshness = {
        command: 30000
        for command in (
            "orchestration.dispatch",
            "orchestration.workerStart",
            "orchestration.workerStop",
            "orchestration.workerAbandon",
            "orchestration.workerShow",
        )
    }
    data = {
        "version": 1,
        "default": {"action": "block"},
        "source_contract_sha": "403c60bbadb734d870a22c372aca8a988e348d21",
        "freshness_max_age_ms": freshness,
        "observation": {
            "interval_ms": 60000,
            "qualifying_checkpoints": 3,
            "probe_timeout_ms": 60000,
            "default_ceiling_ms": 2700000,
        },
        "rules": [
            {
                "id": "dispatch",
                "match": {
                    "normalized_command": "orchestration.dispatch",
                    "state_class": "OK",
                },
                "action": action,
            }
        ],
    }
    return Policy.from_bytes(json.dumps(data).encode())


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.result = (
            0,
            '{"ok":true,"result":{"dispatchId":"Dispatch-A","state":"dispatched"}}',
            "",
        )

    def __call__(self, argv, *, cwd, env, shell):
        self.calls.append((argv, cwd, env, shell))
        return self.result


def _service(store, action="require_approval", runner=None):
    runner = runner or FakeRunner()
    resolver = StaticIdentityResolver(context())
    service = GateService(
        store,
        policy(action),
        resolver=resolver,
        epoch_id="epoch-A",
        _test_overrides=True,
    )
    executor = GateExecutor(
        store,
        policy(action),
        OrcaAdapter(runner),
        resolver=resolver,
        epoch_id="epoch-A",
    )
    return service, executor, runner


def test_production_service_rejects_caller_supplied_evidence_and_record_ids(store):
    service = GateService(
        store,
        policy("allow"),
        resolver=StaticIdentityResolver(context()),
        epoch_id="epoch-A",
    )
    with pytest.raises(DeniedError, match="adapter-owned"):
        service.preflight(
            TypedRequest.from_dict(dispatch_data()),
            facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
            evidence_digest="caller",
            observed_mono_ms=1,
            decision_event_id="Caller-Dec",
            mutation_intent_id="Caller-Intent",
            orca_request_id="Caller-Request",
            operation_id="Caller-Operation",
        )


def test_owner_resolution_appends_linked_decision_without_mutating_basis(store):
    service, _, _ = _service(store)
    pending = service.preflight(
        TypedRequest.from_dict(dispatch_data()),
        facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
        evidence_digest="Evidence-A",
        observed_mono_ms=1000,
        decision_event_id="Dec-A",
        mutation_intent_id="Intent-A",
        orca_request_id="Req-A",
    )
    assert pending["decision"] == "require_approval" and pending["invocation"] is None
    service.owner_authorize("Dec-A", "Auth-A", authorization_kind="owner_approval")
    resolved = service.resolve_approval(
        "Auth-A",
        pending["request"],
        facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
        evidence_digest="Evidence-B",
        observed_mono_ms=1100,
        resolution_event_id="Resolution-A",
        operation_id="Op-A",
    )
    assert resolved.operation_id == "Op-A"
    assert resolved.owner_authorization_id == "Auth-A"
    assert store.get_decision("Dec-A")["action"] == "require_approval"
    assert store.get_resolution_decision("Resolution-A")["action"] == "allow"


def test_owner_resolution_rejects_policy_drift_from_basis_decision(store):
    service, _, _ = _service(store)
    pending = service.preflight(
        TypedRequest.from_dict(dispatch_data()),
        facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
        evidence_digest="Evidence-A",
        observed_mono_ms=1000,
        decision_event_id="Dec-A",
        mutation_intent_id="Intent-A",
        orca_request_id="Req-A",
    )
    service.owner_authorize("Dec-A", "Auth-A", authorization_kind="owner_approval")
    drifted = json.loads(json.dumps(service.policy.data))
    drifted["rules"][0]["id"] = "different-policy-bytes"
    service.policy = Policy.from_bytes(json.dumps(drifted).encode())
    with pytest.raises(DeniedError, match="policy/source contract"):
        service.resolve_approval(
            "Auth-A",
            pending["request"],
            facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
            evidence_digest="Evidence-B",
            observed_mono_ms=1001,
        )


def test_owner_reconciliation_binds_exact_recovery_or_manual_action(store):
    service, _, _ = _service(store)
    store.mark_reconciliation("operation:Op-A", "unknown")
    store.append_decision(
        "Recon-A",
        {
            "action": "reconciliation_required",
            "normalized_command": "orchestration.dispatch",
            "args_digest": "digest",
            "state_class": "RUNTIME_UNKNOWN",
            "mutation_intent_id": "Intent-A",
            "reconciliation_scope": "operation:Op-A",
        },
    )
    with pytest.raises(InputError, match="reconciliation action"):
        service.owner_authorize(
            "Recon-A",
            "Auth-Missing",
            authorization_kind="owner_reconciliation",
            reconciliation_scope="operation:Op-A",
        )
    authorization = service.owner_authorize(
        "Recon-A",
        "Auth-A",
        authorization_kind="owner_reconciliation",
        reconciliation_scope="operation:Op-A",
        reconciliation_action="manual_break_glass",
    )
    assert authorization["reconciliation_action"] == "manual_break_glass"


def test_preflight_and_resolution_can_source_fresh_facts_only_from_discovery(store):
    snapshot = DiscoverySnapshot(
        facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
        evidence={"source": "fake-adapter"},
        observed_mono_ms=1000,
    )

    class Discovery:
        def discover(self, request):
            assert request.command == "orchestration.dispatch"
            return snapshot

    resolver = StaticIdentityResolver(context())
    service = GateService(
        store,
        policy("allow"),
        resolver=resolver,
        epoch_id="epoch-A",
        discovery=Discovery(),
        _test_overrides=True,
    )
    result = service.preflight(
        TypedRequest.from_dict(dispatch_data()),
        decision_event_id="Dec-A",
        mutation_intent_id="Intent-A",
        orca_request_id="Req-A",
        operation_id="Op-A",
    )
    assert result["decision"] == "allow"
    assert store.get_decision("Dec-A")["evidence_digest"] == snapshot.evidence_digest
    assert result["discovery"] == snapshot.evidence
    observation = store.events("observation_events")[0]
    assert json.loads(observation["payload"])["evidence"] == snapshot.evidence


def test_read_execution_occurs_only_after_allow_decision_is_durable(store):
    data = json.loads(json.dumps(policy().data))
    data["rules"] = [{
        "id": "read",
        "match": {"normalized_command": "orchestration.runShow", "state_class": "OK"},
        "action": "allow",
    }]
    read_policy = Policy.from_bytes(json.dumps(data).encode())
    executed = []

    class Discovery:
        def discover(self, request):
            return DiscoverySnapshot(
                facts={"normalized_command": request.command, "state_class": "OK"},
                evidence={"phase": "preconditions"},
                observed_mono_ms=1000,
            )

        def execute_admitted(self, request):
            decisions = store.events("decision_events")
            assert len(decisions) == 1
            executed.append(request.command)
            return {"exit_code": 0, "result": {"runId": "R"}, "envelope": {"ok": True, "result": {"runId": "R"}}}

    service = GateService(
        store,
        read_policy,
        resolver=StaticIdentityResolver(context()),
        epoch_id="epoch-A",
        discovery=Discovery(),
    )
    request = TypedRequest.from_dict(
        {"command": "orchestration.runShow", "args": {"run_id": "R"}, "context": context()}
    )
    result = service.preflight(request)
    assert executed == ["orchestration.runShow"]
    assert result["discovery"]["result"] == {"runId": "R"}


def test_read_or_preview_never_executes_if_decision_persistence_fails(store):
    data = json.loads(json.dumps(policy().data))
    data["rules"] = [{
        "id": "read",
        "match": {"normalized_command": "orchestration.runShow", "state_class": "OK"},
        "action": "allow",
    }]
    read_policy = Policy.from_bytes(json.dumps(data).encode())
    executed = []

    class Discovery:
        def discover(self, request):
            return DiscoverySnapshot(
                facts={"normalized_command": request.command, "state_class": "OK"},
                evidence={"phase": "preconditions"},
                observed_mono_ms=1000,
            )

        def execute_admitted(self, request):
            executed.append(request.command)
            return {}

    service = GateService(
        store,
        read_policy,
        resolver=StaticIdentityResolver(context()),
        epoch_id="epoch-A",
        discovery=Discovery(),
    )
    store.inject_failure("decision")
    with pytest.raises(PersistenceError):
        service.preflight(
            TypedRequest.from_dict(
                {"command": "orchestration.runShow", "args": {"run_id": "R"}, "context": context()}
            )
        )
    assert executed == []


def test_policy_allow_executes_once_after_exact_recomputation_and_records_provenance(store):
    service, executor, runner = _service(store, action="allow")
    approved = service.preflight(
        TypedRequest.from_dict(dispatch_data()),
        facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
        evidence_digest="Evidence-A",
        observed_mono_ms=1000,
        decision_event_id="Dec-A",
        mutation_intent_id="Intent-A",
        orca_request_id="Req-A",
        operation_id="Op-A",
    )
    result = executor.execute("Op-A", approved["request"], now_mono_ms=1001)
    assert result["result"]["dispatchId"] == "Dispatch-A"
    assert store.require_local_provenance("Dispatch-A")["origin"] == "local"
    with pytest.raises(DeniedError, match="consumed"):
        executor.execute("Op-A", approved["request"], now_mono_ms=1002)
    assert len(runner.calls) == 1


def test_same_intent_preflight_reuses_receipt_identity_but_new_operation(store):
    service, _, _ = _service(store, action="allow")
    first = service.preflight(
        TypedRequest.from_dict(dispatch_data()),
        facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
        evidence_digest="Evidence-A",
        observed_mono_ms=1000,
        decision_event_id="Dec-A",
        mutation_intent_id="Intent-A",
        orca_request_id="Req-A",
        operation_id="Op-A",
    )
    recovered = service.preflight(
        TypedRequest.from_dict(dispatch_data()),
        facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
        evidence_digest="Evidence-B",
        observed_mono_ms=1001,
        decision_event_id="Dec-B",
        recovery_intent_id="Intent-A",
        operation_id="Op-B",
    )
    identity = recovered["request"].context["mutation_intent_identity_or_null"]
    assert identity["mutation_intent_id"] == "Intent-A"
    assert identity["orca_request_id"] == "Req-A"
    assert recovered["invocation"].operation_id == "Op-B"
    assert first["invocation"].operation_id == "Op-A"

    changed = dispatch_data()
    changed["args"]["task_id"] = "Other-Task"
    with pytest.raises(DeniedError, match="receipt method/payload/target"):
        service.preflight(
            TypedRequest.from_dict(changed),
            facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
            evidence_digest="Evidence-C",
            observed_mono_ms=1002,
            recovery_intent_id="Intent-A",
        )


def test_completed_receipt_replay_is_recorded_without_new_provenance_effect(store):
    service, executor, runner = _service(store, action="allow")
    first = service.preflight(
        TypedRequest.from_dict(dispatch_data()),
        facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
        evidence_digest="Evidence-A",
        observed_mono_ms=1000,
        decision_event_id="Dec-A",
        mutation_intent_id="Intent-A",
        orca_request_id="Req-A",
        operation_id="Op-A",
    )
    executor.execute("Op-A", first["request"], now_mono_ms=1001)
    original = store.get_provenance("Dispatch-A")
    runner.result = (
        0,
        '{"ok":true,"receipt":{"replayed":true},"result":{"dispatchId":"Dispatch-A","state":"dispatched"}}',
        "",
    )
    recovered = service.preflight(
        TypedRequest.from_dict(dispatch_data()),
        facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
        evidence_digest="Evidence-B",
        observed_mono_ms=1002,
        decision_event_id="Dec-B",
        recovery_intent_id="Intent-A",
        operation_id="Op-B",
    )
    executor.execute("Op-B", recovered["request"], now_mono_ms=1003)
    outcomes = [json.loads(row["payload"]) for row in store.events("outcomes")]
    assert outcomes[-1]["effect_claim"] == "receipt_replay"
    assert outcomes[-1]["envelope"]["receipt"]["replayed"] is True
    assert store.get_provenance("Dispatch-A") == original
    assert store.events("recovery_records")[-1]["code"] == "RECEIPT_REPLAY_RECONSTRUCTED"


def test_stop_noop_is_recorded_but_not_returned_as_containment_success(store):
    data = json.loads(json.dumps(policy().data))
    data["rules"] = [{
        "id": "stop",
        "match": {"normalized_command": "orchestration.workerStop", "state_class": "OK"},
        "action": "allow",
    }]
    stop_policy = Policy.from_bytes(json.dumps(data).encode())
    runner = FakeRunner()
    runner.result = (
        0,
        '{"ok":true,"result":{"dispatchId":"Dispatch-A","state":"stopped","alreadySettled":true,"processAction":"none"}}',
        "",
    )
    resolver = StaticIdentityResolver(context())
    service = GateService(
        store,
        stop_policy,
        resolver=resolver,
        epoch_id="epoch-A",
        _test_overrides=True,
    )
    executor = GateExecutor(store, stop_policy, OrcaAdapter(runner), resolver=resolver, epoch_id="epoch-A")
    store.record_provenance(
        "Dispatch-A",
        {
            "origin": "local",
            "created_by_operation_id": "Prior",
            "target_instance_identity": context()["target_instance_identity"],
            "source_contract_sha": context()["source_contract_sha"],
        },
    )
    request = TypedRequest.from_dict({
        "command": "orchestration.workerStop",
        "args": {"dispatch_id": "Dispatch-A"},
        "context": context(),
    })
    approved = service.preflight(
        request,
        facts={"normalized_command": "orchestration.workerStop", "state_class": "OK"},
        evidence_digest="Evidence-A",
        observed_mono_ms=1000,
        decision_event_id="Dec-A",
        mutation_intent_id="Intent-A",
        orca_request_id="Req-A",
        operation_id="Op-A",
    )
    with pytest.raises(DeniedError, match="fresh containment"):
        executor.execute("Op-A", approved["request"], now_mono_ms=1001)
    outcome = json.loads(store.events("outcomes")[0]["payload"])
    assert outcome["mechanical_success"] is False
    assert outcome["details"]["effect_claim"] == "no_new_effect"


def test_executor_binding_or_epoch_freshness_mismatch_prevents_spawn(store):
    service, executor, runner = _service(store, action="allow")
    approved = service.preflight(
        TypedRequest.from_dict(dispatch_data()),
        facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
        evidence_digest="Evidence-A",
        observed_mono_ms=1000,
        decision_event_id="Dec-A",
        mutation_intent_id="Intent-A",
        orca_request_id="Req-A",
        operation_id="Op-A",
    )
    changed = approved["request"].canonical_invocation()
    changed["structured_args"]["task_id"] = "task-a"
    bad = TypedRequest.from_dict(
        {"command": changed.pop("normalized_command"), "args": changed.pop("structured_args"), "context": {key: value for key, value in changed.items() if key != "normalization_version"}}
    )
    with pytest.raises(DeniedError, match="digest"):
        executor.execute("Op-A", bad, now_mono_ms=1001)
    executor.epoch_id = "epoch-B"
    with pytest.raises(DeniedError, match="epoch"):
        executor.execute("Op-A", approved["request"], now_mono_ms=1001)
    assert runner.calls == []


def test_preexec_persistence_failure_prevents_spawn(store):
    service, executor, runner = _service(store, action="allow")
    approved = service.preflight(
        TypedRequest.from_dict(dispatch_data()),
        facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
        evidence_digest="Evidence-A",
        observed_mono_ms=1000,
        decision_event_id="Dec-A",
        mutation_intent_id="Intent-A",
        orca_request_id="Req-A",
        operation_id="Op-A",
    )
    store.inject_failure("exec_attempt")
    with pytest.raises(PersistenceError):
        executor.execute("Op-A", approved["request"], now_mono_ms=1001)
    assert runner.calls == []
    assert store.startup_sweep() == ["operation:Op-A"]


def test_postexec_outcome_failure_uses_recovery_channel_and_reconciles(store):
    service, executor, runner = _service(store, action="allow")
    approved = service.preflight(
        TypedRequest.from_dict(dispatch_data()),
        facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
        evidence_digest="Evidence-A",
        observed_mono_ms=1000,
        decision_event_id="Dec-A",
        mutation_intent_id="Intent-A",
        orca_request_id="Req-A",
        operation_id="Op-A",
    )
    store.inject_failure("outcome")
    with pytest.raises(ReconciliationRequired, match="recording"):
        executor.execute("Op-A", approved["request"], now_mono_ms=1001)
    assert len(runner.calls) == 1
    assert store.reconciliation_required("operation:Op-A")
    assert store.events("recovery_records")[0]["code"] == "OUTCOME_RECORDING_FAILED"


def test_recovery_channel_failure_stops_supervised_flow(store):
    service, executor, runner = _service(store, action="allow")
    approved = service.preflight(
        TypedRequest.from_dict(dispatch_data()),
        facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
        evidence_digest="Evidence-A",
        observed_mono_ms=1000,
        decision_event_id="Dec-A",
        mutation_intent_id="Intent-A",
        orca_request_id="Req-A",
        operation_id="Op-A",
    )
    store.inject_failure("outcome")
    store.inject_failure("recovery_channel")
    with pytest.raises(AuditabilityLost):
        executor.execute("Op-A", approved["request"], now_mono_ms=1001)
    assert len(runner.calls) == 1


def test_reconciliation_opened_after_approval_has_preexec_precedence(store):
    service, executor, runner = _service(store, action="allow")
    approved = service.preflight(
        TypedRequest.from_dict(dispatch_data()),
        facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
        evidence_digest="Evidence-A",
        observed_mono_ms=1000,
        decision_event_id="Dec-A",
        mutation_intent_id="Intent-A",
        orca_request_id="Req-A",
        operation_id="Op-A",
    )
    store.mark_reconciliation("operation:Op-A", "late_unknown")
    with pytest.raises(ReconciliationRequired, match="precedence"):
        executor.execute("Op-A", approved["request"], now_mono_ms=1001)
    assert runner.calls == []


def test_task_scope_reconciliation_precedes_new_intent_preflight(store):
    service, _, _ = _service(store, action="allow")
    store.mark_reconciliation("task:R:T", "prior_unknown")
    result = service.preflight(
        TypedRequest.from_dict(dispatch_data()),
        facts={"normalized_command": "orchestration.dispatch", "state_class": "OK"},
        evidence_digest="Evidence-A",
        observed_mono_ms=1000,
        decision_event_id="Dec-A",
        mutation_intent_id="Intent-A",
        orca_request_id="Req-A",
        operation_id="Op-A",
    )
    assert result["decision"] == "reconciliation_required"
    assert result["invocation"] is None


def test_dispatch_targeting_preflight_rejects_cross_instance_provenance(store):
    service, _, runner = _service(store, action="allow")
    store.record_provenance(
        "Dispatch-A",
        {
            "origin": "local",
            "created_by_operation_id": "Prior",
            "target_instance_identity": "other-instance",
            "source_contract_sha": context()["source_contract_sha"],
        },
    )
    request = TypedRequest.from_dict({
        "command": "orchestration.workerStop",
        "args": {"dispatch_id": "Dispatch-A"},
        "context": context(),
    })
    with pytest.raises(ReconciliationRequired, match="provenance.*identity"):
        service.preflight(
            request,
            facts={"normalized_command": "orchestration.workerStop", "state_class": "OK"},
            evidence_digest="Evidence-A",
            observed_mono_ms=1000,
        )
    assert runner.calls == []


def worker_start_policy():
    data = policy().data
    data = json.loads(json.dumps(data))
    data["rules"] = [
        {
            "id": "worker-start",
            "match": {
                "normalized_command": "orchestration.workerStart",
                "state_class": "OK",
            },
            "action": "allow",
        }
    ]
    return Policy.from_bytes(json.dumps(data).encode())


def worker_start_data():
    return {
        "command": "orchestration.workerStart",
        "args": {
            "run_id": "R",
            "task_id": "T",
            "from_handle": "From",
            "worktree_mode": "Worktree-A",
            "resolved_worktree_id": "Worktree-A",
            "terminal_handle": "Target-Terminal",
            "setup": "inherit",
        },
        "context": context(),
    }


@pytest.mark.parametrize("accepted_dispatch", ["Dispatch-A", None])
def test_worker_start_unknown_acceptance_creates_only_receipt_bound_provenance(
    store, accepted_dispatch
):
    error_data = {"status": "pending", "accepted": {}}
    if accepted_dispatch:
        error_data["accepted"]["dispatchId"] = accepted_dispatch
    runner = FakeRunner()
    runner.result = (
        1,
        json.dumps(
            {"ok": False, "error": {"code": "operation_unknown", "data": error_data}}
        ),
        "",
    )
    resolver = StaticIdentityResolver(context())
    policy_value = worker_start_policy()
    service = GateService(
        store,
        policy_value,
        resolver=resolver,
        epoch_id="epoch-A",
        _test_overrides=True,
    )
    executor = GateExecutor(
        store,
        policy_value,
        OrcaAdapter(runner),
        resolver=resolver,
        epoch_id="epoch-A",
    )
    approved = service.preflight(
        TypedRequest.from_dict(worker_start_data()),
        facts={"normalized_command": "orchestration.workerStart", "state_class": "OK"},
        evidence_digest="Evidence-A",
        observed_mono_ms=1000,
        decision_event_id="Dec-A",
        mutation_intent_id="Intent-A",
        orca_request_id="Req-A",
        operation_id="Op-A",
    )
    with pytest.raises(ReconciliationRequired):
        executor.execute("Op-A", approved["request"], now_mono_ms=1001)
    assert store.get_provenance("Dispatch-A") is not None if accepted_dispatch else store.get_provenance("Dispatch-A") is None
    outcome = json.loads(store.events("outcomes")[0]["payload"])
    assert outcome["mechanical_success"] is False
    assert outcome["code"] == "RECONCILIATION_REQUIRED"
    assert outcome["details"]["envelope"]["error"]["code"] == "operation_unknown"
    assert store.reconciliation_required("operation:Op-A")
