'''One explicit deterministic assertion for every normative A1-A36 contract item.'''
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from hermes_orca_gate.classifiers import classify_dispatch, classify_worker, route_for
from hermes_orca_gate.errors import AuditabilityLost, DeniedError, InputError, PersistenceError, PolicyLoadError, ReconciliationRequired
from hermes_orca_gate.models import ApprovedInvocation, GATED, SCHEMAS, TypedRequest
from hermes_orca_gate.orca_cli import OrcaAdapter, OrcaDiscovery, parse_raw_flags
from hermes_orca_gate.policy import Policy
from hermes_orca_gate.service import GateExecutor, GateService, ObservationController, ObservationSession, StaticIdentityResolver, reconstruct_receipt, terminal_scope_allowed
from test_service import FakeRunner, _service, context as service_context, dispatch_data, policy as service_policy


def _context(**changes):
    value = {'cli_executable_identity': 'orca@1', 'source_contract_sha': '403c60bbadb734d870a22c372aca8a988e348d21', 'effective_target_identity': 'Target-A', 'fixed_or_bound_cwd': 'C:/Repo-A', 'execution_relevant_env': {'ORCA_USER_DATA_PATH': 'C:/Data-A', 'ORCA_DEV_CLI_INVOCATION': '0'}, 'target_instance_identity': 'Instance-A', 'dev_mode': False, 'credential_binding_id': 'Cred-A'}
    value.update(changes); return value


def _dispatch(**arg_changes):
    args = {'run_id': 'Run-A', 'task_id': 'Task-A', 'to_handle': 'To-A', 'from_handle': 'From-A', 'inject': False, 'return_preamble': False, 'dry_run': False}; args.update(arg_changes)
    return TypedRequest.from_dict({'command': 'orchestration.dispatch', 'args': args, 'context': _context()})


def _invocation(operation='Op-A'):
    return ApprovedInvocation(operation, 'policy_allow', 'Dec-A', 'orchestration.dispatch', 'args', 'policy', '403c60bbadb734d870a22c372aca8a988e348d21', 'evidence', 1, 'epoch', 'orca@1', 'Cred-A', mutation_intent_id='Intent-A', orca_request_id='Req-A')


def _policy_for(command, action='allow', state='OK'):
    data = json.loads(json.dumps(service_policy().data))
    data['rules'] = [{'id': command, 'match': {'normalized_command': command, 'state_class': state}, 'action': action}]
    return Policy.from_bytes(json.dumps(data).encode())


def _worker_request(command='orchestration.workerShow', dispatch_id='Dispatch-A'):
    return TypedRequest.from_dict({'command': command, 'args': {'dispatch_id': dispatch_id}, 'context': service_context()})


def _record_worker(store, dispatch_id='Dispatch-A', terminal='Terminal-A', started=0):
    store.record_provenance(dispatch_id, {'origin': 'local', 'created_by_operation_id': 'Prior', 'terminal_handle': terminal, 'worker_started_mono_ms': started, 'evidence_process_epoch_id': 'epoch-A', 'target_instance_identity': service_context()['target_instance_identity'], 'source_contract_sha': service_context()['source_contract_sha']})


def _worker_gate(store, command='orchestration.workerShow', action='allow', runner=None):
    selected = _policy_for(command, action)
    runner = runner or FakeRunner()
    resolver = StaticIdentityResolver(service_context())
    return (GateService(store, selected, resolver=resolver, epoch_id='epoch-A', _test_overrides=True), GateExecutor(store, selected, OrcaAdapter(runner), resolver=resolver, epoch_id='epoch-A'), runner)


def test_a01_gated_execution_requires_exact_unconsumed_invocation(store):
    service, executor, runner = _service(store, action='allow')
    approved = service.preflight(TypedRequest.from_dict(dispatch_data()), facts={'normalized_command': 'orchestration.dispatch', 'state_class': 'OK'}, evidence_digest='E', observed_mono_ms=1, decision_event_id='D', mutation_intent_id='I', orca_request_id='R', operation_id='O')
    executor.execute('O', approved['request'], now_mono_ms=2)
    with pytest.raises(DeniedError, match='consumed'): executor.execute('O', approved['request'], now_mono_ms=3)
    show_service, show_executor, show_runner = _worker_gate(store)
    shown = show_service.preflight(_worker_request(), facts={'normalized_command': 'orchestration.workerShow', 'state_class': 'OK'}, evidence_digest='Show-E', observed_mono_ms=4, decision_event_id='Show-D', operation_id='Show-O')
    show_executor.execute('Show-O', shown['request'], now_mono_ms=5)
    with pytest.raises(DeniedError, match='consumed'): show_executor.execute('Show-O', shown['request'], now_mono_ms=6)
    assert 'orchestration.workerShow' in GATED and len(runner.calls) == len(show_runner.calls) == 1


