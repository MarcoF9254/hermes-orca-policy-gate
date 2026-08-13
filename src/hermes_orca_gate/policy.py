"""Closed JSON policy loader and deterministic evaluator."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import PolicyLoadError

ROOT_FIELDS = frozenset(
    {"version", "default", "source_contract_sha", "freshness_max_age_ms", "observation", "rules"}
)
FRESH_COMMANDS = frozenset(
    {
        "orchestration.dispatch",
        "orchestration.workerStart",
        "orchestration.workerStop",
        "orchestration.workerAbandon",
        "orchestration.workerShow",
    }
)
OBSERVATION_FIELDS = frozenset(
    {"interval_ms", "qualifying_checkpoints", "probe_timeout_ms", "default_ceiling_ms"}
)
MATCH_FIELDS = frozenset(
    {
        "normalized_command",
        "state_class",
        "target_instance_identity",
        "repo_id",
        "context_digest",
        "approved_action",
    }
)
ACTIONS = frozenset({"allow", "require_approval", "block", "reconciliation_required"})
COMMANDS = FRESH_COMMANDS | {
    "orchestration.dispatchDryRun",
    "orchestration.runCurrent",
    "orchestration.runShow",
    "orchestration.taskList",
    "orchestration.dispatchShow",
    "orchestration.workerRead",
    "terminal.read",
    "terminal.wait",
}
STATES = frozenset(
    {
        "RUNTIME_UNKNOWN",
        "CIRCUIT_BROKEN",
        "FAILED",
        "STARTING",
        "STOPPING",
        "STOPPED",
        "OBSERVATION_PENDING",
        "OK",
    }
)


def _fail(message: str) -> None:
    raise PolicyLoadError(message)


def _exact(value: object, fields: frozenset[str] | set[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != set(fields):
        _fail(f"invalid {name} fields")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


@dataclass(frozen=True)
class Policy:
    data: dict[str, Any]
    sha256: str

    @classmethod
    def from_path(cls, path: str | Path) -> "Policy":
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            raise PolicyLoadError(f"cannot read policy: {exc}") from exc
        return cls.from_bytes(raw)

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Policy":
        try:
            data = json.loads(raw, object_pairs_hook=_no_duplicates)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PolicyLoadError(f"invalid policy JSON: {exc}") from exc
        if not isinstance(data, dict) or set(data) != ROOT_FIELDS:
            _fail("invalid root fields")
        if type(data["version"]) is not int or data["version"] != 1:
            _fail("unsupported policy version")
        if data["default"] != {"action": "block"}:
            _fail("default action must be block")
        source_sha = data["source_contract_sha"]
        if (
            not isinstance(source_sha, str)
            or len(source_sha) != 40
            or any(character not in "0123456789abcdef" for character in source_sha)
        ):
            _fail("invalid source contract identity")
        freshness = data["freshness_max_age_ms"]
        if not isinstance(freshness, dict) or set(freshness) != FRESH_COMMANDS:
            _fail("freshness commands must be exact")
        if any(
            type(value) is not int or not 1 <= value <= 60_000
            for value in freshness.values()
        ):
            _fail("freshness must be 1..60000 ms")
        observation = data["observation"]
        _exact(observation, OBSERVATION_FIELDS, "observation")
        if any(type(value) is not int or value <= 0 for value in observation.values()):
            _fail("observation values must be positive integers")
        if observation != {
            "interval_ms": 60_000,
            "qualifying_checkpoints": 3,
            "probe_timeout_ms": 60_000,
            "default_ceiling_ms": 2_700_000,
        }:
            _fail("observation profile does not match reviewed v1 constants")
        rules = data["rules"]
        if not isinstance(rules, list):
            _fail("rules must be a list")
        identifiers: set[str] = set()
        for rule in rules:
            _exact(rule, {"id", "match", "action"}, "rule")
            rule_id = rule["id"]
            if not isinstance(rule_id, str) or not rule_id or rule_id in identifiers:
                _fail("duplicate or invalid rule id")
            identifiers.add(rule_id)
            if rule["action"] not in ACTIONS:
                _fail("invalid action")
            match = rule["match"]
            if not isinstance(match, dict) or not match or set(match) - MATCH_FIELDS:
                _fail("invalid match field/operator")
            if match.get("normalized_command") not in COMMANDS:
                _fail("unknown command")
            if "state_class" in match and match["state_class"] not in STATES:
                _fail("unknown state")
            if any(not isinstance(value, str) for value in match.values()):
                _fail("invalid match value")
        return cls(data=data, sha256=hashlib.sha256(raw).hexdigest())

    def decide(
        self, facts: dict[str, str], *, reconciliation_required: bool = False
    ) -> str:
        if reconciliation_required:
            return "reconciliation_required"
        if facts.get("state_class") in {
            "STARTING",
            "STOPPING",
            "OBSERVATION_PENDING",
        }:
            return "observe"
        if facts.get("state_class") in {
            "RUNTIME_UNKNOWN",
            "CIRCUIT_BROKEN",
            "FAILED",
        }:
            return "require_approval"
        for rule in self.data["rules"]:
            if all(facts.get(key) == value for key, value in rule["match"].items()):
                return rule["action"]
        return "block"

    def freshness_for(self, command: str) -> int:
        try:
            return self.data["freshness_max_age_ms"][command]
        except KeyError as exc:
            raise PolicyLoadError("missing freshness for gated command") from exc
