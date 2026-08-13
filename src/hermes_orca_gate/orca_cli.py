"""Mechanical Orca CLI adapter. No caller-provided argv reaches subprocess."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canonical import canonical_digest
from .classifiers import classify_dispatch, classify_worker
from .errors import GateError
from .errors import DeniedError, InputError, OutcomeSchemaError, ReconciliationRequired
from .models import MUTATIONS, TypedRequest

REFUSED_PREFIXES = ("federation",)
REFUSED_COMMANDS = frozenset(
    {
        "orchestration.run",
        "orchestration.runStop",
        "coordinator-start",
        "coordinator-stop",
        "orchestration.reset",
        "orchestration.worker-release",
        "orchestration.worker-retain",
        "orchestration.check",
    }
)


def parse_raw_flags(
    tokens: list[str], schema: dict[str, str], *, command: str = ""
) -> dict[str, str | bool]:
    if command in REFUSED_COMMANDS or command.startswith(REFUSED_PREFIXES):
        raise InputError("command independently refused")
    result: dict[str, str | bool] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not isinstance(token, str) or not token.startswith("--") or token == "--":
            raise InputError("positional input refused")
        flag, equals, inline = token.partition("=")
        if flag == "--on" or flag not in schema:
            raise InputError("flag independently refused")
        if flag in result:
            raise InputError("duplicate flag refused")
        kind = schema[flag]
        if kind == "bool":
            if equals:
                raise InputError("boolean flag cannot take value")
            result[flag] = True
        elif kind == "value":
            if equals:
                if inline == "":
                    raise InputError("missing flag value")
                value = inline
            else:
                index += 1
                if index >= len(tokens) or str(tokens[index]).startswith("--"):
                    raise InputError("missing flag value")
                value = tokens[index]
            result[flag] = value
        else:
            raise InputError("invalid adapter flag schema")
        index += 1
    return result


def subprocess_runner(
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    shell: bool,
    timeout_ms: int | None = None,
) -> tuple[int, str, str]:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        shell=shell,
        text=True,
        encoding="utf-8",  # Orca emits UTF-8; text=True would otherwise decode with the machine locale.
        capture_output=True,
        check=False,
        timeout=None if timeout_ms is None else timeout_ms / 1000,
    )
    return completed.returncode, completed.stdout, completed.stderr


Runner = Callable[..., tuple[int, str, str]]
EXECUTABLE_IDENTITY_MARKER = "::sha256:"


def resolve_executable_identity(identity: str) -> str:
    """Return the bound path after rechecking an optional file SHA-256 identity."""

    if EXECUTABLE_IDENTITY_MARKER not in identity:
        return identity
    path, digest = identity.rsplit(EXECUTABLE_IDENTITY_MARKER, 1)
    if (
        not path
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise DeniedError("invalid Orca executable identity")
    try:
        current = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise DeniedError("Orca executable identity is unavailable") from exc
    if current != digest:
        raise DeniedError("Orca executable identity changed after approval")
    return path


@dataclass(frozen=True)
class DiscoverySnapshot:
    facts: dict[str, str]
    evidence: dict[str, Any]
    observed_mono_ms: int

    @property
    def evidence_digest(self) -> str:
        return canonical_digest(self.evidence)


# Orca spells the same argument differently per subcommand: run-show takes --id, task-list --run.
COMMAND_FLAG_OVERRIDES: dict[str, dict[str, str]] = {
    "orchestration.runShow": {"run_id": "--id"},
    "orchestration.runCurrent": {"terminal_handle": "--from"},
    "orchestration.dispatchShow": {"return_preamble": "--preamble"},
}


class OrcaAdapter:
    def __init__(self, runner: Runner = subprocess_runner):
        self.runner = runner

    def build_argv(
        self, request: TypedRequest, *, orca_request_id: str | None = None
    ) -> list[str]:
        command = request.command
        args = request.args
        prefix = {
            "orchestration.dispatch": ["orchestration", "dispatch"],
            "orchestration.dispatchDryRun": ["orchestration", "dispatch"],
            "orchestration.workerStart": ["orchestration", "worker-start"],
            "orchestration.workerStop": ["orchestration", "worker-stop"],
            "orchestration.workerAbandon": ["orchestration", "worker-abandon"],
            "orchestration.workerShow": ["orchestration", "worker-show"],
            "orchestration.runCurrent": ["orchestration", "run-current"],
            "orchestration.runShow": ["orchestration", "run-show"],
            "orchestration.taskList": ["orchestration", "task-list"],
            "orchestration.dispatchShow": ["orchestration", "dispatch-show"],
            "orchestration.workerRead": ["orchestration", "worker-read"],
            "terminal.read": ["terminal", "read"],
            "terminal.wait": ["terminal", "wait"],
        }[command]
        argv = [
            resolve_executable_identity(request.context["cli_executable_identity"]),
            *prefix,
        ]
        flags = {
            "run_id": "--run",
            "task_id": "--task",
            "to_handle": "--to",
            "from_handle": "--from",
            "dispatch_id": "--dispatch",
            "worktree_mode": "--worktree",
            "parent_worktree_id": "--parent-worktree",
            "repo_id": "--repo",
            "base_branch": "--base",
            "name": "--name",
            "display_name": "--display-name",
            "comment": "--comment",
            "setup": "--setup",
            "agent": "--agent",
            "model": "--model",
            "effort": "--effort",
            "terminal_handle": "--terminal",
            "startup_timeout_ms": "--timeout-ms",
            "retry_of": "--retry-of",
            "cursor": "--cursor",
            "limit": "--limit",
            "source": "--source",
            "status": "--status",
            "condition": "--for",
            "timeout_ms": "--timeout-ms",
        }
        overrides = COMMAND_FLAG_OVERRIDES.get(command, {})
        flags.update(overrides)
        for key, value in args.items():
            if key == "resolved_worktree_id" or value is None:
                continue
            if key == "dry_run":
                if value:
                    argv.append("--dry-run")
            elif key in {"inject", "return_preamble"}:
                if value:
                    argv.append(overrides.get(key, "--" + key.replace("_", "-")))
            elif key in flags:
                argv.extend([flags[key], str(value)])
        if orca_request_id is not None:
            if command not in MUTATIONS:
                raise InputError("retry request is not admitted")
            argv.extend(["--retry-request", orca_request_id])
        argv.append("--json")
        return argv

    def invoke(
        self,
        request: TypedRequest,
        *,
        orca_request_id: str | None = None,
        process_timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        argv = self.build_argv(request, orca_request_id=orca_request_id)
        try:
            runner_kwargs = {
                "cwd": request.context["fixed_or_bound_cwd"],
                "env": dict(request.context["execution_relevant_env"]),
                "shell": False,
            }
            if self.runner is subprocess_runner:
                runner_kwargs["timeout_ms"] = process_timeout_ms
            code, stdout, stderr = self.runner(argv, **runner_kwargs)
        except subprocess.TimeoutExpired as exc:
            raise DeniedError(
                "Orca process exceeded the bound deadline",
                details={"spawn_started": True, "process_deadline_exceeded": True},
            ) from exc
        except OSError as exc:
            raise DeniedError(
                "failed to spawn Orca",
                details={"spawn_started": False, "error_type": type(exc).__name__},
            ) from exc
        try:
            envelope = json.loads(stdout)
        except (ValueError, TypeError) as exc:
            if request.command in MUTATIONS or request.command == "orchestration.workerShow":
                raise ReconciliationRequired(
                    "malformed Orca JSON envelope", details={"exit_code": code}
                ) from exc
            raise DeniedError("malformed Orca JSON envelope") from exc
        if code != 0 or not isinstance(envelope, dict) or envelope.get("ok") is not True:
            error = envelope.get("error") if isinstance(envelope, dict) else None
            error_code = error.get("code") if isinstance(error, dict) else None
            if request.command == "orchestration.workerStart" and error_code == "operation_unknown":
                data = error.get("data")
                data = data if isinstance(data, dict) else {}
                accepted = data.get("accepted")
                dispatch_id = (
                    accepted.get("dispatchId") if isinstance(accepted, dict) else None
                )
                details: dict[str, Any] = {
                    "kind": "operation_unknown",
                    "exit_code": code,
                    "envelope": envelope,
                }
                if isinstance(dispatch_id, str) and dispatch_id:
                    details["accepted_dispatch_id"] = dispatch_id
                raise ReconciliationRequired(
                    "worker-start outcome is unknown", details=details
                )
            if request.command == "orchestration.workerStop" and error_code == "stop_unknown":
                raise ReconciliationRequired(
                    "worker-stop outcome is unknown",
                    details={
                        "kind": "stop_unknown",
                        "exit_code": code,
                        "envelope": envelope,
                    },
                )
            raise DeniedError(
                "Orca did not report mechanical success",
                details={
                    "exit_code": code,
                    "error": error if isinstance(envelope, dict) else stderr,
                },
            )
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise OutcomeSchemaError("missing result object")
        if request.command in MUTATIONS:
            try:
                self.validate_outcome(request.command, result)
                self._validate_outcome_correlation(request, result)
            except ReconciliationRequired as exc:
                exc.details.update({"exit_code": code, "envelope": envelope})
                raise
        if request.command in {
            "orchestration.workerStop",
            "orchestration.workerAbandon",
        } and not self.fresh_containment(request.command, result):
            raise DeniedError(
                "Orca result did not establish fresh containment",
                details={
                    "exit_code": code,
                    "envelope": envelope,
                    "result": result,
                    "effect_claim": "no_new_effect",
                },
            )
        return {"exit_code": code, "result": result, "envelope": envelope}

    @staticmethod
    def _validate_outcome_correlation(
        request: TypedRequest, result: dict[str, Any]
    ) -> None:
        mismatches: dict[str, Any] = {}
        if request.command in {
            "orchestration.workerStop",
            "orchestration.workerAbandon",
        } and result.get("dispatchId") != request.args["dispatch_id"]:
            mismatches["dispatchId"] = result.get("dispatchId")
        if request.command in {
            "orchestration.dispatch",
            "orchestration.workerStart",
        }:
            for result_key, argument_key in (("runId", "run_id"), ("taskId", "task_id")):
                if result_key in result and result[result_key] != request.args[argument_key]:
                    mismatches[result_key] = result[result_key]
        if request.command == "orchestration.workerStart":
            mode = request.args["worktree_mode"]
            expected_worktree = request.args.get("resolved_worktree_id")
            if mode not in {"new-child", "new-top-level"} and "worktreeId" in result and (
                result.get("worktreeId") != expected_worktree
            ):
                mismatches["worktreeId"] = result.get("worktreeId")
            for result_key, argument_key in (
                ("repoId", "repo_id"),
                ("baseBranch", "base_branch"),
                ("name", "name"),
                ("terminalHandle", "terminal_handle"),
            ):
                if (
                    argument_key in request.args
                    and result_key in result
                    and result[result_key] != request.args[argument_key]
                ):
                    mismatches[result_key] = result.get(result_key)
        if mismatches:
            raise ReconciliationRequired(
                "mutation outcome identity does not match approved request",
                details={"mismatches": mismatches},
            )

    @staticmethod
    def validate_outcome(command: str, result: dict[str, Any]) -> dict[str, Any]:
        required_by_command = {
            "orchestration.dispatch": {"dispatchId", "state"},
            "orchestration.workerStart": {
                "runId",
                "taskId",
                "dispatchId",
                "state",
                "effects",
                "residualResources",
                "startupTimeoutMs",
            },
            "orchestration.workerStop": {
                "dispatchId",
                "state",
                "alreadySettled",
                "processAction",
            },
            "orchestration.workerAbandon": {
                "dispatchId",
                "state",
                "alreadySettled",
                "stale",
                "processAction",
                "residualResources",
            },
        }
        required = required_by_command.get(command)
        if required is None:
            raise OutcomeSchemaError("unknown mutation outcome schema")
        if not isinstance(result, dict) or not required <= set(result):
            raise OutcomeSchemaError("required outcome fields missing")
        if not isinstance(result["dispatchId"], str) or not result["dispatchId"]:
            raise OutcomeSchemaError("dispatchId must be non-empty")
        if not isinstance(result["state"], str) or not result["state"]:
            raise OutcomeSchemaError("state must be non-empty")
        if command == "orchestration.dispatch":
            return result
        if command == "orchestration.workerStart":
            if (
                not all(isinstance(result[key], str) and result[key] for key in ("runId", "taskId"))
                or not isinstance(result["effects"], list)
                or not isinstance(result["residualResources"], list)
                or type(result["startupTimeoutMs"]) is not int
                or result["startupTimeoutMs"] <= 0
            ):
                raise OutcomeSchemaError("invalid worker-start result fields")
            return result
        if command == "orchestration.workerStop" and "stale" in result:
            raise OutcomeSchemaError("worker-stop must not contain stale")
        if type(result["alreadySettled"]) is not bool:
            raise OutcomeSchemaError("alreadySettled must be boolean")
        if not isinstance(result["processAction"], str):
            raise OutcomeSchemaError("processAction must be a string")
        if command == "orchestration.workerAbandon":
            if type(result["stale"]) is not bool or not isinstance(
                result["residualResources"], list
            ):
                raise OutcomeSchemaError("invalid abandon result fields")
        return result

    @staticmethod
    def fresh_containment(command: str, result: dict[str, Any]) -> bool:
        OrcaAdapter.validate_outcome(command, result)
        if result["alreadySettled"] is not False or result["processAction"] in (
            None,
            "",
            "none",
        ):
            return False
        if command == "orchestration.workerStop":
            return bool(result.get("closeEvidence"))
        return result["stale"] is False and isinstance(result["residualResources"], list)


class OrcaDiscovery:
    """Runs only admitted read/preview commands and returns total classified facts."""

    def __init__(
        self,
        adapter: OrcaAdapter,
        *,
        clock: Callable[[], int] | None = None,
        provenance_resolver: Callable[[str], dict[str, Any]] | None = None,
    ):
        self.adapter = adapter
        self.clock = clock or (lambda: time.monotonic_ns() // 1_000_000)
        self.provenance_resolver = provenance_resolver

    def discover(self, request: TypedRequest) -> DiscoverySnapshot:
        observed = self.clock()
        facts = self._base_facts(request)
        evidence: dict[str, Any] = {"normalized_command": request.command}
        try:
            if request.command in {
                "orchestration.dispatch",
                "orchestration.workerStart",
                "orchestration.dispatchDryRun",
            }:
                state, discovered = self._discover_task_lineage(request)
                evidence.update(discovered)
                facts["state_class"] = state
            elif request.command in {
                "orchestration.workerStop",
                "orchestration.workerAbandon",
                "orchestration.workerShow",
                "orchestration.workerRead",
            }:
                state, discovered = self._discover_worker(request)
                facts["state_class"] = state
                evidence.update(discovered)
            else:
                evidence["execution_deferred"] = True
                facts["state_class"] = "OK"
        except GateError as exc:
            facts["state_class"] = "RUNTIME_UNKNOWN"
            evidence = {
                "normalized_command": request.command,
                "error_code": exc.code,
                "error_details": exc.details,
            }
        return DiscoverySnapshot(facts=facts, evidence=evidence, observed_mono_ms=observed)

    def execute_admitted(self, request: TypedRequest) -> dict[str, Any]:
        return self.adapter.invoke(request)

    @staticmethod
    def _base_facts(request: TypedRequest) -> dict[str, str]:
        action = "mutate" if request.command in MUTATIONS else "read"
        if request.command == "orchestration.dispatchDryRun":
            action = "preview"
        return {
            "normalized_command": request.command,
            "state_class": "RUNTIME_UNKNOWN",
            "target_instance_identity": request.context["target_instance_identity"],
            "repo_id": str(request.args.get("repo_id", "")),
            "context_digest": canonical_digest(
                {
                    "effective_target_identity": request.context[
                        "effective_target_identity"
                    ],
                    "cwd": request.context["fixed_or_bound_cwd"],
                    "env": request.context["execution_relevant_env"],
                }
            ),
            "approved_action": action,
        }

    def _read_request(
        self, request: TypedRequest, command: str, args: dict[str, Any]
    ) -> TypedRequest:
        context = dict(request.context)
        context.pop("mutation_intent_identity_or_null", None)
        return TypedRequest.from_dict(
            {"command": command, "args": args, "context": context}
        )

    def _discover_task_lineage(
        self, request: TypedRequest
    ) -> tuple[str, dict[str, Any]]:
        run_id = request.args["run_id"]
        task_id = request.args["task_id"]
        from_handle = request.args["from_handle"]
        current = self.adapter.invoke(
            self._read_request(
                request,
                "orchestration.runCurrent",
                {"terminal_handle": from_handle},
            )
        )["result"]
        if current.get("runId") != run_id or (
            "terminalHandle" in current
            and current.get("terminalHandle") != from_handle
        ):
            raise DeniedError("from terminal is not bound to the explicit run")
        if request.command == "orchestration.workerStart" and request.args[
            "worktree_mode"
        ] in {"new-child", "new-top-level"}:
            if current.get("repoId") != request.args["repo_id"]:
                raise DeniedError("new worktree repo does not match from terminal")
            if (
                request.args["worktree_mode"] == "new-child"
                and current.get("worktreeId")
                != request.args["parent_worktree_id"]
            ):
                raise DeniedError(
                    "new-child parent worktree/repo does not match from terminal"
                )
        target_terminal = None
        if (
            request.command == "orchestration.workerStart"
            and request.args["worktree_mode"] not in {"new-child", "new-top-level"}
            and "terminal_handle" in request.args
        ):
            target_terminal = self.adapter.invoke(
                self._read_request(
                    request,
                    "orchestration.runCurrent",
                    {"terminal_handle": request.args["terminal_handle"]},
                )
            )["result"]
            if (
                target_terminal.get("runId") != run_id
                or target_terminal.get("terminalHandle")
                != request.args["terminal_handle"]
                or target_terminal.get("worktreeId")
                != request.args["resolved_worktree_id"]
            ):
                raise DeniedError(
                    "existing terminal is not bound to the approved run/worktree"
                )
        run = self.adapter.invoke(
            self._read_request(request, "orchestration.runShow", {"run_id": run_id})
        )["result"]
        if run.get("runId") != run_id:
            raise DeniedError("run discovery identity mismatch")
        tasks = self.adapter.invoke(
            self._read_request(request, "orchestration.taskList", {"run_id": run_id})
        )["result"]
        task_rows = tasks.get("tasks")
        if not isinstance(task_rows, list):
            raise DeniedError("task discovery result is malformed")
        task = next(
            (
                row
                for row in task_rows
                if isinstance(row, dict) and row.get("taskId") == task_id
            ),
            None,
        )
        if task is None:
            raise DeniedError("task is absent from the explicit run")
        dispatches_result = self.adapter.invoke(
            self._read_request(
                request,
                "orchestration.dispatchShow",
                {
                    "task_id": task_id,
                    "from_handle": request.args["from_handle"],
                    "return_preamble": False,
                },
            )
        )["result"]
        dispatches = dispatches_result.get("dispatches")
        if not isinstance(dispatches, list):
            raise DeniedError("dispatch lineage result is malformed")
        state = "OK"
        if dispatches:
            latest = dispatches[-1]
            if not isinstance(latest, dict):
                raise DeniedError("dispatch lineage row is malformed")
            retry_of = request.args.get("retry_of")
            if retry_of is not None and latest.get("dispatchId") != retry_of:
                raise DeniedError("retry_of is not the latest applicable dispatch")
            if request.command == "orchestration.workerStart" and "workerState" in latest:
                state = classify_worker(
                    latest.get("dispatchStatus"),
                    latest.get("workerState"),
                    completion_evidence=latest.get("completionEvidence") is True,
                )
            else:
                state = classify_dispatch(
                    latest.get("dispatchStatus"),
                    completion_evidence=latest.get("completionEvidence") is True,
                )
        return state, {
            "current": current,
            "target_terminal": target_terminal,
            "run": run,
            "task": task,
            "dispatches": dispatches,
        }

    def _discover_worker(
        self, request: TypedRequest
    ) -> tuple[str, dict[str, Any]]:
        if self.provenance_resolver is None:
            raise DeniedError("trusted local provenance resolver is unavailable")
        dispatch_id = request.args["dispatch_id"]
        provenance = self.provenance_resolver(dispatch_id)
        if (
            provenance.get("target_instance_identity")
            != request.context["target_instance_identity"]
            or provenance.get("source_contract_sha")
            != request.context["source_contract_sha"]
        ):
            raise DeniedError("provenance source or target identity mismatch")
        task_id = provenance.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise DeniedError("provenance lacks task identity")
        dispatch_request = self._read_request(
            request,
            "orchestration.dispatchShow",
            {"task_id": task_id},
        )
        result = self.adapter.invoke(dispatch_request)["result"]
        dispatches = result.get("dispatches")
        if not isinstance(dispatches, list):
            raise DeniedError("dispatch state result is malformed")
        row = next(
            (
                item
                for item in dispatches
                if isinstance(item, dict) and item.get("dispatchId") == dispatch_id
            ),
            None,
        )
        if row is None:
            raise DeniedError("provenance dispatch is absent from task state")
        state = classify_worker(
            row.get("dispatchStatus"),
            row.get("workerState"),
            worker_present=isinstance(row.get("workerState"), str),
            completion_evidence=row.get("completionEvidence") is True,
        )
        evidence: dict[str, Any] = {"dispatch": row}
        return state, evidence