def test_a02_binding_mismatch_and_non_allow_never_execute(store):
    service, executor, runner = _service(store, action='allow')
    approved = service.preflight(TypedRequest.from_dict(dispatch_data()), facts={'normalized_command': 'orchestration.dispatch', 'state_class': 'OK'}, evidence_digest='E', observed_mono_ms=1, decision_event_id='D', mutation_intent_id='I', orca_request_id='R', operation_id='O')
    mutations = [('structured_args', 'task_id', 'Other'), ('context', 'source_contract_sha', '0' * 40), ('context', 'cli_executable_identity', 'Other-CLI'), ('context', 'credential_binding_id', 'Other-Cred')]
    for location, key, value in mutations:
        changed_data = approved['request'].canonical_invocation()
        target = changed_data['structured_args'] if location == 'structured_args' else changed_data
        target[key] = value
        if key == 'credential_binding_id':
            changed_data['mutation_intent_identity_or_null']['credential_binding_id'] = value
        command = changed_data.pop('normalized_command'); args = changed_data.pop('structured_args'); changed_data.pop('normalization_version')
        with pytest.raises(DeniedError): executor.execute('O', TypedRequest.from_dict({'command': command, 'args': args, 'context': changed_data}), now_mono_ms=2)
    executor.policy = service_policy('require_approval')
    with pytest.raises(DeniedError, match='policy'): executor.execute('O', approved['request'], now_mono_ms=2)
    with pytest.raises(DeniedError, match='ApprovedInvocation'): executor.execute('Missing', approved['request'], now_mono_ms=2)
    for action, suffix in [('require_approval', 'Need'), ('block', 'Block')]:
        non_allow, _, non_runner = _service(store, action=action)
        result = non_allow.preflight(TypedRequest.from_dict(dispatch_data()), facts={'normalized_command': 'orchestration.dispatch', 'state_class': 'OK'}, evidence_digest=suffix, observed_mono_ms=3, decision_event_id=suffix + '-D', mutation_intent_id=suffix + '-I', orca_request_id=suffix + '-R', operation_id=suffix + '-O')
        assert result['invocation'] is None and non_runner.calls == []
    assert runner.calls == []


def test_a03_policy_load_error_is_default_deny():
    valid = json.loads(json.dumps(service_policy().data))
    invalid = []
    for mutate in (
        lambda d: d.update(extra=True),
        lambda d: d.update(version=2),
        lambda d: d.update(default={'action': 'allow'}),
        lambda d: d['rules'][0]['match'].update(operator='contains'),
        lambda d: d['freshness_max_age_ms'].pop('orchestration.workerShow'),
        lambda d: d['observation'].update(interval_ms=1),
    ):
        candidate = json.loads(json.dumps(valid)); mutate(candidate); invalid.append(candidate)
    invalid.append(None)
    for candidate in invalid:
        with pytest.raises(PolicyLoadError): Policy.from_bytes(json.dumps(candidate).encode())


def test_a04_run_and_from_are_explicit_and_consumer_fenced_is_failure():
    for field in ('run_id', 'from_handle'):
        data = {'command': _dispatch().command, 'args': dict(_dispatch().args), 'context': _context()}; del data['args'][field]
        with pytest.raises(InputError): TypedRequest.from_dict(data)
    missing_context = {'command': _dispatch().command, 'args': dict(_dispatch().args), 'context': _context()}; missing_context['context'].pop('credential_binding_id')
    with pytest.raises(InputError): TypedRequest.from_dict(missing_context)
    calls = []
    def mismatch_runner(argv, *, cwd, env, shell):
        calls.append(argv)
        return 0, json.dumps({'ok': True, 'result': {'runId': 'Other-Run', 'terminalHandle': 'From-A'}}), ''
    assert OrcaDiscovery(OrcaAdapter(mismatch_runner)).discover(_dispatch()).facts['state_class'] == 'RUNTIME_UNKNOWN'
    assert len(calls) == 1 and calls[0][1:3] == ['orchestration', 'run-current']
    fenced = OrcaAdapter(lambda *a, **k: (1, '{"ok":false,"error":{"code":"consumer_fenced"}}', ''))
    with pytest.raises(DeniedError) as caught: fenced.invoke(_dispatch(), orca_request_id='Req-A')
    assert caught.value.details['error']['code'] == 'consumer_fenced'


