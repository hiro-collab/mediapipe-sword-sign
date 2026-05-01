import json
import unittest

from mediapipe_sword_sign.payloads import GESTURE_STATE_SCHEMA_VERSION, gesture_state_json, gesture_state_payload
from mediapipe_sword_sign.temporal import GestureHoldState
from mediapipe_sword_sign.types import GesturePrediction, GestureState


def make_state() -> GestureState:
    return GestureState(
        timestamp=123.0,
        source="test",
        hand_detected=True,
        primary="sword_sign",
        gestures={
            "sword_sign": GesturePrediction(
                name="sword_sign",
                active=True,
                confidence=0.95,
                label=0,
            )
        },
    )


class GesturePayloadTests(unittest.TestCase):
    def test_payload_adds_schema_version_and_sequence_without_replacing_raw_state(self):
        payload = gesture_state_payload(make_state(), sequence=7)

        self.assertEqual(payload["schema_version"], GESTURE_STATE_SCHEMA_VERSION)
        self.assertEqual(payload["sequence"], 7)
        self.assertTrue(payload["gestures"]["sword_sign"]["active"])
        self.assertNotIn("stable", payload)

    def test_payload_can_include_stable_gesture_state(self):
        stable = GestureHoldState(
            target="sword_sign",
            current_active=True,
            active=True,
            changed=True,
            activated=True,
            released=False,
            held_for=0.6,
            confidence=0.95,
        )

        payload = gesture_state_payload(make_state(), sequence=8, stable=stable)

        stable_sword = payload["stable"]["gestures"]["sword_sign"]
        self.assertTrue(stable_sword["active"])
        self.assertTrue(stable_sword["activated"])
        self.assertFalse(stable_sword["released"])
        self.assertEqual(stable_sword["held_for"], 0.6)
        self.assertEqual(stable_sword["confidence"], 0.95)

    def test_json_serializes_extended_payload_compactly(self):
        payload = json.loads(gesture_state_json(make_state(), sequence=9))

        self.assertEqual(payload["type"], "gesture_state")
        self.assertEqual(payload["schema_version"], GESTURE_STATE_SCHEMA_VERSION)
        self.assertEqual(payload["sequence"], 9)


if __name__ == "__main__":
    unittest.main()
