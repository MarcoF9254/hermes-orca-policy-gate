import pytest
from hermes_orca_gate.errors import DeniedError, InputError, OutcomeSchemaError, ReconciliationRequired
from hermes_orca_gate.models import TypedRequest
from hermes_orca_gate.orca_cli import OrcaAdapter, OrcaDiscovery, parse_raw_flags
from test_service import context, dispatch_data


class Runner:
    def __init__(self, result=None): self.calls = []; self.result = result or (0, '{"ok":true,"result":{"dispatchId":"D","state":"dispatched"}}', '')
    def __call__(self, argv, *, cwd, env, shell): self.calls.append((argv, cwd, env, shell)); return self.result


def test_adapter_mechanical_argv_and_no_shell():
    runner = Runner(); adapter = OrcaAdapter(runner)
    request = TypedRequest.from_dict(dispatch_data())
    adapter.invoke(request, orca_request_id='Req-A')
    argv, cwd, env, shell = runner.calls[0]
    assert argv.count('--json') == 1 and argv[-1] == '--json'
    assert argv[0] == 'C:/orca/orca.exe@1' and shell is False and cwd == 'C:/repo'
    assert argv[argv.index('--retry-request') + 1] == 'Req-A'
    assert env == context()['execution_relevant_env']


def test_raw_flag_parser_equivalence_duplicates_and_refusals():
    schema = {'--run': 'value', '--inject': 'bool'}
    assert parse_raw_flags(['--run=Run-A', '--inject'], schema) == parse_raw_flags(['--run', 'Run-A', '--inject'], schema)
    for raw in (['--run', 'A', '--run=B'], ['positional'], ['--unknown'], ['--on=remote']):
        with pytest.raises(InputError): parse_raw_flags(raw, schema)
    for command in ('federation-status', 'orchestration.check', 'orchestration.worker-release'):
        with pytest.raises(InputError): parse_raw_flags([], schema, command=command)


def test_truthful_stop_and_abandon_outcomes():
    stop = {'dispatchId': 'D', 'state': 'stopped', 'alreadySettled': False, 'processAction': 'terminated', 'closeEvidence': {'closed': True}}
    assert OrcaAdapter.fresh_containment('orchestration.workerStop', stop)
    assert not OrcaAdapter.fresh_containment('orchestration.workerStop', {**stop, 'alreadySettled': True})
    abandon = {'dispatchId': 'D', 'state': 'abandoned', 'alreadySettled': False, 'stale': False, 'processAction': 'fenced', 'residualResources': []}
    assert OrcaAdapter.fresh_containment('orchestration.workerAbandon', abandon)
    assert not OrcaAdapter.fresh_containment('orchestration.workerAbandon', {**abandon, 'stale': True})
    with pytest.raises(OutcomeSchemaError): OrcaAdapter.validate_outcome('orchestration.workerStop', {'dispatchId': 'D'})


