from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Mapping


GESTURE_SWORD_SIGN = "sword_sign"
GESTURE_VICTORY = "victory"
GESTURE_NONE = "none"

DEFAULT_LABELS: dict[int, str] = {
    0: GESTURE_SWORD_SIGN,
    1: GESTURE_VICTORY,
    2: GESTURE_NONE,
}

DISPLAY_NAMES: dict[str, str] = {
    GESTURE_SWORD_SIGN: "Sword",
    GESTURE_VICTORY: "Victory",
    GESTURE_NONE: "None",
}


@dataclass(frozen=True)
class GesturePrediction:
    name: str
    active: bool
    confidence: float
    label: int | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "active": bool(self.active),
            "confidence": float(self.confidence),
        }
        if self.label is not None:
            payload["label"] = int(self.label)
        return payload


@dataclass(frozen=True)
class GestureState:
    timestamp: float
    source: str
    gestures: Mapping[str, GesturePrediction]
    primary: str | None = None
    hand_detected: bool = False

    @property
    def sword_sign(self) -> GesturePrediction:
        return self.gesture(GESTURE_SWORD_SIGN)

    @property
    def victory(self) -> GesturePrediction:
        return self.gesture(GESTURE_VICTORY)

    def gesture(self, name: str) -> GesturePrediction:
        return self.gestures.get(name, GesturePrediction(name=name, active=False, confidence=0.0))

    def best_gesture(self) -> GesturePrediction | None:
        if not self.gestures:
            return None
        return max(self.gestures.values(), key=lambda gesture: gesture.confidence)

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "gesture_state",
            "timestamp": float(self.timestamp),
            "source": self.source,
            "hand_detected": bool(self.hand_detected),
            "primary": self.primary,
            "gestures": {
                name: gesture.to_dict()
                for name, gesture in self.gestures.items()
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def no_hand(
        cls,
        *,
        source: str,
        timestamp: float | None = None,
        labels: Mapping[int, str] | None = None,
    ) -> "GestureState":
        return cls(
            timestamp=time.time() if timestamp is None else timestamp,
            source=source,
            hand_detected=False,
            primary=None,
            gestures={
                name: GesturePrediction(name=name, active=False, confidence=0.0, label=label)
                for label, name in (labels or DEFAULT_LABELS).items()
            },
        )