def test_a05_complete_flag_fence_rejects_unknown_duplicate_positional_and_check():
    schema = {'--run': 'value'}
    for raw in (['--bad'], ['--run', 'A', '--run=B'], ['word']):
        with pytest.raises(InputError): parse_raw_flags(raw, schema)
    with pytest.raises(InputError): parse_raw_flags([], schema, command='orchestration.check')
    assert set(SCHEMAS) == {'orchestration.dispatch', 'orchestration.dispatchDryRun', 'orchestration.workerStart', 'orchestration.workerStop', 'orchestration.workerAbandon', 'orchestration.workerShow', 'orchestration.runCurrent', 'orchestration.runShow', 'orchestration.taskList', 'orchestration.dispatchShow', 'orchestration.workerRead', 'terminal.read', 'terminal.wait'}
    for command in SCHEMAS:
        with pytest.raises(InputError): parse_raw_flags(['--on', 'remote'], {}, command=command)


def test_a06_flag_equivalence_and_presence_distinction():
    assert parse_raw_flags(['--run=A'], {'--run': 'value'}) == parse_raw_flags(['--run', 'A'], {'--run': 'value'})
    preview = {'command': 'orchestration.dispatchDryRun', 'args': {'run_id': 'R', 'task_id': 'T', 'from_handle': 'F', 'dry_run': True, 'return_preamble': False}, 'context': _context()}
    assert TypedRequest.from_dict(preview).args_digest != TypedRequest.from_dict({**preview, 'args': {**preview['args'], 'to_handle': None}}).args_digest


def test_a07_context_variance_changes_digest():
    base = _dispatch(); digests = set()
    for key, value in [('fixed_or_bound_cwd', 'C:/Other'), ('target_instance_identity', 'Other'), ('cli_executable_identity', 'orca@2'), ('credential_binding_id', 'Cred-B'), ('dev_mode', True)]:
        digests.add(TypedRequest(base.command, base.args, {**base.context, key: value}).args_digest)
    assert len(digests) == 5 and base.args_digest not in digests


def test_a08_repo_worktree_terminal_relationships_and_current_refused():
    data = {'command': 'orchestration.workerStart', 'args': {'run_id': 'R', 'task_id': 'T', 'from_handle': 'F', 'worktree_mode': 'current', 'setup': 'inherit'}, 'context': _context()}
    with pytest.raises(InputError): TypedRequest.from_dict(data)
    conflicting = {'command': 'orchestration.workerStart', 'args': {'run_id': 'R', 'task_id': 'T', 'from_handle': 'F', 'worktree_mode': 'WT', 'resolved_worktree_id': 'WT', 'repo_id': 'Repo', 'setup': 'inherit'}, 'context': _context()}
    with pytest.raises(InputError, match='forbids'): TypedRequest.from_dict(conflicting)


def test_a09_on_and_federation_are_independently_refused():
    with pytest.raises(InputError): parse_raw_flags(['--on=remote'], {'--on': 'value'})
    with pytest.raises(InputError): parse_raw_flags([], {}, command='federation-status')


def test_a10_unproven_dispatch_is_refused_before_cli(store):
    service, _, runner = _worker_gate(store)
    with pytest.raises(ReconciliationRequired):
        service.preflight(_worker_request(dispatch_id='Unknown'), facts={'normalized_command': 'orchestration.workerShow', 'state_class': 'OK'}, evidence_digest='E', observed_mono_ms=1)
    assert store.reconciliation_required('dispatch:Unknown') and runner.calls == []


def test_a11_provenance_store_failure_is_fail_closed(store):
    service, _, runner = _worker_gate(store)
    store.inject_failure('read')
    with pytest.raises(PersistenceError):
        service.preflight(_worker_request(dispatch_id='D'), facts={'normalized_command': 'orchestration.workerShow', 'state_class': 'OK'}, evidence_digest='E', observed_mono_ms=1)
    store.clear_failure('read')
    assert store.reconciliation_required('dispatch:D') and runner.calls == []


def test_a12_worker_show_is_side_effecting_and_not_auto_replayable(store):
    _record_worker(store)
    runner = FakeRunner(); runner.result = (0, 'not-json', '')
    service, executor, _ = _worker_gate(store, runner=runner)
    approved = service.preflight(_worker_request(), facts={'normalized_command': 'orchestration.workerShow', 'state_class': 'OK'}, evidence_digest='E', observed_mono_ms=1, decision_event_id='D', operation_id='O')
    with pytest.raises(ReconciliationRequired, match='malformed'): executor.execute('O', approved['request'], now_mono_ms=2)
    with pytest.raises(ReconciliationRequired, match='reconciliation'): executor.execute('O', approved['request'], now_mono_ms=3)
    assert 'orchestration.workerShow' in GATED and len(runner.calls) == 1


