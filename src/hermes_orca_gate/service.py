"""Deterministic preflight, approval, execution, and observation services."""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .canonical import canonical_digest
from .errors import (
    AuditabilityLost,
    DeniedError,
    GateError,
    InputError,
    PersistenceError,
    ReconciliationRequired,
)
from .models import ApprovedInvocation, DISPATCH_TARGETING, GATED, MUTATIONS, TypedRequest
from .orca_cli import OrcaAdapter
from .policy import Policy
from .stores import GateStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifier(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def _matching_local_provenance(
    store: GateStore, dispatch_id: str, context: dict[str, Any]
) -> dict[str, Any]:
    record = store.require_local_provenance(dispatch_id)
    if (
        record.get("source_contract_sha") != context["source_contract_sha"]
        or record.get("target_instance_identity")
        != context["target_instance_identity"]
    ):
        scope = f"dispatch:{dispatch_id}"
        store.mark_reconciliation(scope, "PROVENANCE_IDENTITY_MISMATCH")
        raise ReconciliationRequired(
            "provenance source/target identity mismatch",
            details={"scope": scope},
        )
    return record


class StaticIdentityResolver:
    """A controlled, injectable source for the effective execution identity."""

    def __init__(self, context: dict[str, Any]):
        self._context = copy.deepcopy(context)
        self._context.pop("mutation_intent_identity_or_null", None)

    def resolve(self) -> dict[str, Any]:
        return copy.deepcopy(self._context)

    def verify(self, request: TypedRequest, *, allow_intent: bool) -> None:
        supplied = copy.deepcopy(request.context)
        intent = supplied.pop("mutation_intent_identity_or_null", None)
        if supplied != self._context:
            raise DeniedError("trusted execution identity mismatch")
        if intent is not None and not allow_intent:
            raise DeniedError("caller-supplied mutation identity is not authority")


@dataclass
class ObservationSession:
    """One-in-flight bounded terminal probe session."""

    started_ms: int
    deadline_ms: int
    qualifying_checkpoints: int
    consecutive_idle: int = 0
    probe_in_flight: bool = False
    runtime_unknown: bool = False
    approved_invocation_id: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.started_ms) is not int
            or type(self.deadline_ms) is not int
            or self.deadline_ms <= self.started_ms
            or type(self.qualifying_checkpoints) is not int
            or self.qualifying_checkpoints <= 0
        ):
            raise InputError("invalid observation session bounds")

    def begin_probe(self) -> bool:
        if self.probe_in_flight:
            return False
        self.probe_in_flight = True
        return True

    def finish_probe(self, result: object, *, new_output: bool, now_ms: int) -> str:
        if not self.probe_in_flight:
            raise InputError("no probe is in flight")
        self.probe_in_flight = False
        if now_ms >= self.deadline_ms:
            self.consecutive_idle = 0
            return "require_approval"
        if not isinstance(result, dict):
            return self._unknown()
        error = result.get("error")
        if isinstance(error, dict):
            if error.get("code") == "timeout" and set(error) <= {"code", "message"}:
                self.consecutive_idle = 0
                return "observe"
            return self._unknown()
        if error is not None:
            return self._unknown()
        if (
            result.get("satisfied") is False
            and isinstance(result.get("blockedReason"), str)
            and result["blockedReason"]
        ):
            self.consecutive_idle = 0
            self.approved_invocation_id = None
            return "require_approval"
        if result.get("satisfied") is not True:
            return self._unknown()
        if new_output:
            self.consecutive_idle = 0
            return "observe"
        self.consecutive_idle += 1
        return (
            "require_approval"
            if self.consecutive_idle >= self.qualifying_checkpoints
            else "observe"
        )

    def deadline_route(self, now_ms: int) -> str:
        if now_ms >= self.deadline_ms:
            self.consecutive_idle = 0
            return "require_approval"
        return "observe"

    def continue_after_ceiling(self, new_deadline_ms: int | None, now_ms: int) -> None:
        if (
            type(new_deadline_ms) is not int
            or new_deadline_ms <= now_ms
            or now_ms < self.deadline_ms
        ):
            raise InputError("a new Owner-selected future deadline is required")
        self.started_ms = now_ms
        self.deadline_ms = new_deadline_ms
        self.consecutive_idle = 0
        self.probe_in_flight = False
        self.runtime_unknown = False
        self.approved_invocation_id = None

    def _unknown(self) -> str:
        self.consecutive_idle = 0
        self.runtime_unknown = True
        self.approved_invocation_id = None
        return "RUNTIME_UNKNOWN"


