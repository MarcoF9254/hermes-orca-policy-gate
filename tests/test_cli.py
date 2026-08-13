import json
import sys
from hermes_orca_gate.cli import _parser, _runtime_context, main
from hermes_orca_gate.orca_cli import DiscoverySnapshot
from hermes_orca_gate.orca_cli import OrcaAdapter as RealOrcaAdapter
from hermes_orca_gate.stores import GateStore
from hermes_orca_gate.models import ApprovedInvocation
from hermes_orca_gate.service import ObservationController
from test_policy import policy_data


def test_validate_policy_compact_json_and_exit_codes(tmp_path, capsys):
    valid = tmp_path / 'valid.json'; valid.write_text(json.dumps(policy_data()), encoding='utf-8')
    assert main(['validate-policy', '--policy', str(valid)]) == 0
    output = capsys.readouterr().out.strip(); assert json.loads(output)['code'] == 'POLICY_VALID' and '\n' not in output
    invalid = tmp_path / 'invalid.json'; invalid.write_text('{}', encoding='utf-8')
    assert main(['validate-policy', '--policy', str(invalid)]) == 2
    assert json.loads(capsys.readouterr().out)['code'] == 'POLICY_LOAD_ERROR'


def test_init_state_and_status_are_usable(tmp_path, capsys):
    state = tmp_path / 'state.db'
    assert main(['init-state', '--state', str(state)]) == 0
    assert json.loads(capsys.readouterr().out)['code'] == 'STATE_INITIALIZED'
    assert main(['status', '--state', str(state)]) == 0
    status = json.loads(capsys.readouterr().out); assert status['code'] == 'STATUS' and status['result']['open_reconciliations'] == 0