def test_a13_dispatch_classifier_covers_all_enums_and_default():
    values = {status: classify_dispatch(status) for status in ('pending', 'dispatched', 'failed', 'circuit_broken')}
    values['completed'] = classify_dispatch('completed', completion_evidence=True)
    assert set(values.values()) == {'OBSERVATION_PENDING', 'OK', 'FAILED', 'CIRCUIT_BROKEN'} and classify_dispatch('other') == 'RUNTIME_UNKNOWN'
    assert classify_dispatch('completed') == 'RUNTIME_UNKNOWN'


def test_a14_worker_classifier_covers_45_pairs_and_missing_worker():
    dispatches = ('pending', 'dispatched', 'completed', 'failed', 'circuit_broken'); workers = ('starting', 'ready', 'start_unknown', 'failed', 'succeeded', 'stopping', 'stop_unknown', 'stopped', 'abandoned')
    def expected(dispatch, worker):
        if dispatch == 'circuit_broken': return 'CIRCUIT_BROKEN'
        if worker in {'start_unknown', 'stop_unknown', 'abandoned'}: return 'RUNTIME_UNKNOWN'
        if dispatch == 'failed' or worker == 'failed': return 'FAILED'
        if worker == 'starting': return 'STARTING'
        if worker == 'stopping': return 'STOPPING'
        if worker == 'stopped': return 'STOPPED'
        if worker == 'succeeded': return 'OK'
        if dispatch == 'completed': return 'RUNTIME_UNKNOWN'
        if worker == 'ready': return 'OBSERVATION_PENDING'
        return 'RUNTIME_UNKNOWN'
    assert {(d, w): classify_worker(d, w) for d in dispatches for w in workers} == {(d, w): expected(d, w) for d in dispatches for w in workers}
    assert classify_worker('pending', None) == 'RUNTIME_UNKNOWN'


def test_a15_unknown_abandoned_and_stopped_never_imply_retry():
    assert route_for(classify_worker('pending', 'stop_unknown')) == 'owner_decision'
    assert route_for(classify_worker('pending', 'abandoned')) == 'owner_decision'
    assert route_for(classify_worker('pending', 'stopped')) == 'contained'


def test_a16_probe_idle_and_output_update_counter_exactly():
    session = ObservationSession(0, 100, 3); session.begin_probe(); assert session.finish_probe({'satisfied': True}, new_output=False, now_ms=1) == 'observe'
    assert session.consecutive_idle == 1; session.begin_probe(); session.finish_probe({'satisfied': True}, new_output=True, now_ms=2); assert session.consecutive_idle == 0


def test_a17_exact_timeout_is_busy_not_runtime_unknown():
    session = ObservationSession(0, 100, 3); session.begin_probe()
    assert session.finish_probe({'error': {'code': 'timeout'}}, new_output=False, now_ms=1) == 'observe' and not session.runtime_unknown


def test_a18_blocked_reason_escalates_other_errors_discard():
    blocked = ObservationSession(0, 100, 3); blocked.begin_probe(); assert blocked.finish_probe({'satisfied': False, 'blockedReason': 'permission'}, new_output=False, now_ms=1) == 'require_approval'
    unknown = ObservationSession(0, 100, 3); unknown.begin_probe(); assert unknown.finish_probe({'error': {'code': 'transport'}}, new_output=False, now_ms=1) == 'RUNTIME_UNKNOWN' and unknown.consecutive_idle == 0


def test_a19_probes_are_serial_and_three_idle_escalates():
    session = ObservationSession(0, 100, 3); assert session.begin_probe() and not session.begin_probe()
    session.finish_probe({'satisfied': True}, new_output=False, now_ms=1)
    for now in (2, 3): session.begin_probe(); route = session.finish_probe({'satisfied': True}, new_output=False, now_ms=now)
    assert route == 'require_approval'