class ObservationController:
    """Executes one durable serial probe for a proven-local worker."""

    def __init__(
        self,
        store: GateStore,
        policy: Policy,
        adapter: OrcaAdapter,
        *,
        resolver: StaticIdentityResolver,
        epoch_id: str,
        clock: Any,
    ):
        self.store = store
        self.policy = policy
        self.adapter = adapter
        self.resolver = resolver
        self.epoch_id = epoch_id
        self.clock = clock

    def run(self, dispatch_id: str, *, sleeper: Any = time.sleep) -> dict[str, Any]:
        while True:
            result = self.observe(dispatch_id)
            route = result["route"]
            if route == "observe":
                now_ms = self.clock()
                delay_ms = min(
                    self.policy.data["observation"]["interval_ms"],
                    max(0, result["deadline_ms"] - now_ms),
                )
                sleeper(delay_ms / 1000)
                continue
            if route == "interval_pending":
                now_ms = self.clock()
                delay_ms = min(
                    max(0, result["next_probe_ms"] - now_ms),
                    max(0, result["deadline_ms"] - now_ms),
                )
                sleeper(delay_ms / 1000)
                continue
            if route == "probe_in_flight":
                sleeper(1.0)
                continue
            return result

    def continue_observation(
        self, dispatch_id: str, authorization_id: str
    ) -> None:
        scope = f"dispatch:{dispatch_id}"
        authorization = self.store.get_owner_authorization(authorization_id)
        if (
            not authorization
            or authorization.get("authorization_kind") != "owner_approval"
            or authorization.get("exact_action") != "observation.continue"
            or authorization.get("observation_scope") != scope
            or type(authorization.get("observation_deadline_ms")) is not int
        ):
            raise DeniedError(
                "exact Owner-selected observation continuation is required"
            )
        self.store.continue_observation(
            scope,
            authorization_id=authorization_id,
            new_deadline_ms=authorization["observation_deadline_ms"],
            now_ms=self.clock(),
        )

    def observe(self, dispatch_id: str) -> dict[str, Any]:
        provenance = _matching_local_provenance(
            self.store, dispatch_id, self.resolver.resolve()
        )
        terminal = provenance.get("terminal_handle")
        started_ms = provenance.get("worker_started_mono_ms")
        provenance_epoch = provenance.get("evidence_process_epoch_id")
        if (
            not isinstance(terminal, str)
            or not terminal
            or type(started_ms) is not int
            or provenance_epoch != self.epoch_id
        ):
            raise ReconciliationRequired(
                "bound worker observation identity is unavailable",
                details={"scope": f"dispatch:{dispatch_id}"},
            )
        observation = self.policy.data["observation"]
        scope = f"dispatch:{dispatch_id}"
        now_ms = self.clock()
        state = self.store.begin_observation_probe(
            scope,
            started_ms=started_ms,
            deadline_ms=started_ms + observation["default_ceiling_ms"],
            epoch_id=self.epoch_id,
            now_ms=now_ms,
        )
        if state is None:
            persisted = self.store.get_observation_state(scope) or {}
            return {
                "route": "probe_in_flight",
                "consecutive_idle": 0,
                "cursor": None,
                "deadline_ms": persisted.get("deadline_ms"),
            }
        if state.get("deferred"):
            return {
                "route": "interval_pending",
                "consecutive_idle": state["consecutive_idle"],
                "cursor": state["cursor"],
                "next_probe_ms": state["next_probe_ms"],
                "deadline_ms": state["deadline_ms"],
            }
        session = ObservationSession(
            state["started_ms"],
            state["deadline_ms"],
            observation["qualifying_checkpoints"],
            consecutive_idle=state["consecutive_idle"],
            probe_in_flight=True,
        )
        cursor = state["cursor"] if state["cursor"] is not None else 0
        if now_ms >= session.deadline_ms:
            session.probe_in_flight = False
            route = session.deadline_route(now_ms)
            return self._finish(scope, session, cursor, route, now_ms)
        context = self.resolver.resolve()
        wait_request = TypedRequest.from_dict(
            {
                "command": "terminal.wait",
                "args": {
                    "terminal_handle": terminal,
                    "condition": "tui-idle",
                    "timeout_ms": observation["probe_timeout_ms"],
                },
                "context": context,
            }
        )
        try:
            wait_result = self.adapter.invoke(
                wait_request,
                process_timeout_ms=max(1, session.deadline_ms - now_ms),
            )["result"]
        except DeniedError as exc:
            error = exc.details.get("error")
            wait_result = {
                "error": {
                    "code": error.get("code")
                    if isinstance(error, dict)
                    else exc.code
                }
            }
        except GateError as exc:
            wait_result = {"error": {"code": exc.code}}
        after_wait_ms = self.clock()
        if after_wait_ms >= session.deadline_ms:
            session.probe_in_flight = False
            route = session.deadline_route(after_wait_ms)
            return self._finish(scope, session, cursor, route, after_wait_ms)
        if not isinstance(wait_result, dict) or wait_result.get("satisfied") is not True:
            route = session.finish_probe(
                wait_result, new_output=False, now_ms=after_wait_ms
            )
            return self._finish(scope, session, cursor, route, after_wait_ms)
        read_request = TypedRequest.from_dict(
            {
                "command": "terminal.read",
                "args": {"terminal_handle": terminal, "cursor": cursor, "limit": 1000},
                "context": context,
            }
        )
        try:
            read_result = self.adapter.invoke(
                read_request,
                process_timeout_ms=max(1, session.deadline_ms - after_wait_ms),
            )["result"]
        except GateError as exc:
            completed_ms = self.clock()
            route = session.finish_probe(
                {"error": {"code": exc.code}},
                new_output=False,
                now_ms=completed_ms,
            )
            return self._finish(scope, session, cursor, route, completed_ms)
        completed_ms = self.clock()
        next_cursor = read_result.get("cursor")
        output = read_result.get("output")
        if completed_ms >= session.deadline_ms:
            session.probe_in_flight = False
            route = session.deadline_route(completed_ms)
            return self._finish(scope, session, next_cursor or cursor, route, completed_ms)
        if not isinstance(next_cursor, (str, int)) or not isinstance(output, str):
            route = session.finish_probe({}, new_output=False, now_ms=completed_ms)
            return self._finish(scope, session, cursor, route, completed_ms)
        route = session.finish_probe(
            wait_result, new_output=bool(output), now_ms=completed_ms
        )
        return self._finish(scope, session, next_cursor, route, completed_ms)

    def _finish(
        self,
        scope: str,
        session: ObservationSession,
        cursor: Any,
        route: str,
        now_ms: int,
    ) -> dict[str, Any]:
        result = {
            "route": route,
            "consecutive_idle": session.consecutive_idle,
            "cursor": cursor,
            "deadline_ms": session.deadline_ms,
        }
        if route == "require_approval":
            decision_id = _identifier("observation-decision")
            decision = {
                "action": "require_approval",
                "normalized_command": "observation.continue",
                "args_digest": canonical_digest(
                    {"scope": scope, "expired_deadline_ms": session.deadline_ms}
                ),
                "state_class": "OBSERVATION_PENDING",
                "observation_scope": scope,
                "expired_deadline_ms": session.deadline_ms,
                "policy_sha256": self.policy.sha256,
                "source_contract_sha": self.policy.data["source_contract_sha"],
                "recorded_at": _now(),
            }
            self.store.append_decision(decision_id, decision)
            result["decision_event_id"] = decision_id
        self.store.finish_observation_probe(
            scope,
            consecutive_idle=session.consecutive_idle,
            cursor=cursor,
            payload={**result, "observed_mono_ms": now_ms, "epoch_id": self.epoch_id},
            next_probe_ms=now_ms + self.policy.data["observation"]["interval_ms"],
        )
        return result