def test_cli_startup_sweep_precedes_status_and_ordinary_paths(tmp_path, capsys):
    state = tmp_path / "state.db"
    store = GateStore(state)
    store.initialize()
    store.append_decision("Dec-A", {"action": "allow"})
    store.append_approved_invocation(
        ApprovedInvocation(
            operation_id="Op-A",
            approval_kind="policy_allow",
            decision_event_id="Dec-A",
            normalized_command="orchestration.dispatch",
            args_digest="args",
            policy_sha256="policy",
            source_contract_sha="403c60bbadb734d870a22c372aca8a988e348d21",
            evidence_digest="evidence",
            evidence_observed_mono_ms=1,
            evidence_process_epoch_id="epoch",
            cli_executable_identity="orca",
            credential_binding_id="cred",
        )
    )
    assert store.consume("Op-A")
    assert main(["status", "--state", str(state)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["result"]["open_reconciliations"] == 1


def test_cli_invalid_invocation_returns_two(capsys):
    assert main(['init-state']) == 2
    assert json.loads(capsys.readouterr().out)['code'] == 'INVALID_INVOCATION'


def test_preflight_cli_exposes_only_explicit_receipt_recovery_selector():
    options = _parser().parse_args([
        'preflight', '--policy', 'p', '--state', 's', '--request', 'r',
        '--recover-intent', 'Intent-A',
    ])
    assert options.recover_intent == 'Intent-A'


def test_cli_owner_reconciliation_cannot_override_block(tmp_path, capsys):
    state = tmp_path / "state.db"
    store = GateStore(state)
    store.initialize()
    store.append_decision(
        "Blocked-A",
        {
            "action": "block",
            "normalized_command": "orchestration.workerStop",
            "args_digest": "digest",
            "state_class": "STOPPED",
        },
    )
    store.mark_reconciliation("dispatch:D", "unknown")
    assert main(
        [
            "owner-authorize",
            "--state",
            str(state),
            "--decision",
            "Blocked-A",
            "--kind",
            "owner_reconciliation",
            "--scope",
            "dispatch:D",
        ]
    ) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "DENIED"


def test_cli_owner_authorization_persists_issued_at_and_selected_observation_deadline(
    tmp_path, capsys
):
    state = tmp_path / "state.db"
    store = GateStore(state)
    store.initialize()
    store.append_decision(
        "Observe-A",
        {
            "action": "require_approval",
            "normalized_command": "observation.continue",
            "args_digest": "digest",
            "state_class": "OBSERVATION_PENDING",
            "observation_scope": "dispatch:Dispatch-A",
        },
    )
    deadline = __import__("time").monotonic_ns() // 1_000_000 + 60_000
    assert main([
        "owner-authorize",
        "--state", str(state),
        "--decision", "Observe-A",
        "--kind", "owner_approval",
        "--observation-deadline-ms", str(deadline),
    ]) == 0
    authorization_id = json.loads(capsys.readouterr().out)["result"]["authorization_id"]
    authorization = store.get_owner_authorization(authorization_id)
    assert authorization["issued_at"]
    assert authorization["observation_scope"] == "dispatch:Dispatch-A"
    assert authorization["observation_deadline_ms"] == deadline


def test_cli_manual_reconciliation_is_explicitly_labeled(tmp_path, capsys):
    state = tmp_path / "state.db"
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({
        "evidence_kind": "independently_sourced_owner_evidence",
        "scope": "operation:Op-A",
        "finding": "no_effect_confirmed",
        "source_contract_sha": "403c60bbadb734d870a22c372aca8a988e348d21",
    }), encoding="utf-8")
    store = GateStore(state)
    store.initialize()
    store.mark_reconciliation("operation:Op-A", "unknown")
    store.append_decision(
        "Recon-A",
        {
            "action": "reconciliation_required",
            "normalized_command": "orchestration.workerShow",
            "args_digest": "digest",
                "state_class": "RUNTIME_UNKNOWN",
                "reconciliation_scope": "operation:Op-A",
            },
    )
    store.append_owner_authorization(
        "Auth-A",
        "Recon-A",
        {
            "authorization_kind": "owner_reconciliation",
            "exact_action": "orchestration.workerShow",
            "args_digest": "digest",
            "approved_state_class": "RUNTIME_UNKNOWN",
                "mutation_intent_id": None,
                "reconciliation_scope": "operation:Op-A",
                "reconciliation_action": "manual_break_glass",
            },
    )
    assert main(
        [
            "reconcile",
            "--state",
            str(state),
            "--scope",
            "operation:Op-A",
            "--authorization",
            "Auth-A",
            "--evidence",
            str(evidence),
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "RECONCILIATION_CLOSED_MANUAL"


def test_cli_approval_lifecycle_reconstructs_gate_created_intent(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("ORCA_USER_DATA_PATH", str(tmp_path / "orca-data"))
    monkeypatch.setenv("ORCA_DEV_CLI_INVOCATION", "0")
    monkeypatch.setenv("HERMES_ORCA_GATE_CREDENTIAL_BINDING_ID", "cred-v1")
    monkeypatch.setenv("HERMES_ORCA_GATE_PROCESS_EPOCH_ID", "epoch-A")
    monkeypatch.setattr("hermes_orca_gate.cli.shutil.which", lambda _: sys.executable)

    class Discovery:
        def __init__(self, adapter, **_):
            self.adapter = adapter

        def discover(self, request):
            return DiscoverySnapshot(
                facts={
                    "normalized_command": request.command,
                    "state_class": "OK",
                },
                evidence={"source": "fake-runner"},
                observed_mono_ms=1000,
            )

    monkeypatch.setattr("hermes_orca_gate.cli.OrcaDiscovery", Discovery)
    state = tmp_path / "state.db"
    policy_path = tmp_path / "policy.json"
    data = policy_data()
    data["rules"][0]["match"] = {
        "normalized_command": "orchestration.dispatch",
        "state_class": "OK",
    }
    policy_path.write_text(json.dumps(data), encoding="utf-8")
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "command": "orchestration.dispatch",
                "args": {
                    "run_id": "R",
                    "task_id": "T",
                    "to_handle": "To",
                    "from_handle": "From",
                    "inject": False,
                    "return_preamble": False,
                    "dry_run": False,
                },
            }
        ),
        encoding="utf-8",
    )
    common = ["--policy", str(policy_path), "--state", str(state)]
    assert main(["preflight", *common, "--request", str(request_path)]) == 0
    preflight = json.loads(capsys.readouterr().out)["result"]
    assert preflight["decision"] == "require_approval"
    assert main(
        [
            "owner-authorize",
            "--state",
            str(state),
            "--decision",
            preflight["decision_event_id"],
            "--kind",
            "owner_approval",
        ]
    ) == 0
    authorization_id = json.loads(capsys.readouterr().out)["result"]["authorization_id"]
    assert main(
        [
            "resolve-approval",
            *common,
            "--authorization",
            authorization_id,
            "--request",
            str(request_path),
        ]
    ) == 0
    resolved = json.loads(capsys.readouterr().out)
    assert resolved["code"] == "APPROVAL_RESOLVED"
    store = GateStore(state)
    assert store.get_owner_authorization(authorization_id)["exact_action"] == "orchestration.dispatch"
    invocation = store.get_approved_invocation(resolved["result"]["operation_id"])
    assert invocation["mutation_intent_id"]
    assert invocation["orca_request_id"]


def test_cli_observe_executes_probe_instead_of_accepting_probe_claims(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("ORCA_USER_DATA_PATH", str(tmp_path / "orca-data"))
    monkeypatch.setenv("ORCA_DEV_CLI_INVOCATION", "0")
    monkeypatch.setenv("HERMES_ORCA_GATE_CREDENTIAL_BINDING_ID", "cred-v1")
    monkeypatch.setenv("HERMES_ORCA_GATE_PROCESS_EPOCH_ID", "epoch-A")
    monkeypatch.setattr("hermes_orca_gate.cli.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("hermes_orca_gate.cli.time.monotonic_ns", lambda: 1_000_000)
    calls = []

    def runner(argv, *, cwd, env, shell):
        calls.append(argv)
        result = (
            {"cursor": 1, "output": ""}
            if argv[1:3] == ["terminal", "read"]
            else {"satisfied": True}
        )
        return 0, json.dumps({"ok": True, "result": result}), ""

    monkeypatch.setattr(
        "hermes_orca_gate.cli.OrcaAdapter", lambda: RealOrcaAdapter(runner)
    )
    monkeypatch.setattr(
        ObservationController,
        "run",
        lambda self, dispatch_id: self.observe(dispatch_id),
    )
    state = tmp_path / "state.db"
    store = GateStore(state)
    store.initialize()
    runtime_context = _runtime_context()
    store.record_provenance(
        "Dispatch-A",
        {
            "origin": "local",
            "created_by_operation_id": "Op-A",
            "terminal_handle": "Terminal-A",
            "worker_started_mono_ms": 0,
            "evidence_process_epoch_id": "epoch-A",
            "target_instance_identity": runtime_context["target_instance_identity"],
            "source_contract_sha": runtime_context["source_contract_sha"],
        },
    )
    assert main(
        [
            "observe",
            "--policy",
            "config/policy.example.json",
            "--state",
            str(state),
            "--dispatch",
            "Dispatch-A",
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["result"]["route"] == "observe"
    assert len(calls) == 2
