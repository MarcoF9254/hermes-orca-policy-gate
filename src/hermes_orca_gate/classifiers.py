"""Total deterministic classifiers for Orca dispatch and worker state."""

from __future__ import annotations

DISPATCH_STATUSES = frozenset(
    {"pending", "dispatched", "completed", "failed", "circuit_broken"}
)
WORKER_STATES = frozenset(
    {
        "starting",
        "ready",
        "start_unknown",
        "failed",
        "succeeded",
        "stopping",
        "stop_unknown",
        "stopped",
        "abandoned",
    }
)


def classify_dispatch(
    status: object, *, completion_evidence: bool = False, query_ok: bool = True
) -> str:
    if not query_ok or status not in DISPATCH_STATUSES:
        return "RUNTIME_UNKNOWN"
    if status == "circuit_broken":
        return "CIRCUIT_BROKEN"
    if status == "failed":
        return "FAILED"
    if status == "completed":
        return "OK" if completion_evidence else "RUNTIME_UNKNOWN"
    return "OBSERVATION_PENDING"


def classify_worker(
    dispatch_status: object,
    worker_state: object,
    *,
    query_ok: bool = True,
    worker_present: bool = True,
    completion_evidence: bool = False,
) -> str:
    if (
        not query_ok
        or not worker_present
        or dispatch_status not in DISPATCH_STATUSES
        or worker_state not in WORKER_STATES
    ):
        return "RUNTIME_UNKNOWN"
    if dispatch_status == "circuit_broken":
        return "CIRCUIT_BROKEN"
    if worker_state in {"start_unknown", "stop_unknown", "abandoned"}:
        return "RUNTIME_UNKNOWN"
    if dispatch_status == "failed" or worker_state == "failed":
        return "FAILED"
    if worker_state == "starting":
        return "STARTING"
    if worker_state == "stopping":
        return "STOPPING"
    if worker_state == "stopped":
        return "STOPPED"
    if worker_state == "succeeded":
        return "OK"
    if dispatch_status == "completed":
        return "OK" if completion_evidence else "RUNTIME_UNKNOWN"
    if worker_state == "ready":
        return "OBSERVATION_PENDING"
    return "RUNTIME_UNKNOWN"


def route_for(state: str) -> str:
    return {
        "STARTING": "observe",
        "STOPPING": "observe",
        "OBSERVATION_PENDING": "observe",
        "OK": "terminal",
        "STOPPED": "contained",
    }.get(state, "owner_decision")
