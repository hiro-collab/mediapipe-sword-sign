import json
import unittest

from mediapipe_sword_sign.types import GestureState


class GestureStateTests(unittest.TestCase):
    def test_no_hand_state_is_protocol_serializable(self):
        state = GestureState.no_hand(source="test", timestamp=123.0)
        payload = json.loads(state.to_json())

        self.assertEqual(payload["type"], "gesture_state")
        self.assertEqual(payload["source"], "test")
        self.assertFalse(payload["hand_detected"])
        self.assertFalse(payload["gestures"]["sword_sign"]["active"])

    def test_metadata_is_serialized_when_present(self):
        state = GestureState.no_hand(
            source="test",
            timestamp=123.0,
            metadata={"frame_id": 7, "fps": 30.0},
        )
        payload = json.loads(state.to_json())

        self.assertEqual(payload["metadata"]["frame_id"], 7)
        self.assertEqual(payload["metadata"]["fps"], 30.0)


if __name__ == "__main__":
    unittest.main()