def _payload_digest(request: TypedRequest) -> str:
    return canonical_digest(
        {
            "normalized_method": request.command,
            "structured_args": request.args,
            "effective_target_identity": request.context["effective_target_identity"],
            "target_instance_identity": request.context["target_instance_identity"],
        }
    )


class GateService:
    """Creates immutable decisions, Owner records, and ApprovedInvocations."""

    def __init__(
        self,
        store: GateStore,
        policy: Policy,
        *,
        resolver: StaticIdentityResolver,
        epoch_id: str,
        discovery: Any | None = None,
        _test_overrides: bool = False,
    ):
        self.store = store
        self.policy = policy
        self.resolver = resolver
        self.epoch_id = epoch_id
        self.discovery = discovery
        self._test_overrides = _test_overrides

    def startup(self) -> list[str]:
        return self.store.startup_sweep()

    def preflight(
        self,
        request: TypedRequest,
        *,
        facts: dict[str, str] | None = None,
        evidence_digest: str | None = None,
        observed_mono_ms: int | None = None,
        decision_event_id: str | None = None,
        mutation_intent_id: str | None = None,
        orca_request_id: str | None = None,
        operation_id: str | None = None,
        reconciliation_scope: str | None = None,
        recovery_intent_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._test_overrides and any(
            value is not None
            for value in (
                facts,
                evidence_digest,
                observed_mono_ms,
                decision_event_id,
                mutation_intent_id,
                orca_request_id,
                operation_id,
                reconciliation_scope,
            )
        ):
            raise DeniedError(
                "adapter-owned evidence and gate-generated record identities are required"
            )
        self.resolver.verify(request, allow_intent=False)
        discovery_evidence = None
        if facts is None:
            if self.discovery is None:
                raise DeniedError("adapter-owned discovery is required")
            snapshot = self.discovery.discover(request)
            facts = snapshot.facts
            evidence_digest = snapshot.evidence_digest
            observed_mono_ms = snapshot.observed_mono_ms
            discovery_evidence = snapshot.evidence
        if evidence_digest is None or type(observed_mono_ms) is not int:
            raise InputError("complete discovery evidence is required")
        if facts.get("normalized_command") != request.command:
            raise DeniedError("discovery command does not match request")
        if request.context["source_contract_sha"] != self.policy.data["source_contract_sha"]:
            raise DeniedError("policy/source contract binding mismatch")
        self._adapter_preconditions(request)
        effective = request
        intent_id = request_id = None
        if request.command in MUTATIONS:
            payload_digest = _payload_digest(request)
            if recovery_intent_id:
                if mutation_intent_id is not None or orca_request_id is not None:
                    raise InputError("recovery selector conflicts with new intent identity")
                intent_id = recovery_intent_id
                request_id = self.store.recovery_request_id(
                    intent_id,
                    method=request.command,
                    payload_digest=payload_digest,
                    credential_binding_id=request.context["credential_binding_id"],
                    target_instance_identity=request.context["target_instance_identity"],
                )
            else:
                intent_id = mutation_intent_id or _identifier("intent")
                request_id = orca_request_id or _identifier("request")
            intent_identity = {
                "mutation_intent_id": intent_id,
                "orca_request_id": request_id,
                "normalized_method": request.command,
                "effective_payload_digest": payload_digest,
                "credential_binding_id": request.context["credential_binding_id"],
            }
            context = self.resolver.resolve()
            context["mutation_intent_identity_or_null"] = intent_identity
            effective = TypedRequest.from_dict(
                {"command": request.command, "args": request.args, "context": context}
            )
            if recovery_intent_id is None:
                self.store.create_mutation_intent(
                    intent_id=intent_id,
                    request_id=request_id,
                    method=request.command,
                    payload_digest=payload_digest,
                    credential_binding_id=request.context["credential_binding_id"],
                    target_instance_identity=request.context["target_instance_identity"],
                )
        active_scope = self._active_reconciliation(effective, reconciliation_scope)
        action = self.policy.decide(
            facts, reconciliation_required=active_scope is not None
        )
        decision_id = decision_event_id or _identifier("decision")
        decision = {
            "action": action,
            "normalized_command": effective.command,
            "args_digest": effective.args_digest,
            "state_class": facts.get("state_class"),
            "facts": dict(facts),
            "evidence_digest": evidence_digest,
            "evidence_observed_mono_ms": observed_mono_ms,
            "evidence_process_epoch_id": self.epoch_id,
            "policy_sha256": self.policy.sha256,
            "source_contract_sha": effective.context["source_contract_sha"],
            "cli_executable_identity": effective.context["cli_executable_identity"],
            "credential_binding_id": effective.context["credential_binding_id"],
            "mutation_intent_id": intent_id,
            "orca_request_id": request_id,
            "receipt_recovery": recovery_intent_id is not None,
            "reconciliation_scope": active_scope or reconciliation_scope,
            "recorded_at": _now(),
        }
        self.store.append_decision(decision_id, decision)
        if discovery_evidence is not None:
            self.store.append_observation(
                f"decision:{decision_id}",
                {
                    "evidence": discovery_evidence,
                    "evidence_digest": evidence_digest,
                    "observed_mono_ms": observed_mono_ms,
                    "process_epoch_id": self.epoch_id,
                },
            )
        if action == "allow" and effective.command not in GATED:
            if self.discovery is None or not hasattr(
                self.discovery, "execute_admitted"
            ):
                raise DeniedError("admitted read/preview executor is unavailable")
            try:
                executed = self.discovery.execute_admitted(effective)
            except GateError as exc:
                self.store.append_observation(
                    f"execution:{decision_id}",
                    {
                        "normalized_command": effective.command,
                        "mechanical_success": False,
                        "code": exc.code,
                        "details": exc.details,
                        "observed_at": _now(),
                    },
                )
                raise
            execution_evidence = {
                "normalized_command": effective.command,
                "mechanical_success": True,
                "exit_code": executed["exit_code"],
                "result": executed["result"],
                "envelope": executed["envelope"],
                "observed_at": _now(),
            }
            self.store.append_observation(
                f"execution:{decision_id}", execution_evidence
            )
            discovery_evidence = {
                **(discovery_evidence or {}),
                "result": executed["result"],
            }
        invocation = None
        if action == "allow" and effective.command in GATED:
            invocation = self._issue(
                operation_id or _identifier("operation"),
                "policy_allow",
                decision_id,
                effective,
                decision,
            )
        return {
            "decision_event_id": decision_id,
            "decision": action,
            "invocation": invocation,
            "request": effective,
            "discovery": discovery_evidence,
        }

    def owner_authorize(
        self,
        basis_decision_event_id: str,
        authorization_id: str | None = None,
        *,
        authorization_kind: str,
        reconciliation_scope: str | None = None,
        reconciliation_action: str | None = None,
    ) -> dict[str, Any]:
        basis = self.store.get_decision(basis_decision_event_id)
        if not basis:
            raise DeniedError("unknown basis decision")
        if authorization_kind == "owner_approval":
            if (
                basis["action"] != "require_approval"
                or reconciliation_scope
                or reconciliation_action
            ):
                raise DeniedError("Owner approval cannot override this decision")
        elif authorization_kind == "owner_reconciliation":
            reconciliation_scope = reconciliation_scope or basis.get(
                "reconciliation_scope"
            )
            if (
                basis["action"] != "reconciliation_required"
                or not reconciliation_scope
                or not self.store.reconciliation_required(reconciliation_scope)
            ):
                raise DeniedError("exact open reconciliation scope is required")
            if reconciliation_action not in {
                "receipt_recovery",
                "manual_break_glass",
            }:
                raise InputError("an exact reconciliation action is required")
        else:
            raise InputError("invalid authorization kind")
        authorization_id = authorization_id or _identifier("authorization")
        payload = {
            "authorization_kind": authorization_kind,
            "exact_action": basis["normalized_command"],
            "args_digest": basis["args_digest"],
            "approved_state_class": basis.get("state_class"),
            "mutation_intent_id": basis.get("mutation_intent_id"),
            "reconciliation_scope": reconciliation_scope,
            "reconciliation_action": reconciliation_action,
            "issued_at": _now(),
        }
        self.store.append_owner_authorization(
            authorization_id, basis_decision_event_id, payload
        )
        return {"authorization_id": authorization_id, **payload}

    def resolve_approval(
        self,
        authorization_id: str,
        request: TypedRequest,
        *,
        facts: dict[str, str] | None = None,
        evidence_digest: str | None = None,
        observed_mono_ms: int | None = None,
        resolution_event_id: str | None = None,
        operation_id: str | None = None,
    ) -> ApprovedInvocation:
        if not self._test_overrides and any(
            value is not None
            for value in (
                facts,
                evidence_digest,
                observed_mono_ms,
                resolution_event_id,
                operation_id,
            )
        ):
            raise DeniedError(
                "adapter-owned evidence and gate-generated record identities are required"
            )
        self.resolver.verify(request, allow_intent=True)
        discovery_evidence = None
        if facts is None:
            if self.discovery is None:
                raise DeniedError("fresh adapter-owned discovery is required")
            snapshot = self.discovery.discover(request)
            facts = snapshot.facts
            evidence_digest = snapshot.evidence_digest
            observed_mono_ms = snapshot.observed_mono_ms
            discovery_evidence = snapshot.evidence
        if evidence_digest is None or type(observed_mono_ms) is not int:
            raise InputError("complete fresh discovery evidence is required")
        authorization = self.store.get_owner_authorization(authorization_id)
        if not authorization:
            raise DeniedError("unknown Owner authorization")
        basis_id = authorization["basis_decision_event_id"]
        basis = self.store.get_decision(basis_id)
        if not basis:
            raise DeniedError("missing basis decision")
        if (
            basis.get("policy_sha256") != self.policy.sha256
            or basis.get("source_contract_sha")
            != self.policy.data["source_contract_sha"]
            or basis.get("source_contract_sha")
            != request.context["source_contract_sha"]
            or basis.get("cli_executable_identity")
            != request.context["cli_executable_identity"]
            or basis.get("credential_binding_id")
            != request.context["credential_binding_id"]
        ):
            raise DeniedError("policy/source contract or execution identity changed")
        reconciliation = authorization["authorization_kind"] == "owner_reconciliation"
        if reconciliation and authorization.get("reconciliation_action") != "receipt_recovery":
            raise DeniedError(
                "manual reconciliation authorization cannot issue an Orca invocation"
            )
        fresh_action = self.policy.decide(facts, reconciliation_required=reconciliation)
        expected = "reconciliation_required" if reconciliation else "require_approval"
        intent = request.context.get("mutation_intent_identity_or_null") or {}
        if (
            facts.get("normalized_command") != request.command
            or fresh_action != expected
            or request.command != authorization["exact_action"]
            or request.args_digest != authorization["args_digest"]
            or facts.get("state_class") != authorization["approved_state_class"]
            or intent.get("mutation_intent_id")
            != authorization.get("mutation_intent_id")
        ):
            raise DeniedError("fresh approval resolution no longer matches authorization")
        scope = authorization.get("reconciliation_scope")
        if scope and not self.store.reconciliation_required(scope):
            raise DeniedError("reconciliation scope changed")
        resolution_id = resolution_event_id or _identifier("resolution")
        resolution = {
            "action": "allow",
            "fresh_policy_action": fresh_action,
            "args_digest": request.args_digest,
            "state_class": facts.get("state_class"),
            "evidence_digest": evidence_digest,
            "evidence_observed_mono_ms": observed_mono_ms,
            "evidence_process_epoch_id": self.epoch_id,
            "recorded_at": _now(),
        }
        self.store.append_resolution_decision(
            resolution_id, basis_id, authorization_id, resolution
        )
        if discovery_evidence is not None:
            self.store.append_observation(
                f"resolution:{resolution_id}",
                {
                    "evidence": discovery_evidence,
                    "evidence_digest": evidence_digest,
                    "observed_mono_ms": observed_mono_ms,
                    "process_epoch_id": self.epoch_id,
                },
            )
        merged = {
            **basis,
            **resolution,
            "policy_sha256": self.policy.sha256,
            "source_contract_sha": request.context["source_contract_sha"],
            "cli_executable_identity": request.context["cli_executable_identity"],
            "credential_binding_id": request.context["credential_binding_id"],
            "mutation_intent_id": intent.get("mutation_intent_id"),
            "orca_request_id": intent.get("orca_request_id"),
            "reconciliation_scope": scope,
        }
        return self._issue(
            operation_id or _identifier("operation"),
            authorization["authorization_kind"],
            resolution_id,
            request,
            merged,
            owner_authorization_id=authorization_id,
            resolution_event_id=resolution_id,
        )

    def reconcile(
        self, scope: str, authorization_id: str, evidence: dict[str, Any]
    ) -> dict[str, Any]:
        authorization = self.store.get_owner_authorization(authorization_id)
        if (
            not authorization
            or authorization["authorization_kind"] != "owner_reconciliation"
            or authorization.get("reconciliation_scope") != scope
            or authorization.get("reconciliation_action") != "manual_break_glass"
        ):
            raise DeniedError("exact Owner reconciliation authorization required")
        self.store.close_reconciliation(scope, authorization_id, evidence)
        return {"scope": scope, "closed": True}

    def _adapter_preconditions(self, request: TypedRequest) -> None:
        if request.command in DISPATCH_TARGETING:
            _matching_local_provenance(
                self.store, request.args["dispatch_id"], request.context
            )
        if request.command in {"terminal.read", "terminal.wait"}:
            terminal = request.args["terminal_handle"]
            if (
                terminal != request.context["effective_target_identity"]
                and not self.store.provenance_for_terminal(terminal)
            ):
                raise DeniedError("terminal is not current or bound by local provenance")

    def _active_reconciliation(
        self, request: TypedRequest, explicit_scope: str | None
    ) -> str | None:
        scopes = [explicit_scope] if explicit_scope else []
        if "dispatch_id" in request.args:
            scopes.append(f"dispatch:{request.args['dispatch_id']}")
        if request.args.get("run_id") and request.args.get("task_id"):
            scopes.append(f"task:{request.args['run_id']}:{request.args['task_id']}")
        identity = request.context.get("mutation_intent_identity_or_null") or {}
        if identity.get("mutation_intent_id"):
            scopes.append(f"intent:{identity['mutation_intent_id']}")
        return next(
            (scope for scope in scopes if scope and self.store.reconciliation_required(scope)),
            None,
        )

    def _issue(
        self,
        operation_id: str,
        approval_kind: str,
        decision_event_id: str,
        request: TypedRequest,
        decision: dict[str, Any],
        *,
        owner_authorization_id: str | None = None,
        resolution_event_id: str | None = None,
    ) -> ApprovedInvocation:
        invocation = ApprovedInvocation(
            operation_id=operation_id,
            approval_kind=approval_kind,
            decision_event_id=decision_event_id,
            owner_authorization_id=owner_authorization_id,
            approval_resolution_event_id=resolution_event_id,
            normalized_command=request.command,
            args_digest=request.args_digest,
            policy_sha256=decision["policy_sha256"],
            source_contract_sha=decision["source_contract_sha"],
            evidence_digest=decision["evidence_digest"],
            evidence_observed_mono_ms=decision["evidence_observed_mono_ms"],
            evidence_process_epoch_id=decision["evidence_process_epoch_id"],
            cli_executable_identity=decision["cli_executable_identity"],
            credential_binding_id=decision["credential_binding_id"],
            mutation_intent_id=decision.get("mutation_intent_id"),
            orca_request_id=decision.get("orca_request_id"),
            reconciliation_scope=decision.get("reconciliation_scope"),
            issued_at=_now(),
            affected_scopes=tuple(self._request_scopes(request, decision)),
        )
        self.store.append_approved_invocation(invocation)
        return invocation

    @staticmethod
    def _request_scopes(
        request: TypedRequest, decision: dict[str, Any]
    ) -> list[str]:
        scopes: list[str] = []
        if decision.get("mutation_intent_id"):
            scopes.append(f"intent:{decision['mutation_intent_id']}")
        if request.args.get("dispatch_id"):
            scopes.append(f"dispatch:{request.args['dispatch_id']}")
        if request.args.get("run_id") and request.args.get("task_id"):
            scopes.append(f"task:{request.args['run_id']}:{request.args['task_id']}")
        return scopes


class GateExecutor:
    """Recomputes exact bindings, atomically consumes, and invokes Orca once."""

    def __init__(
        self,
        store: GateStore,
        policy: Policy,
        adapter: OrcaAdapter,
        *,
        resolver: StaticIdentityResolver,
        epoch_id: str,
    ):
        self.store = store
        self.policy = policy
        self.adapter = adapter
        self.resolver = resolver
        self.epoch_id = epoch_id

    def execute(
        self, operation_id: str, request: TypedRequest, *, now_mono_ms: int
    ) -> dict[str, Any]:
        invocation = self.store.get_approved_invocation(operation_id)
        if not invocation or invocation.get("decision") != "allow":
            raise DeniedError("exact ApprovedInvocation is required")
        self.resolver.verify(request, allow_intent=True)
        self._verify(invocation, request, now_mono_ms)
        if request.command in DISPATCH_TARGETING:
            _matching_local_provenance(
                self.store, request.args["dispatch_id"], request.context
            )
        affected_scopes = self._affected_scopes(operation_id, request, invocation)
        active_scope = next(
            (
                scope
                for scope in affected_scopes
                if self.store.reconciliation_required(scope)
            ),
            None,
        )
        if active_scope and (
            invocation["approval_kind"] != "owner_reconciliation"
            or invocation.get("reconciliation_scope") != active_scope
        ):
            raise ReconciliationRequired(
                "reconciliation has precedence", details={"scope": active_scope}
            )
        if not self.store.consume(operation_id):
            raise DeniedError("operation ID is already consumed")
        self.store.append_exec_attempt(
            operation_id,
            {
                "normalized_command": request.command,
                "args_digest": request.args_digest,
                "mutation_intent_id": invocation.get("mutation_intent_id"),
            },
        )
        try:
            outcome = self.adapter.invoke(
                request, orca_request_id=invocation.get("orca_request_id")
            )
        except ReconciliationRequired as exc:
            accepted_dispatch_id = exc.details.get("accepted_dispatch_id")
            accepted_provenance = None
            if (
                request.command == "orchestration.workerStart"
                and isinstance(accepted_dispatch_id, str)
                and accepted_dispatch_id
            ):
                accepted_provenance = {
                    "origin": "local",
                    "creating_command": request.command,
                    "created_by_operation_id": operation_id,
                    "mutation_intent_id": invocation.get("mutation_intent_id"),
                    "run_id": request.args["run_id"],
                    "task_id": request.args["task_id"],
                    "terminal_handle": None,
                    "worktree_id": None,
                    "worker_started_mono_ms": now_mono_ms,
                    "evidence_process_epoch_id": self.epoch_id,
                    "receipt_evidence": "worker_start_accepted",
                    "target_instance_identity": request.context[
                        "target_instance_identity"
                    ],
                    "source_contract_sha": request.context[
                        "source_contract_sha"
                    ],
                }
            unknown_outcome = {
                "mechanical_success": False,
                "spawn_started": True,
                "code": exc.code,
                "details": exc.details,
            }
            try:
                if accepted_provenance is not None:
                    self.store.append_outcome_with_provenance(
                        operation_id,
                        unknown_outcome,
                        accepted_dispatch_id,
                        accepted_provenance,
                    )
                else:
                    self.store.append_outcome(operation_id, unknown_outcome)
            except PersistenceError as persistence_exc:
                self._recover(
                    operation_id,
                    "OUTCOME_RECORDING_FAILED",
                    {"original_code": exc.code},
                    scopes=self._affected_scopes(
                        operation_id, request, invocation, accepted_dispatch_id
                    ),
                )
                raise ReconciliationRequired(
                    "post-exec outcome recording failed"
                ) from persistence_exc
            self._recover(
                operation_id,
                exc.code,
                exc.details,
                scopes=self._affected_scopes(
                    operation_id, request, invocation, accepted_dispatch_id
                ),
            )
            raise
        except GateError as exc:
            try:
                self.store.append_outcome(
                    operation_id,
                    {"mechanical_success": False, "code": exc.code, "details": exc.details},
                )
            except PersistenceError:
                self._recover(
                    operation_id,
                    "OUTCOME_RECORDING_FAILED",
                    {"original_code": exc.code},
                    scopes=affected_scopes,
                )
            raise
        result = outcome["result"]
        if request.command in {"orchestration.dispatch", "orchestration.workerStart"}:
            if not isinstance(result.get("dispatchId"), str) or not result["dispatchId"]:
                self._recover(
                    operation_id,
                    "OUTCOME_SCHEMA_MISMATCH",
                    outcome,
                    scopes=affected_scopes,
                )
                raise ReconciliationRequired("mutation outcome lacks dispatchId")
        receipt = outcome["envelope"].get("receipt")
        receipt_replay = bool(
            (isinstance(receipt, dict) and receipt.get("replayed") is True)
            or outcome["envelope"].get("replayed") is True
        )
        outcome_record = {
            "mechanical_success": True,
            "spawn_started": True,
            "exit_code": outcome["exit_code"],
            "result": result,
            "envelope": outcome["envelope"],
            "effect_claim": (
                "receipt_replay" if receipt_replay else "fresh_mechanical_result"
            ),
        }
        try:
            provenance = self._provenance_payload(
                request, invocation, result, now_mono_ms
            )
            if provenance is None:
                self.store.append_outcome(operation_id, outcome_record)
            else:
                dispatch_id, provenance_record = provenance
                existing = self.store.get_provenance(dispatch_id)
                if existing is None:
                    self.store.append_outcome_with_provenance(
                        operation_id,
                        outcome_record,
                        dispatch_id,
                        provenance_record,
                    )
                elif receipt_replay and (
                    existing.get("creating_command") == request.command
                    and existing.get("mutation_intent_id")
                    == invocation.get("mutation_intent_id")
                ):
                    self.store.append_outcome(operation_id, outcome_record)
                else:
                    raise PersistenceError(
                        "dispatch provenance already exists outside exact receipt replay"
                    )
            if receipt_replay:
                self.store.append_recovery(
                    operation_id,
                    "RECEIPT_REPLAY_RECONSTRUCTED",
                    {
                        "mutation_intent_id": invocation.get("mutation_intent_id"),
                        "orca_request_id": invocation.get("orca_request_id"),
                        "dispatch_id": result.get("dispatchId"),
                        "effect_claim": "receipt_replay",
                    },
                    terminal_closure=True,
                )
        except PersistenceError as exc:
            self._recover(
                operation_id,
                "OUTCOME_RECORDING_FAILED",
                {"spawned": True, "error": str(exc)},
                scopes=affected_scopes,
            )
            raise ReconciliationRequired("post-exec outcome recording failed") from exc
        return outcome

    def _verify(
        self, invocation: dict[str, Any], request: TypedRequest, now_mono_ms: int
    ) -> None:
        if invocation["normalized_command"] != request.command:
            raise DeniedError("command binding mismatch")
        if invocation["args_digest"] != request.args_digest:
            raise DeniedError("invocation digest mismatch")
        if invocation["policy_sha256"] != self.policy.sha256:
            raise DeniedError("policy binding mismatch")
        for field in (
            "source_contract_sha",
            "cli_executable_identity",
            "credential_binding_id",
        ):
            if invocation[field] != request.context[field]:
                raise DeniedError(f"{field} binding mismatch")
        if invocation["evidence_process_epoch_id"] != self.epoch_id:
            raise DeniedError("evidence process epoch mismatch; fresh decision required")
        age = now_mono_ms - invocation["evidence_observed_mono_ms"]
        if age < 0 or age > self.policy.freshness_for(request.command):
            raise DeniedError("evidence is stale; fresh discovery and decision required")
        if invocation["approval_kind"].startswith("owner_") and (
            not invocation.get("owner_authorization_id")
            or not invocation.get("approval_resolution_event_id")
        ):
            raise DeniedError("Owner invocation references are incomplete")

    def _provenance_payload(
        self,
        request: TypedRequest,
        invocation: dict[str, Any],
        result: dict[str, Any],
        now_mono_ms: int,
    ) -> tuple[str, dict[str, Any]] | None:
        if request.command not in {"orchestration.dispatch", "orchestration.workerStart"}:
            return None
        return (
            result["dispatchId"],
            {
                "origin": "local",
                "creating_command": request.command,
                "created_by_operation_id": invocation["operation_id"],
                "mutation_intent_id": invocation.get("mutation_intent_id"),
                "run_id": result.get("runId", request.args.get("run_id")),
                "task_id": result.get("taskId", request.args.get("task_id")),
                "terminal_handle": result.get("terminalHandle"),
                "worktree_id": result.get("worktreeId"),
                "worker_started_mono_ms": now_mono_ms,
                "evidence_process_epoch_id": self.epoch_id,
                "target_instance_identity": request.context[
                    "target_instance_identity"
                ],
                "source_contract_sha": request.context["source_contract_sha"],
            },
        )

    @staticmethod
    def _affected_scopes(
        operation_id: str,
        request: TypedRequest,
        invocation: dict[str, Any],
        accepted_dispatch_id: str | None = None,
    ) -> list[str]:
        scopes = [f"operation:{operation_id}"]
        if invocation.get("mutation_intent_id"):
            scopes.append(f"intent:{invocation['mutation_intent_id']}")
        dispatch_id = accepted_dispatch_id or request.args.get("dispatch_id")
        if dispatch_id:
            scopes.append(f"dispatch:{dispatch_id}")
        if request.args.get("run_id") and request.args.get("task_id"):
            scopes.append(f"task:{request.args['run_id']}:{request.args['task_id']}")
        return scopes

    def _recover(
        self,
        operation_id: str,
        code: str,
        details: dict[str, Any],
        *,
        scopes: list[str],
    ) -> None:
        try:
            self.store.append_recovery(operation_id, code, details)
            for scope in scopes:
                self.store.mark_reconciliation(scope, code)
        except PersistenceError as exc:
            raise AuditabilityLost(
                "primary outcome and independent recovery channel both failed",
                details={"operation_id": operation_id, "error": str(exc)},
            ) from exc


def reconstruct_receipt(method: str, receipt: object) -> dict[str, Any]:
    """Reconstruct only completed receipts or worker-start acceptance identity."""

    if not isinstance(receipt, dict):
        return {"kind": "operation_unknown", "success": False}
    if receipt.get("status") == "completed" and isinstance(receipt.get("result"), dict):
        return {"kind": "completed_replay", "success": True, "result": receipt["result"]}
    accepted = receipt.get("accepted")
    if (
        method == "orchestration.workerStart"
        and receipt.get("status") == "pending"
        and isinstance(accepted, dict)
        and isinstance(accepted.get("dispatchId"), str)
        and accepted["dispatchId"]
    ):
        return {"kind": "accepted", "dispatch_id": accepted["dispatchId"], "success": False}
    return {"kind": "operation_unknown", "success": False}


def terminal_scope_allowed(
    requested_terminal: str,
    *,
    current_terminal: str | None,
    provenance_terminal: str | None,
) -> bool:
    return requested_terminal == current_terminal or requested_terminal == provenance_terminal
