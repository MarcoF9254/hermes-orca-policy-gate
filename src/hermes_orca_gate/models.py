"""Closed request schemas and immutable gate record value objects."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from copy import deepcopy
from typing import Any

from .canonical import NORMALIZATION_VERSION, canonical_digest
from .errors import InputError

CONTEXT_FIELDS = frozenset(
    {
        "cli_executable_identity",
        "source_contract_sha",
        "effective_target_identity",
        "fixed_or_bound_cwd",
        "execution_relevant_env",
        "target_instance_identity",
        "dev_mode",
        "credential_binding_id",
        "mutation_intent_identity_or_null",
    }
)
SCHEMAS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "orchestration.dispatch": (
        frozenset(
            {"run_id", "task_id", "to_handle", "from_handle", "inject", "return_preamble", "dry_run"}
        ),
        frozenset(),
    ),
    "orchestration.dispatchDryRun": (
        frozenset({"run_id", "task_id", "from_handle", "dry_run", "return_preamble"}),
        frozenset({"to_handle"}),
    ),
    "orchestration.workerStart": (
        frozenset({"run_id", "task_id", "from_handle", "worktree_mode", "setup"}),
        frozenset(
            {
                "parent_worktree_id",
                "resolved_worktree_id",
                "repo_id",
                "base_branch",
                "name",
                "display_name",
                "comment",
                "agent",
                "model",
                "effort",
                "terminal_handle",
                "startup_timeout_ms",
                "retry_of",
            }
        ),
    ),
    "orchestration.workerStop": (frozenset({"dispatch_id"}), frozenset()),
    "orchestration.workerAbandon": (frozenset({"dispatch_id"}), frozenset()),
    "orchestration.workerShow": (frozenset({"dispatch_id"}), frozenset()),
    "orchestration.runCurrent": (frozenset({"terminal_handle"}), frozenset()),
    "orchestration.runShow": (frozenset({"run_id"}), frozenset()),
    "orchestration.taskList": (frozenset({"run_id"}), frozenset({"status"})),
    "orchestration.dispatchShow": (
        frozenset({"task_id"}),
        frozenset({"from_handle", "return_preamble"}),
    ),
    "orchestration.workerRead": (
        frozenset({"dispatch_id", "cursor", "limit", "source"}),
        frozenset(),
    ),
    "terminal.read": (frozenset({"terminal_handle", "cursor", "limit"}), frozenset()),
    "terminal.wait": (
        frozenset({"terminal_handle", "condition", "timeout_ms"}),
        frozenset(),
    ),
}
MUTATIONS = frozenset(
    {
        "orchestration.dispatch",
        "orchestration.workerStart",
        "orchestration.workerStop",
        "orchestration.workerAbandon",
    }
)
GATED = MUTATIONS | {"orchestration.workerShow"}
DISPATCH_TARGETING = frozenset(
    {
        "orchestration.workerStop",
        "orchestration.workerAbandon",
        "orchestration.workerShow",
        "orchestration.workerRead",
    }
)


def _closed(value: object, allowed: frozenset[str] | set[str], label: str) -> None:
    if not isinstance(value, dict):
        raise InputError(f"{label} must be an object")
    unknown = set(value) - set(allowed)
    if unknown:
        raise InputError(f"unknown {label} fields: {sorted(unknown)}")


def _nonempty_string(value: object, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise InputError(f"{label} must be a non-empty string")


@dataclass(frozen=True)
class TypedRequest:
    command: str
    args: dict[str, Any]
    context: dict[str, Any]

    @classmethod
    def from_dict(cls, data: object) -> "TypedRequest":
        _closed(data, {"command", "args", "context"}, "request")
        assert isinstance(data, dict)
        command = data.get("command")
        if command not in SCHEMAS:
            raise InputError("command is not admitted")
        required, optional = SCHEMAS[command]
        args = data.get("args")
        _closed(args, required | optional, "argument")
        assert isinstance(args, dict)
        missing = required - set(args)
        if missing:
            raise InputError(f"missing argument fields: {sorted(missing)}")
        for field in required:
            if args[field] is None:
                raise InputError(f"{field} must not be null")
        context = data.get("context")
        _closed(context, CONTEXT_FIELDS, "context")
        assert isinstance(context, dict)
        required_context = CONTEXT_FIELDS - {"mutation_intent_identity_or_null"}
        missing_context = required_context - set(context)
        if missing_context:
            raise InputError(f"missing context fields: {sorted(missing_context)}")
        cls._validate(command, args, context)
        return cls(command=command, args=deepcopy(args), context=deepcopy(context))

    @staticmethod
    def _validate(command: str, args: dict[str, Any], context: dict[str, Any]) -> None:
        for key, value in args.items():
            if value is not None and not isinstance(value, (str, int, bool)):
                raise InputError(f"invalid type for {key}")
        for key in (
            "run_id",
            "task_id",
            "to_handle",
            "from_handle",
            "dispatch_id",
            "terminal_handle",
        ):
            if key in args and args[key] is not None:
                _nonempty_string(args[key], key)
        for key in (
            "worktree_mode",
            "parent_worktree_id",
            "resolved_worktree_id",
            "repo_id",
            "base_branch",
            "name",
            "display_name",
            "comment",
            "agent",
            "model",
            "effort",
            "retry_of",
            "source",
            "status",
            "condition",
        ):
            if key in args and args[key] is not None:
                _nonempty_string(args[key], key)
        if command == "orchestration.dispatch":
            if args["dry_run"] is not False:
                raise InputError("dispatch mutation requires dry_run=false")
            if type(args["inject"]) is not bool or type(args["return_preamble"]) is not bool:
                raise InputError("dispatch boolean fields must be explicit booleans")
        if command == "orchestration.dispatchDryRun":
            if args["dry_run"] is not True:
                raise InputError("preview requires dry_run=true")
            if type(args["return_preamble"]) is not bool:
                raise InputError("return_preamble must be an explicit boolean")
        if command == "orchestration.workerStart":
            mode = args["worktree_mode"]
            if mode in ("current", None, ""):
                raise InputError("current or omitted worktree is refused")
            if args["setup"] not in ("run", "skip", "inherit"):
                raise InputError("invalid setup")
            if mode == "new-child" and "parent_worktree_id" not in args:
                raise InputError("new-child requires parent_worktree_id")
            if mode in ("new-child", "new-top-level") and any(
                key not in args for key in ("repo_id", "base_branch", "name")
            ):
                raise InputError("new worktree requires repo/base/name")
            if mode in ("new-child", "new-top-level"):
                for key in ("repo_id", "base_branch", "name"):
                    _nonempty_string(args[key], key)
            if mode == "new-child":
                _nonempty_string(args["parent_worktree_id"], "parent_worktree_id")
            if mode == "new-top-level" and "parent_worktree_id" in args:
                raise InputError("new-top-level forbids parent")
            if mode in ("new-child", "new-top-level") and any(
                key in args for key in ("resolved_worktree_id", "terminal_handle")
            ):
                raise InputError("new worktree mode forbids conflicting existing topology")
            if mode not in ("new-child", "new-top-level") and any(
                key in args
                for key in ("parent_worktree_id", "repo_id", "base_branch", "name")
            ):
                raise InputError("existing worktree mode forbids creation topology")
            if mode not in ("new-child", "new-top-level") and "terminal_handle" not in args:
                raise InputError(
                    "existing worktree requires terminal_handle relationship evidence"
                )
            if mode not in ("new-child", "new-top-level") and args.get(
                "resolved_worktree_id"
            ) != mode:
                raise InputError("existing worktree identity mismatch")
            if "startup_timeout_ms" in args and (
                type(args["startup_timeout_ms"]) is not int
                or args["startup_timeout_ms"] <= 0
            ):
                raise InputError("startup_timeout_ms must be positive")
        for key in ("cursor", "limit", "timeout_ms"):
            if key in args and (type(args[key]) is not int or args[key] < 0):
                raise InputError(f"{key} must be a non-negative integer")
        env = context["execution_relevant_env"]
        if not isinstance(env, dict) or set(env) != {
            "ORCA_USER_DATA_PATH",
            "ORCA_DEV_CLI_INVOCATION",
        }:
            raise InputError("execution environment is not the closed bound set")
        if not all(isinstance(value, str) for value in env.values()):
            raise InputError("execution environment values must be strings")
        dev_environment = env["ORCA_DEV_CLI_INVOCATION"]
        if dev_environment not in {"0", "1"} or context["dev_mode"] is not (
            dev_environment == "1"
        ):
            raise InputError("dev_mode does not match the bound Orca environment")
        for key in (
            "cli_executable_identity",
            "source_contract_sha",
            "effective_target_identity",
            "fixed_or_bound_cwd",
            "target_instance_identity",
            "credential_binding_id",
        ):
            _nonempty_string(context[key], key)
        if type(context["dev_mode"]) is not bool:
            raise InputError("dev_mode must be a derived boolean")
        mutation_identity = context.get("mutation_intent_identity_or_null")
        if mutation_identity is not None:
            expected = {
                "mutation_intent_id",
                "orca_request_id",
                "normalized_method",
                "effective_payload_digest",
                "credential_binding_id",
            }
            if not isinstance(mutation_identity, dict) or set(mutation_identity) != expected:
                raise InputError("mutation intent identity fields are not closed")
            for key in expected:
                _nonempty_string(mutation_identity[key], f"mutation intent {key}")
            digest = mutation_identity["effective_payload_digest"]
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise InputError("mutation intent payload digest is invalid")
            if (
                command not in MUTATIONS
                or mutation_identity["normalized_method"] != command
                or mutation_identity["credential_binding_id"]
                != context["credential_binding_id"]
            ):
                raise InputError("mutation intent binding mismatch")

    def canonical_invocation(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "normalization_version": NORMALIZATION_VERSION,
            "normalized_command": self.command,
            "structured_args": deepcopy(self.args),
        }
        result.update(deepcopy(self.context))
        result.setdefault("mutation_intent_identity_or_null", None)
        return result

    @property
    def args_digest(self) -> str:
        return canonical_digest(self.canonical_invocation())


@dataclass(frozen=True)
class ApprovedInvocation:
    operation_id: str
    approval_kind: str
    decision_event_id: str
    normalized_command: str
    args_digest: str
    policy_sha256: str
    source_contract_sha: str
    evidence_digest: str
    evidence_observed_mono_ms: int
    evidence_process_epoch_id: str
    cli_executable_identity: str
    credential_binding_id: str
    owner_authorization_id: str | None = None
    approval_resolution_event_id: str | None = None
    mutation_intent_id: str | None = None
    orca_request_id: str | None = None
    reconciliation_scope: str | None = None
    decision: str = "allow"
    issued_at: str = ""
    affected_scopes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
