from __future__ import annotations

import json
import math
from typing import Any

from .temporal import GestureHoldState
from .types import GestureState


GESTURE_STATE_SCHEMA_VERSION = 1
MAX_SEQUENCE = 2**63 - 1


def gesture_state_payload(
    state: GestureState,
    *,
    sequence: int | None = None,
    stable: GestureHoldState | None = None,
) -> dict[str, Any]:
    payload = state.to_dict()
    payload["schema_version"] = GESTURE_STATE_SCHEMA_VERSION
    if sequence is not None:
        payload["sequence"] = _sequence(sequence)
    if stable is not None:
        stable_target = _json_key(stable.target, name="stable target")
        payload["stable"] = {
            "gestures": {
                stable_target: {
                    "active": bool(stable.active),
                    "activated": bool(stable.activated),
                    "released": bool(stable.released),
                    "held_for": _non_negative_finite_float(
                        stable.held_for,
                        name="stable held_for",
                    ),
                    "confidence": _probability(stable.confidence, name="stable confidence"),
                }
            }
        }
    return payload


def gesture_state_json(
    state: GestureState,
    *,
    sequence: int | None = None,
    stable: GestureHoldState | None = None,
) -> str:
    return json.dumps(
        gesture_state_payload(state, sequence=sequence, stable=stable),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _sequence(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("sequence must be an integer") from exc
    if not 0 <= parsed <= MAX_SEQUENCE:
        raise ValueError(f"sequence must be between 0 and {MAX_SEQUENCE}")
    return parsed


def _json_key(value: str, *, name: str) -> str:
    parsed = str(value).strip()
    if not parsed:
        raise ValueError(f"{name} must not be empty")
    if any(ord(char) < 32 or ord(char) == 127 for char in parsed):
        raise ValueError(f"{name} must not contain control characters")
    return parsed


def _finite_float(value: float, *, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def _non_negative_finite_float(value: float, *, name: str) -> float:
    parsed = _finite_float(value, name=name)
    if parsed < 0:
        raise ValueError(f"{name} must be 0 or greater")
    return parsed


def _probability(value: float, *, name: str) -> float:
    parsed = _finite_float(value, name=name)
    if not 0.0 <= parsed <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return parsed