def test_a20_ceiling_is_absolute_and_independent_of_startup_timeout(store):
    _record_worker(store)
    controller = ObservationController(store, service_policy(), OrcaAdapter(lambda *a, **k: (_ for _ in ()).throw(AssertionError('ceiling must prevent probe'))), resolver=StaticIdentityResolver(service_context()), epoch_id='epoch-A', clock=lambda: 2700000)
    result = controller.observe('Dispatch-A')
    assert result['route'] == 'require_approval' and result['decision_event_id']
    assert 'startup_timeout_ms' not in store.get_decision(result['decision_event_id'])
    _record_worker(store, 'Dispatch-B', 'Terminal-B')
    ticks = iter((0, 1, 2)); calls = []
    def output_runner(argv, *, cwd, env, shell):
        calls.append(argv)
        result = {'satisfied': True} if argv[1:3] == ['terminal', 'wait'] else {'cursor': 7, 'output': 'arrived-during-wait'}
        return 0, json.dumps({'ok': True, 'result': result}), ''
    output_controller = ObservationController(store, service_policy(), OrcaAdapter(output_runner), resolver=StaticIdentityResolver(service_context()), epoch_id='epoch-A', clock=lambda: next(ticks))
    output = output_controller.observe('Dispatch-B')
    persisted = store.get_observation_state('dispatch:Dispatch-B')
    assert output['route'] == 'observe' and output['cursor'] == 7 and persisted['consecutive_idle'] == 0
    assert [call[1:3] for call in calls] == [['terminal', 'wait'], ['terminal', 'read']]


def test_a21_post_ceiling_requires_new_owner_deadline(store):
    store.begin_observation_probe('dispatch:Dispatch-A', started_ms=0, deadline_ms=100, epoch_id='epoch-A', now_ms=1)
    store.finish_observation_probe('dispatch:Dispatch-A', consecutive_idle=0, cursor=0, payload={'route': 'require_approval'}, next_probe_ms=100)
    store.append_owner_authorization('Bad-Auth', 'D', {'authorization_kind': 'owner_approval', 'exact_action': 'observation.continue', 'observation_scope': 'dispatch:Dispatch-A', 'observation_deadline_ms': 100})
    controller = ObservationController(store, service_policy(), OrcaAdapter(), resolver=StaticIdentityResolver(service_context()), epoch_id='epoch-A', clock=lambda: 100)
    with pytest.raises(PersistenceError): controller.continue_observation('Dispatch-A', 'Bad-Auth')
    store.append_owner_authorization('Good-Auth', 'D', {'authorization_kind': 'owner_approval', 'exact_action': 'observation.continue', 'observation_scope': 'dispatch:Dispatch-A', 'observation_deadline_ms': 200})
    controller.continue_observation('Dispatch-A', 'Good-Auth')
    state = store.get_observation_state('dispatch:Dispatch-A')
    assert state['deadline_ms'] == 200 and json.loads(store.events('observation_events')[-1]['payload'])['authorization_id'] == 'Good-Auth'


def test_a22_claude_permission_residual_follows_timeout_until_ceiling(store):
    _record_worker(store)
    calls = []
    def timeout_runner(argv, *, cwd, env, shell):
        calls.append(argv); return 1, '{"ok":false,"error":{"code":"timeout"}}', ''
    ticks = iter((2_699_999, 2_700_000))
    controller = ObservationController(store, service_policy(), OrcaAdapter(timeout_runner), resolver=StaticIdentityResolver(service_context()), epoch_id='epoch-A', clock=lambda: next(ticks))
    result = controller.observe('Dispatch-A')
    state = store.get_observation_state('dispatch:Dispatch-A')
    assert result['route'] == 'require_approval' and state['consecutive_idle'] == 0 and len(calls) == 1


def test_a23_atomic_consumption_has_one_winner(store):
    service, executor, runner = _service(store, action='allow')
    approved = service.preflight(TypedRequest.from_dict(dispatch_data()), facts={'normalized_command': 'orchestration.dispatch', 'state_class': 'OK'}, evidence_digest='E', observed_mono_ms=1, decision_event_id='D', mutation_intent_id='I', orca_request_id='R', operation_id='O')
    def attempt(_):
        try:
            executor.execute('O', approved['request'], now_mono_ms=2); return 'executed'
        except DeniedError:
            return 'denied'
    with ThreadPoolExecutor(max_workers=4) as pool: results = list(pool.map(attempt, range(8)))
    assert results.count('executed') == 1 and results.count('denied') == 7 and len(runner.calls) == 1


@pytest.mark.parametrize('with_exec_marker', [False, True])
def test_a24_consumed_without_outcome_startup_reconciles_each_pre_outcome_boundary(store, with_exec_marker):
    operation = 'Op-With-Marker' if with_exec_marker else 'Op-After-Consume'
    store.append_decision('Dec-' + operation, {'action': 'allow'}); store.append_approved_invocation(_invocation(operation)); store.consume(operation)
    if with_exec_marker: store.append_exec_attempt(operation, {'spawn_boundary': 'unknown'})
    assert store.startup_sweep() == [f'operation:{operation}'] and store.reconciliation_required(f'operation:{operation}')