def worker_start_request():
    return TypedRequest.from_dict(
        {
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
    )


@pytest.mark.parametrize(
    ("accepted", "expected_dispatch"),
    [({"dispatchId": "Dispatch-A"}, "Dispatch-A"), ({}, None)],
)
def test_worker_start_operation_unknown_routes_reconciliation_with_limited_metadata(
    accepted, expected_dispatch
):
    envelope = {
        "ok": False,
        "error": {
            "code": "operation_unknown",
            "data": {"status": "pending", "accepted": accepted},
        },
    }
    runner = Runner((1, __import__("json").dumps(envelope), ""))
    with pytest.raises(ReconciliationRequired) as caught:
        OrcaAdapter(runner).invoke(worker_start_request(), orca_request_id="Req-A")
    assert caught.value.details["kind"] == "operation_unknown"
    assert caught.value.details.get("accepted_dispatch_id") == expected_dispatch


def test_mutation_success_outcomes_require_complete_command_specific_evidence():
    incomplete = Runner((0, '{"ok":true,"result":{"dispatchId":"D"}}', ""))
    with pytest.raises(OutcomeSchemaError):
        OrcaAdapter(incomplete).invoke(worker_start_request(), orca_request_id="Req-A")
    complete_result = {
        "runId": "R",
        "taskId": "T",
        "dispatchId": "D",
        "state": "starting",
        "effects": [],
        "residualResources": [],
        "startupTimeoutMs": 60000,
    }
    complete = Runner((0, __import__("json").dumps({"ok": True, "result": complete_result}), ""))
    assert OrcaAdapter(complete).invoke(worker_start_request(), orca_request_id="Req-A")["result"] == complete_result


@pytest.mark.parametrize(
    "change",
    [
        lambda result: result.update(runId="Other-Run"),
        lambda result: result.update(taskId="Other-Task"),
        lambda result: result.update(worktreeId="Other-Worktree"),
    ],
)
def test_worker_start_outcome_identity_must_match_approved_request(change):
    result = {
        "runId": "R",
        "taskId": "T",
        "dispatchId": "D",
        "state": "starting",
        "effects": [],
        "residualResources": [],
        "startupTimeoutMs": 60000,
        "worktreeId": "Worktree-A",
    }
    change(result)
    runner = Runner((0, __import__("json").dumps({"ok": True, "result": result}), ""))
    with pytest.raises(ReconciliationRequired, match="outcome identity"):
        OrcaAdapter(runner).invoke(worker_start_request(), orca_request_id="Req-A")


def test_dispatch_targeting_outcome_dispatch_id_must_match_request():
    request = TypedRequest.from_dict(
        {
            "command": "orchestration.workerStop",
            "args": {"dispatch_id": "Dispatch-A"},
            "context": context(),
        }
    )
    result = {
        "dispatchId": "Other-Dispatch",
        "state": "stopped",
        "alreadySettled": False,
        "processAction": "terminated",
        "closeEvidence": {"closed": True},
    }
    runner = Runner((0, __import__("json").dumps({"ok": True, "result": result}), ""))
    with pytest.raises(ReconciliationRequired, match="outcome identity"):
        OrcaAdapter(runner).invoke(request, orca_request_id="Req-A")


def test_malformed_read_only_result_fails_without_claiming_reconciliation():
    request = TypedRequest.from_dict(
        {"command": "orchestration.runShow", "args": {"run_id": "R"}, "context": context()}
    )
    with pytest.raises(DeniedError, match="malformed"):
        OrcaAdapter(Runner((0, "not-json", ""))).invoke(request)


def test_dispatch_discovery_uses_only_admitted_reads_and_classifies_evidence():
    calls = []

    def runner(argv, *, cwd, env, shell):
        calls.append(argv)
        command = tuple(argv[1:3])
        results = {
            ("orchestration", "run-current"): {"runId": "R", "terminalHandle": "From"},
            ("orchestration", "run-show"): {"runId": "R"},
            ("orchestration", "task-list"): {
                "tasks": [{"taskId": "T", "status": "ready"}]
            },
            ("orchestration", "dispatch-show"): {"dispatches": []},
        }
        return 0, __import__("json").dumps({"ok": True, "result": results[command]}), ""

    discovery = OrcaDiscovery(OrcaAdapter(runner), clock=lambda: 1234)
    snapshot = discovery.discover(TypedRequest.from_dict(dispatch_data()))
    assert snapshot.facts["state_class"] == "OK"
    assert snapshot.facts["normalized_command"] == "orchestration.dispatch"
    assert snapshot.observed_mono_ms == 1234
    assert [call[1:3] for call in calls] == [
        ["orchestration", "run-current"],
        ["orchestration", "run-show"],
        ["orchestration", "task-list"],
        ["orchestration", "dispatch-show"],
    ]
    assert all("--retry-request" not in call and "--dry-run" not in call for call in calls)
    dispatch_show = calls[-1]
    assert dispatch_show[dispatch_show.index("--from") + 1] == "From"


def test_dry_run_discovery_defers_preview_until_after_policy_decision():
    calls = []

    def runner(argv, *, cwd, env, shell):
        calls.append(argv)
        command = tuple(argv[1:3])
        result = {
            ("orchestration", "run-current"): {"runId": "R"},
            ("orchestration", "run-show"): {"runId": "R"},
            ("orchestration", "task-list"): {"tasks": [{"taskId": "T"}]},
            ("orchestration", "dispatch-show"): {"dispatches": []},
        }[command]
        return 0, __import__("json").dumps({"ok": True, "result": result}), ""

    request = TypedRequest.from_dict(
        {
            "command": "orchestration.dispatchDryRun",
            "args": {
                "run_id": "R",
                "task_id": "T",
                "from_handle": "From",
                "dry_run": True,
                "return_preamble": False,
            },
            "context": context(),
        }
    )
    snapshot = OrcaDiscovery(OrcaAdapter(runner)).discover(request)
    assert snapshot.facts["state_class"] == "OK"
    assert all("--dry-run" not in call for call in calls)
    assert "preview" not in snapshot.evidence


def test_dispatch_discovery_cross_checks_from_terminal_current_run():
    calls = []

    def runner(argv, *, cwd, env, shell):
        calls.append(argv)
        command = tuple(argv[1:3])
        results = {
            ("orchestration", "run-current"): {"runId": "Other-Run", "terminalHandle": "From"},
            ("orchestration", "run-show"): {"runId": "R"},
            ("orchestration", "task-list"): {"tasks": [{"taskId": "T"}]},
            ("orchestration", "dispatch-show"): {"dispatches": []},
        }
        return 0, __import__("json").dumps({"ok": True, "result": results[command]}), ""

    snapshot = OrcaDiscovery(OrcaAdapter(runner)).discover(
        TypedRequest.from_dict(dispatch_data())
    )
    assert snapshot.facts["state_class"] == "RUNTIME_UNKNOWN"
    assert [call[1:3] for call in calls] == [["orchestration", "run-current"]]


def test_dispatch_completion_evidence_requires_exact_boolean_true():
    def runner(argv, *, cwd, env, shell):
        command = tuple(argv[1:3])
        results = {
            ("orchestration", "run-current"): {"runId": "R"},
            ("orchestration", "run-show"): {"runId": "R"},
            ("orchestration", "task-list"): {"tasks": [{"taskId": "T"}]},
            ("orchestration", "dispatch-show"): {
                "dispatches": [{
                    "dispatchId": "D",
                    "dispatchStatus": "completed",
                    "completionEvidence": "true",
                }]
            },
        }
        return 0, __import__("json").dumps({"ok": True, "result": results[command]}), ""

    snapshot = OrcaDiscovery(OrcaAdapter(runner)).discover(
        TypedRequest.from_dict(dispatch_data())
    )
    assert snapshot.facts["state_class"] == "RUNTIME_UNKNOWN"


def test_worker_start_retry_must_match_latest_dispatch_identity():
    calls = []

    def runner(argv, *, cwd, env, shell):
        calls.append(argv)
        command = tuple(argv[1:3])
        results = {
            ("orchestration", "run-current"): {"runId": "R"},
            ("orchestration", "run-show"): {"runId": "R"},
            ("orchestration", "task-list"): {"tasks": [{"taskId": "T"}]},
            ("orchestration", "dispatch-show"): {
                "dispatches": [{
                    "dispatchId": "Latest-D",
                    "dispatchStatus": "failed",
                    "workerState": "failed",
                }]
            },
        }
        return 0, __import__("json").dumps({"ok": True, "result": results[command]}), ""

    raw = worker_start_request().canonical_invocation()
    args = raw.pop("structured_args")
    args["retry_of"] = "Older-D"
    command = raw.pop("normalized_command")
    raw.pop("normalization_version")
    snapshot = OrcaDiscovery(OrcaAdapter(runner)).discover(
        TypedRequest.from_dict({"command": command, "args": args, "context": raw})
    )
    assert snapshot.facts["state_class"] == "RUNTIME_UNKNOWN"
    assert snapshot.evidence["error_code"] == "DENIED"


def test_new_child_discovery_binds_parent_worktree_and_repo_to_from_terminal():
    calls = []

    def runner(argv, *, cwd, env, shell):
        calls.append(argv)
        command = tuple(argv[1:3])
        results = {
            ("orchestration", "run-current"): {
                "runId": "R",
                "terminalHandle": "From",
                "worktreeId": "Other-Parent",
                "repoId": "Repo-A",
            },
            ("orchestration", "run-show"): {"runId": "R"},
            ("orchestration", "task-list"): {"tasks": [{"taskId": "T"}]},
            ("orchestration", "dispatch-show"): {"dispatches": []},
        }
        return 0, __import__("json").dumps({"ok": True, "result": results[command]}), ""

    data = worker_start_request().canonical_invocation()
    args = data.pop("structured_args")
    args.update(
        worktree_mode="new-child",
        parent_worktree_id="Parent-A",
        repo_id="Repo-A",
        base_branch="Main",
        name="Worker",
    )
    args.pop("resolved_worktree_id")
    args.pop("terminal_handle")
    command = data.pop("normalized_command")
    data.pop("normalization_version")
    snapshot = OrcaDiscovery(OrcaAdapter(runner)).discover(
        TypedRequest.from_dict({"command": command, "args": args, "context": data})
    )
    assert snapshot.facts["state_class"] == "RUNTIME_UNKNOWN"
    assert snapshot.evidence["error_code"] == "DENIED"
    assert len(calls) == 1


def test_new_top_level_discovery_binds_repo_to_from_terminal():
    def runner(argv, *, cwd, env, shell):
        command = tuple(argv[1:3])
        result = {
            ("orchestration", "run-current"): {"runId": "R", "repoId": "Other-Repo"},
            ("orchestration", "run-show"): {"runId": "R"},
            ("orchestration", "task-list"): {"tasks": [{"taskId": "T"}]},
            ("orchestration", "dispatch-show"): {"dispatches": []},
        }[command]
        return 0, __import__("json").dumps({"ok": True, "result": result}), ""

    data = worker_start_request().canonical_invocation()
    args = data.pop("structured_args")
    args.update(
        worktree_mode="new-top-level",
        repo_id="Repo-A",
        base_branch="Main",
        name="Worker",
    )
    args.pop("resolved_worktree_id")
    args.pop("terminal_handle")
    command = data.pop("normalized_command")
    data.pop("normalization_version")
    snapshot = OrcaDiscovery(OrcaAdapter(runner)).discover(
        TypedRequest.from_dict({"command": command, "args": args, "context": data})
    )
    assert snapshot.facts["state_class"] == "RUNTIME_UNKNOWN"


def test_existing_terminal_is_cross_checked_against_exact_worktree():
    calls = []

    def runner(argv, *, cwd, env, shell):
        calls.append(argv)
        command = tuple(argv[1:3])
        if command == ("orchestration", "run-current"):
            terminal = argv[argv.index("--terminal") + 1]
            if terminal == "Target-Terminal":
                result = {
                    "runId": "R",
                    "terminalHandle": terminal,
                    "worktreeId": "Other-Worktree",
                }
            else:
                result = {"runId": "R", "terminalHandle": terminal}
        else:
            result = {
                ("orchestration", "run-show"): {"runId": "R"},
                ("orchestration", "task-list"): {"tasks": [{"taskId": "T"}]},
                ("orchestration", "dispatch-show"): {"dispatches": []},
            }[command]
        return 0, __import__("json").dumps({"ok": True, "result": result}), ""

    data = worker_start_request().canonical_invocation()
    args = data.pop("structured_args")
    args["terminal_handle"] = "Target-Terminal"
    command = data.pop("normalized_command")
    data.pop("normalization_version")
    snapshot = OrcaDiscovery(OrcaAdapter(runner)).discover(
        TypedRequest.from_dict({"command": command, "args": args, "context": data})
    )
    assert snapshot.facts["state_class"] == "RUNTIME_UNKNOWN"
    assert [call[1:3] for call in calls] == [
        ["orchestration", "run-current"],
        ["orchestration", "run-current"],
    ]


def test_discovery_failure_is_total_runtime_unknown_not_benign_absence():
    def runner(argv, *, cwd, env, shell):
        return 1, '{"ok":false,"error":{"code":"unreachable"}}', ""

    snapshot = OrcaDiscovery(OrcaAdapter(runner), clock=lambda: 5).discover(
        TypedRequest.from_dict(dispatch_data())
    )
    assert snapshot.facts["state_class"] == "RUNTIME_UNKNOWN"
    assert snapshot.evidence["error_code"] == "DENIED"


def test_worker_lifecycle_discovery_uses_provenance_bound_dispatch_show():
    calls = []

    def runner(argv, *, cwd, env, shell):
        calls.append(argv)
        result = {
            "dispatches": [
                {
                    "dispatchId": "Dispatch-A",
                    "dispatchStatus": "dispatched",
                    "workerState": "ready",
                }
            ]
        }
        return 0, __import__("json").dumps({"ok": True, "result": result}), ""

    request = TypedRequest.from_dict(
        {
            "command": "orchestration.workerStop",
            "args": {"dispatch_id": "Dispatch-A"},
            "context": context(),
        }
    )
    discovery = OrcaDiscovery(
        OrcaAdapter(runner),
        clock=lambda: 2,
        provenance_resolver=lambda _: {
            "task_id": "Task-A",
            "target_instance_identity": context()["target_instance_identity"],
            "source_contract_sha": context()["source_contract_sha"],
        },
    )
    snapshot = discovery.discover(request)
    assert snapshot.facts["state_class"] == "OBSERVATION_PENDING"
    assert [call[1:3] for call in calls] == [["orchestration", "dispatch-show"]]


def test_worker_discovery_rejects_provenance_from_other_target_instance():
    calls = []

    def runner(argv, *, cwd, env, shell):
        calls.append(argv)
        raise AssertionError("mismatched provenance must prevent discovery RPC")

    request = TypedRequest.from_dict(
        {
            "command": "orchestration.workerStop",
            "args": {"dispatch_id": "Dispatch-A"},
            "context": context(),
        }
    )
    snapshot = OrcaDiscovery(
        OrcaAdapter(runner),
        provenance_resolver=lambda _: {
            "task_id": "Task-A",
            "target_instance_identity": "other-instance",
            "source_contract_sha": context()["source_contract_sha"],
        },
    ).discover(request)
    assert snapshot.facts["state_class"] == "RUNTIME_UNKNOWN"
    assert calls == []


def test_executable_identity_binds_resolved_path_and_file_sha256(tmp_path):
    executable = tmp_path / "orca.exe"
    executable.write_bytes(b"orca-v1")
    digest = __import__("hashlib").sha256(b"orca-v1").hexdigest()
    data = dispatch_data()
    data["context"]["cli_executable_identity"] = f"{executable.resolve()}::sha256:{digest}"
    request = TypedRequest.from_dict(data)
    assert OrcaAdapter().build_argv(request)[0] == str(executable.resolve())
    executable.write_bytes(b"orca-v2")
    with pytest.raises(DeniedError, match="executable identity"):
        OrcaAdapter().build_argv(request)


def test_spawn_failure_is_a_recordable_pre_spawn_deny():
    def unavailable(argv, *, cwd, env, shell):
        raise OSError("missing executable")

    with pytest.raises(DeniedError, match="spawn") as caught:
        OrcaAdapter(unavailable).invoke(
            TypedRequest.from_dict(dispatch_data()), orca_request_id="Req-A"
        )
    assert caught.value.details == {"spawn_started": False, "error_type": "OSError"}


def test_default_runner_receives_independent_process_deadline(monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = '{"ok":true,"result":{"runId":"R"}}'
        stderr = ""

    def run(argv, **kwargs):
        captured.update(kwargs)
        return Completed()

    monkeypatch.setattr("hermes_orca_gate.orca_cli.subprocess.run", run)
    request = TypedRequest.from_dict(
        {"command": "orchestration.runShow", "args": {"run_id": "R"}, "context": context()}
    )
    OrcaAdapter().invoke(request, process_timeout_ms=1234)
    assert captured["timeout"] == pytest.approx(1.234)
