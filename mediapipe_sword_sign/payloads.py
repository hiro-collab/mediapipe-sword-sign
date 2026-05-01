from __future__ import annotations

import json
from typing import Any

from .temporal import GestureHoldState
from .types import GestureState


GESTURE_STATE_SCHEMA_VERSION = 1


def gesture_state_payload(
    state: GestureState,
    *,
    sequence: int | None = None,
    stable: GestureHoldState | None = None,
) -> dict[str, Any]:
    payload = state.to_dict()
    payload["schema_version"] = GESTURE_STATE_SCHEMA_VERSION
    if sequence is not None:
        payload["sequence"] = int(sequence)
    if stable is not None:
        payload["stable"] = {
            "gestures": {
                stable.target: {
                    "active": bool(stable.active),
                    "activated": bool(stable.activated),
                    "released": bool(stable.released),
                    "held_for": float(stable.held_for),
                    "confidence": float(stable.confidence),
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
        ensure_ascii=False,
        separators=(",", ":"),
    )
