import json
import unittest

from mediapipe_sword_sign.topics import (
    MSG_TYPE_GESTURE_STATE,
    SWORD_SIGN_STATE_TOPIC,
    TOPIC_ENVELOPE_SCHEMA_VERSION,
    TopicEnvelope,
    TopicHeader,
    binary_topic_message,
    parse_binary_topic_message,
    topic_json,
    topic_payload,
)


class TopicTests(unittest.TestCase):
    def test_topic_payload_wraps_header(self):
        payload = topic_payload(
            SWORD_SIGN_STATE_TOPIC,
            MSG_TYPE_GESTURE_STATE,
            {"type": "gesture_state", "primary": "sword_sign"},
            sequence=12,
            stamp=123.4,
            frame_id="logitech_camera",
        )

        self.assertEqual(payload["schema_version"], TOPIC_ENVELOPE_SCHEMA_VERSION)
        self.assertEqual(payload["topic"], SWORD_SIGN_STATE_TOPIC)
        self.assertEqual(payload["msg_type"], MSG_TYPE_GESTURE_STATE)
        self.assertEqual(payload["header"]["seq"], 12)
        self.assertEqual(payload["header"]["stamp"], 123.4)
        self.assertEqual(payload["header"]["frame_id"], "logitech_camera")
        self.assertEqual(payload["payload"]["primary"], "sword_sign")

    def test_topic_json_is_compact_json(self):
        payload = json.loads(
            topic_json(
                SWORD_SIGN_STATE_TOPIC,
                MSG_TYPE_GESTURE_STATE,
                {"type": "gesture_state"},
                sequence=1,
                stamp=1.0,
            )
        )

        self.assertEqual(payload["topic"], SWORD_SIGN_STATE_TOPIC)

    def test_binary_topic_message_wraps_json_header_and_bytes(self):
        message = binary_topic_message(
            SWORD_SIGN_STATE_TOPIC,
            MSG_TYPE_GESTURE_STATE,
            {"type": "gesture_state"},
            b"jpeg-bytes",
            sequence=3,
            stamp=2.5,
            frame_id="camera",
        )

        envelope, data = parse_binary_topic_message(message)

        self.assertEqual(envelope["topic"], SWORD_SIGN_STATE_TOPIC)
        self.assertEqual(envelope["header"]["seq"], 3)
        self.assertEqual(data, b"jpeg-bytes")

    def test_parse_binary_topic_message_rejects_unknown_message(self):
        with self.assertRaises(ValueError):
            parse_binary_topic_message(b"not-a-topic-message")

    def test_topic_validation_rejects_bad_topic_names(self):
        with self.assertRaises(ValueError):
            TopicEnvelope(
                topic="vision/sword_sign/state",
                msg_type=MSG_TYPE_GESTURE_STATE,
                payload={},
                header=TopicHeader(sequence=1, stamp=1.0, frame_id="camera"),
            ).to_dict()

        with self.assertRaises(ValueError):
            TopicEnvelope(
                topic="/vision//sword_sign",
                msg_type=MSG_TYPE_GESTURE_STATE,
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
