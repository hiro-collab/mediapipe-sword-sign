from pathlib import PureWindowsPath
import unittest

from apps.serve_camera_hub import (
    camera_status_payload,
    compressed_image_binary_payload,
    looks_like_local_file_source,
    parse_camera_backend,
    redact_camera_source,
)
from mediapipe_sword_sign.topics import (
    CAMERA_STATUS_TOPIC,
    MSG_TYPE_CAMERA_STATUS,
    topic_payload,
)


class Worker2OrganModuleContractsTests(unittest.TestCase):
    def test_replay_video_source_is_reported_as_filename_only(self):
        source = r"fixture_bundle\agent_os_synthetic_replay"

        payload = camera_status_payload(
            camera_index=0,
            frame_number=10,
            fps=12.5,
            frame_read_ok=True,
            capture={
                "backend": parse_camera_backend("replay-video"),
                "source": redact_camera_source(source),
            },
            camera_source=source,
        )

        self.assertTrue(looks_like_local_file_source(source))
        self.assertEqual(payload["camera"]["source"], "local-file:agent_os_synthetic_replay")
        self.assertEqual(payload["capture"]["source"], "local-file:agent_os_synthetic_replay")
        self.assertNotIn(str(PureWindowsPath(source).parent), str(payload))

    def test_camera_disconnect_state_stays_structured_and_non_secret(self):
        payload = camera_status_payload(
            camera_index=0,
            frame_number=0,
            fps=0.0,
            frame_read_ok=False,
            capture={
                "backend": "replay-video",
                "read_failures": 3,
                "read_latency_ms": 0.0,
            },
        )

        self.assertFalse(payload["camera"]["frame_read_ok"])
        self.assertEqual(payload["capture"]["read_failures"], 3)
        self.assertEqual(payload["processors"], {"sword_sign": {"enabled": True}})
        self.assertNotIn("token", str(payload).lower())

    def test_camera_status_topic_keeps_feedback_correlation_fields(self):
        status = camera_status_payload(
            camera_index=0,
            frame_number=42,
            fps=29.97,
        )

        envelope = topic_payload(
            CAMERA_STATUS_TOPIC,
            MSG_TYPE_CAMERA_STATUS,
            status,
            sequence=42,
            stamp=123.456,
            frame_id="cam0",
        )

        self.assertEqual(envelope["topic"], CAMERA_STATUS_TOPIC)
        self.assertEqual(envelope["msg_type"], MSG_TYPE_CAMERA_STATUS)
        self.assertEqual(envelope["header"]["seq"], 42)
        self.assertEqual(envelope["header"]["frame_id"], "cam0")
        self.assertEqual(envelope["payload"]["frame_id"], 42)

    def test_binary_frame_payload_is_summary_only_for_routine_events(self):
        payload = compressed_image_binary_payload(byte_length=2048)

        self.assertEqual(payload["transport"], "binary")
        self.assertEqual(payload["byte_length"], 2048)
        self.assertNotIn("data_base64", payload)
        self.assertNotIn("path", payload)


if __name__ == "__main__":
    unittest.main()
