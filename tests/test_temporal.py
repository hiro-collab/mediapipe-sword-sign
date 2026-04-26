import unittest

from mediapipe_sword_sign.temporal import GestureHoldTracker
from mediapipe_sword_sign.types import GesturePrediction, GestureState


def make_state(active: bool, confidence: float = 0.95) -> GestureState:
    return GestureState(
        timestamp=0.0,
        source="test",
        hand_detected=True,
        primary="sword_sign" if active else None,
        gestures={
            "sword_sign": GesturePrediction(
                name="sword_sign",
                active=active,
                confidence=confidence,
                label=0,
            )
        },
    )


class GestureHoldTrackerTests(unittest.TestCase):
    def test_does_not_activate_before_hold_duration(self):
        tracker = GestureHoldTracker(hold_seconds=0.5)

        first = tracker.update(make_state(True), now=10.0)
        second = tracker.update(make_state(True), now=10.4)

        self.assertFalse(first.active)
        self.assertFalse(second.active)
        self.assertFalse(second.activated)

    def test_activates_after_hold_duration(self):
        tracker = GestureHoldTracker(hold_seconds=0.5)

        tracker.update(make_state(True), now=10.0)
        held = tracker.update(make_state(True), now=10.5)

        self.assertTrue(held.active)
        self.assertTrue(held.activated)
        self.assertAlmostEqual(held.held_for, 0.5)

    def test_release_grace_keeps_active_through_short_gap(self):
        tracker = GestureHoldTracker(hold_seconds=0.5, release_grace_seconds=0.2)

        tracker.update(make_state(True), now=10.0)
        tracker.update(make_state(True), now=10.5)
        gap = tracker.update(make_state(False), now=10.6)

        self.assertFalse(gap.current_active)
        self.assertTrue(gap.active)
        self.assertFalse(gap.released)

    def test_release_grace_does_not_activate_without_current_frame(self):
        tracker = GestureHoldTracker(hold_seconds=0.5, release_grace_seconds=1.0)

        tracker.update(make_state(True), now=10.0)
        gap = tracker.update(make_state(False), now=10.6)

        self.assertFalse(gap.current_active)
        self.assertFalse(gap.active)
        self.assertFalse(gap.activated)

    def test_releases_after_grace_expires(self):
        tracker = GestureHoldTracker(hold_seconds=0.5, release_grace_seconds=0.2)

        tracker.update(make_state(True), now=10.0)
        tracker.update(make_state(True), now=10.5)
        released = tracker.update(make_state(False), now=10.8)

        self.assertFalse(released.active)
        self.assertTrue(released.released)

    def test_target_change_resets_state(self):
        tracker = GestureHoldTracker(hold_seconds=0.0)

        active = tracker.update(make_state(True), now=10.0)
        tracker.configure(target="victory")
        reset = tracker.update(make_state(True), now=10.1)

        self.assertTrue(active.active)
        self.assertFalse(reset.active)


if __name__ == "__main__":
    unittest.main()