def test_a25_pre_exec_persistence_failure_prevents_spawn_post_exec_routes_recovery(store):
    service, executor, runner = _service(store, action='allow')
    first = service.preflight(TypedRequest.from_dict(dispatch_data()), facts={'normalized_command': 'orchestration.dispatch', 'state_class': 'OK'}, evidence_digest='E1', observed_mono_ms=1, decision_event_id='D1', mutation_intent_id='I1', orca_request_id='R1', operation_id='O1')
    store.inject_failure('exec_attempt')
    with pytest.raises(PersistenceError): executor.execute('O1', first['request'], now_mono_ms=2)
    assert runner.calls == []
    store.clear_failure('exec_attempt')
    second = service.preflight(TypedRequest.from_dict(dispatch_data()), facts={'normalized_command': 'orchestration.dispatch', 'state_class': 'OK'}, evidence_digest='E2', observed_mono_ms=3, decision_event_id='D2', mutation_intent_id='I2', orca_request_id='R2', operation_id='O2')
    store.inject_failure('outcome')
    with pytest.raises(ReconciliationRequired, match='recording'): executor.execute('O2', second['request'], now_mono_ms=4)
    store.clear_failure('outcome')
    assert len(runner.calls) == 1 and store.reconciliation_required('operation:O2')
    assert store.events('recovery_records')[-1]['code'] == 'OUTCOME_RECORDING_FAILED'


def test_a26_recovery_channel_failure_stops_and_reconciliation_precedes(store):
    service, executor, runner = _service(store, action='allow')
    approved = service.preflight(TypedRequest.from_dict(dispatch_data()), facts={'normalized_command': 'orchestration.dispatch', 'state_class': 'OK'}, evidence_digest='E', observed_mono_ms=1, decision_event_id='D', mutation_intent_id='I', orca_request_id='R', operation_id='O')
    store.inject_failure('outcome'); store.inject_failure('recovery_channel')
    with pytest.raises(AuditabilityLost): executor.execute('O', approved['request'], now_mono_ms=2)
    store.clear_failure('outcome'); store.clear_failure('recovery_channel')
    assert store.startup_sweep() == ['operation:O']
    assert len(runner.calls) == 1 and store.reconciliation_required('operation:O')
    with pytest.raises(ReconciliationRequired): executor.execute('O', approved['request'], now_mono_ms=3)
    assert len(runner.calls) == 1


def test_a27_uncertain_intent_stabilizes_request_and_exact_binding(store):
    store.create_mutation_intent(intent_id='Intent-A', request_id='Req-A', method='orchestration.dispatch', payload_digest='payload', credential_binding_id='Cred-A', target_instance_identity='Instance-A')
    assert store.recovery_request_id('Intent-A', method='orchestration.dispatch', payload_digest='payload', credential_binding_id='Cred-A', target_instance_identity='Instance-A') == 'Req-A'
    with pytest.raises(DeniedError): store.recovery_request_id('Intent-A', method='orchestration.dispatch', payload_digest='other', credential_binding_id='Cred-A', target_instance_identity='Instance-A')


def test_a28_deliberate_second_mutation_and_completed_replay_are_distinguished(store):
    service, executor, runner = _service(store, action='allow')
    first = service.preflight(TypedRequest.from_dict(dispatch_data()), facts={'normalized_command': 'orchestration.dispatch', 'state_class': 'OK'}, evidence_digest='E1', observed_mono_ms=1, decision_event_id='D1', mutation_intent_id='I1', orca_request_id='R1', operation_id='O1')
    executor.execute('O1', first['request'], now_mono_ms=2)
    runner.result = (0, '{"ok":true,"receipt":{"replayed":true},"result":{"dispatchId":"Dispatch-A","state":"dispatched"}}', '')
    replay = service.preflight(TypedRequest.from_dict(dispatch_data()), facts={'normalized_command': 'orchestration.dispatch', 'state_class': 'OK'}, evidence_digest='E2', observed_mono_ms=3, decision_event_id='D2', recovery_intent_id='I1', operation_id='O2')
    executor.execute('O2', replay['request'], now_mono_ms=4)
    second = service.preflight(TypedRequest.from_dict(dispatch_data()), facts={'normalized_command': 'orchestration.dispatch', 'state_class': 'OK'}, evidence_digest='E3', observed_mono_ms=5, decision_event_id='D3', mutation_intent_id='I2', orca_request_id='R2', operation_id='O3')
    outcomes = [json.loads(row['payload']) for row in store.events('outcomes')]
    assert outcomes[-1]['effect_claim'] == 'receipt_replay'
    assert second['request'].context['mutation_intent_identity_or_null']['orca_request_id'] == 'R2'


