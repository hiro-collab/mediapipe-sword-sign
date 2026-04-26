from __future__ import annotations

import time
from dataclasses import dataclass

from .types import GESTURE_SWORD_SIGN, GestureState


@dataclass(frozen=True)
class GestureHoldState:
    target: str
    current_active: bool
    active: bool
    changed: bool
    activated: bool
    released: bool
    held_for: float
    confidence: float


class GestureHoldTracker:
    """Promote frame-level gesture states to duration-based states."""

    def __init__(
        self,
        target: str = GESTURE_SWORD_SIGN,
        *,
        hold_seconds: float = 0.5,
        release_grace_seconds: float = 0.1,
    ) -> None:
        self.target = target
        self.hold_seconds = max(0.0, float(hold_seconds))
        self.release_grace_seconds = max(0.0, float(release_grace_seconds))
        self._candidate_started_at: float | None = None
        self._last_seen_at: float | None = None
        self._active = False

    def configure(
        self,
        *,
        target: str | None = None,
        hold_seconds: float | None = None,
        release_grace_seconds: float | None = None,
    ) -> None:
        if target is not None and target != self.target:
            self.target = target
            self.reset()
        if hold_seconds is not None:
            self.hold_seconds = max(0.0, float(hold_seconds))
        if release_grace_seconds is not None:
            self.release_grace_seconds = max(0.0, float(release_grace_seconds))

    def reset(self) -> None:
        self._candidate_started_at = None
        self._last_seen_at = None
        self._active = False

    def update(self, state: GestureState, *, now: float | None = None) -> GestureHoldState:
        now = time.monotonic() if now is None else float(now)
        prediction = state.gesture(self.target)
        current_active = prediction.active

        if current_active:
            if self._candidate_started_at is None:
                self._candidate_started_at = now
            self._last_seen_at = now

        effective_active = current_active or self._is_within_release_grace(now)
        if not effective_active:
            self._candidate_started_at = None
            self._last_seen_at = None

        held_for = (
            max(0.0, now - self._candidate_started_at)
            if effective_active and self._candidate_started_at is not None
            else 0.0
        )

        previous_active = self._active
        can_be_active = current_active or previous_active
        self._active = bool(effective_active and held_for >= self.hold_seconds and can_be_active)
        changed = previous_active != self._active

        return GestureHoldState(
            target=self.target,
            current_active=current_active,
            active=self._active,
            changed=changed,
            activated=changed and self._active,
            released=changed and not self._active,
            held_for=held_for,
            confidence=prediction.confidence,
        )

    def _is_within_release_grace(self, now: float) -> bool:
        if self._candidate_started_at is None or self._last_seen_at is None:
            return False
        return (now - self._last_seen_at) <= self.release_grace_seconds
