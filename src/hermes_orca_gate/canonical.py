"""Versioned canonical JSON encoding used for immutable invocation bindings."""

from __future__ import annotations

import hashlib
import json
from typing import Any

NORMALIZATION_VERSION = "canonical-json-v1"


def _validate(value: Any) -> None:
    if isinstance(value, float):
        raise ValueError("float values are not admitted")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, list):
        for item in value:
            _validate(item)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("map keys must be strings")
        for item in value.values():
            _validate(item)
        return
    raise ValueError(f"unsupported canonical type: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    """Encode the admitted JSON domain as sorted, compact UTF-8 bytes."""

    _validate(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()