def test_a29_acceptance_reconstruction_is_limited_and_missing_dispatch_is_unknown():
    accepted = reconstruct_receipt('orchestration.workerStart', {'status': 'pending', 'accepted': {'dispatchId': 'D'}})
    assert accepted == {'kind': 'accepted', 'dispatch_id': 'D', 'success': False}
    assert reconstruct_receipt('orchestration.workerStart', {'status': 'pending', 'accepted': {}})['kind'] == 'operation_unknown'
    assert reconstruct_receipt('orchestration.workerStop', {'status': 'pending'})['success'] is False


def test_a30_historical_noop_is_not_fresh_containment_and_malformed_reconciles():
    stop = {'dispatchId': 'D', 'state': 'stopped', 'alreadySettled': True, 'processAction': 'none', 'closeEvidence': {'closed': True}}
    assert not OrcaAdapter.fresh_containment('orchestration.workerStop', stop)
    with pytest.raises(ReconciliationRequired): OrcaAdapter.validate_outcome('orchestration.workerAbandon', {'dispatchId': 'D'})


def test_a31_dry_run_has_no_inject_retry_intent_or_provenance(store):
    data = {'command': 'orchestration.dispatchDryRun', 'args': {'run_id': 'R', 'task_id': 'T', 'from_handle': 'F', 'dry_run': True, 'return_preamble': False}, 'context': _context()}
    request = TypedRequest.from_dict(data); runner = FakeRunner()
    selected = _policy_for('orchestration.dispatchDryRun')
    service = GateService(store, selected, resolver=StaticIdentityResolver(_context()), epoch_id='epoch-A', discovery=OrcaDiscovery(OrcaAdapter(runner)), _test_overrides=True)
    result = service.preflight(request, facts={'normalized_command': request.command, 'state_class': 'OK'}, evidence_digest='E', observed_mono_ms=1, decision_event_id='D')
    argv = runner.calls[0][0]
    with store.connect() as database:
        intent_count = database.execute('SELECT COUNT(*) FROM mutation_intents').fetchone()[0]
        provenance_count = database.execute('SELECT COUNT(*) FROM provenance_records').fetchone()[0]
    assert result['decision'] == 'allow' and result['invocation'] is None and len(runner.calls) == 1
    assert '--dry-run' in argv and '--inject' not in argv and '--retry-request' not in argv
    assert intent_count == provenance_count == 0 and request.context.get('mutation_intent_identity_or_null') is None


def test_a32_worker_topology_is_exact_and_other_effect_authority_absent():
    args = {'run_id': 'R', 'task_id': 'T', 'from_handle': 'F', 'worktree_mode': 'new-child', 'setup': 'inherit', 'parent_worktree_id': 'WT-P', 'repo_id': 'Repo-A', 'base_branch': 'Main-X', 'name': 'Worker-X'}
    request = TypedRequest.from_dict({'command': 'orchestration.workerStart', 'args': args, 'context': _context()})
    assert request.args['repo_id'] == 'Repo-A' and 'filesystem_path' not in request.args
    existing = {'command': 'orchestration.workerStart', 'args': {'run_id': 'R', 'task_id': 'T', 'from_handle': 'F', 'worktree_mode': 'WT-A', 'resolved_worktree_id': 'WT-A', 'terminal_handle': 'Target-Terminal', 'setup': 'inherit'}, 'context': _context()}
    existing_request = TypedRequest.from_dict(existing)
    result = {'runId': 'R', 'taskId': 'T', 'dispatchId': 'D', 'state': 'starting', 'effects': [], 'residualResources': [], 'startupTimeoutMs': 1, 'worktreeId': 'WT-B'}
    runner = lambda argv, **kwargs: (0, json.dumps({'ok': True, 'result': result}), '')
    with pytest.raises(ReconciliationRequired, match='outcome identity'): OrcaAdapter(runner).invoke(existing_request, orca_request_id='Receipt-A')


