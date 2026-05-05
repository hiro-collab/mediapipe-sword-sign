import json
import unittest

from mediapipe_sword_sign.topics import (
    MSG_TYPE_ROOM_LIGHT_STATE,
    ROOM_LIGHT_STATE_TOPIC,
    TOPIC_ENVELOPE_SCHEMA_VERSION,
    TopicEnvelope,
    TopicHeader,
    topic_json,
    topic_payload,
)


class TopicTests(unittest.TestCase):
    def test_topic_payload_wraps_ros_like_header(self):
        payload = topic_payload(
            ROOM_LIGHT_STATE_TOPIC,
            MSG_TYPE_ROOM_LIGHT_STATE,
            {"type": "room_light_state", "label": "electric_on"},
            sequence=12,
            stamp=123.4,
            frame_id="logitech_camera",
        )

        self.assertEqual(payload["schema_version"], TOPIC_ENVELOPE_SCHEMA_VERSION)
        self.assertEqual(payload["topic"], ROOM_LIGHT_STATE_TOPIC)
        self.assertEqual(payload["msg_type"], MSG_TYPE_ROOM_LIGHT_STATE)
        self.assertEqual(payload["header"]["seq"], 12)
        self.assertEqual(payload["header"]["stamp"], 123.4)
        self.assertEqual(payload["header"]["frame_id"], "logitech_camera")
        self.assertEqual(payload["payload"]["label"], "electric_on")

    def test_topic_json_is_compact_json(self):
        payload = json.loads(
            topic_json(
                ROOM_LIGHT_STATE_TOPIC,
                MSG_TYPE_ROOM_LIGHT_STATE,
                {"type": "room_light_state"},
                sequence=1,
                stamp=1.0,
            )
        )

        self.assertEqual(payload["topic"], ROOM_LIGHT_STATE_TOPIC)

    def test_topic_validation_rejects_bad_topic_names(self):
        with self.assertRaises(ValueError):
            TopicEnvelope(
                topic="vision/room_light/state",
                msg_type=MSG_TYPE_ROOM_LIGHT_STATE,
                payload={},
                header=TopicHeader(sequence=1, stamp=1.0, frame_id="camera"),
            ).to_dict()

        with self.assertRaises(ValueError):
            TopicEnvelope(
                topic="/vision//room_light",
                msg_type=MSG_TYPE_ROOM_LIGHT_STATE,
                payload={},
                header=TopicHeader(sequence=1, stamp=1.0, frame_id="camera"),
            ).to_dict()

    def test_topic_validation_rejects_invalid_header(self):
        with self.assertRaises(ValueError):
            TopicHeader(sequence=-1, stamp=1.0, frame_id="camera").to_dict()

        with self.assertRaises(ValueError):
            TopicHeader(sequence=1, stamp=float("nan"), frame_id="camera").to_dict()


if __name__ == "__main__":
    unittest.main()