def test_a33_freshness_is_bounded_and_epoch_mismatch_invalidates(store):
    service, executor, runner = _service(store, action='allow')
    approved = service.preflight(TypedRequest.from_dict(dispatch_data()), facts={'normalized_command': 'orchestration.dispatch', 'state_class': 'OK'}, evidence_digest='E', observed_mono_ms=1000, decision_event_id='D', mutation_intent_id='I', orca_request_id='R', operation_id='O')
    executor.epoch_id = 'epoch-B'
    with pytest.raises(DeniedError, match='epoch'): executor.execute('O', approved['request'], now_mono_ms=1001)
    executor.epoch_id = 'epoch-A'
    with pytest.raises(DeniedError, match='stale'): executor.execute('O', approved['request'], now_mono_ms=31_001)
    assert 1 <= executor.policy.freshness_for('orchestration.workerShow') <= 60000 and runner.calls == []


def test_a34_case_sensitive_ids_are_not_normalized():
    assert _dispatch(run_id='Run-A').args_digest != _dispatch(run_id='run-a').args_digest


def test_a35_owner_resolution_revalidates_action_digest_state_intent_and_keeps_basis(store):
    service, _, _ = _service(store)
    pending = service.preflight(TypedRequest.from_dict(dispatch_data()), facts={'normalized_command': 'orchestration.dispatch', 'state_class': 'OK'}, evidence_digest='E1', observed_mono_ms=1, decision_event_id='Basis', mutation_intent_id='Intent-A', orca_request_id='Receipt-A')
    service.owner_authorize('Basis', 'Auth', authorization_kind='owner_approval')
    changed = pending['request'].canonical_invocation(); changed['structured_args']['task_id'] = 'Other'
    command = changed.pop('normalized_command'); args = changed.pop('structured_args'); changed.pop('normalization_version')
    with pytest.raises(DeniedError, match='matches authorization'):
        service.resolve_approval('Auth', TypedRequest.from_dict({'command': command, 'args': args, 'context': changed}), facts={'normalized_command': 'orchestration.dispatch', 'state_class': 'OK'}, evidence_digest='E2', observed_mono_ms=2)
    with pytest.raises(DeniedError, match='matches authorization'):
        service.resolve_approval('Auth', pending['request'], facts={'normalized_command': 'orchestration.dispatch', 'state_class': 'FAILED'}, evidence_digest='E3', observed_mono_ms=3)
    store.append_decision('Blocked', {'action': 'block', 'normalized_command': 'orchestration.dispatch', 'args_digest': pending['request'].args_digest, 'state_class': 'OK'})
    with pytest.raises(DeniedError): service.owner_authorize('Blocked', 'Block-Auth', authorization_kind='owner_approval')
    store.mark_reconciliation('operation:Recon-A', 'unknown')
    store.append_decision('Recon-Basis', {'action': 'reconciliation_required', 'normalized_command': 'orchestration.dispatch', 'args_digest': pending['request'].args_digest, 'state_class': 'RUNTIME_UNKNOWN', 'mutation_intent_id': 'Intent-A', 'reconciliation_scope': 'operation:Recon-A'})
    with pytest.raises(DeniedError): service.owner_authorize('Recon-Basis', 'Recon-Auth', authorization_kind='owner_reconciliation', reconciliation_scope='operation:Other', reconciliation_action='receipt_recovery')
    assert store.get_decision('Basis')['action'] == 'require_approval'


def test_a36_terminal_read_wait_are_bound_not_arbitrary(store):
    request = TypedRequest.from_dict({'command': 'terminal.read', 'args': {'terminal_handle': 'Term-A', 'cursor': 0, 'limit': 100}, 'context': _context()})
    runner = FakeRunner(); runner.result = (0, '{"ok":true,"result":{"cursor":1,"output":"bound"}}', '')
    selected = _policy_for('terminal.read')
    service = GateService(store, selected, resolver=StaticIdentityResolver(_context()), epoch_id='epoch-A', discovery=OrcaDiscovery(OrcaAdapter(runner)), _test_overrides=True)
    with pytest.raises(DeniedError, match='not current or bound'):
        service.preflight(request, facts={'normalized_command': 'terminal.read', 'state_class': 'OK'}, evidence_digest='E1', observed_mono_ms=1)
    assert runner.calls == []
    _record_worker(store, terminal='Term-A')
    allowed = service.preflight(request, facts={'normalized_command': 'terminal.read', 'state_class': 'OK'}, evidence_digest='E2', observed_mono_ms=2, decision_event_id='D2')
    assert allowed['decision'] == 'allow' and len(runner.calls) == 1
    assert terminal_scope_allowed('Term-A', current_terminal=None, provenance_terminal='Term-A') and not terminal_scope_allowed('term-a', current_terminal='Term-A', provenance_terminal=None)
