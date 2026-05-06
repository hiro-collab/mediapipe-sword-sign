import base64
import math
import time
import unittest

import cv2
import numpy as np

from apps.camera_hub_gui import (
    MAX_WS_MESSAGE_BYTES,
    MAX_WS_QUEUE,
    HubTopicRuntime,
    compact_envelope_for_display,
    decode_binary_compressed_image,
    decode_compressed_image,
    decode_jpeg_bytes,
    envelope_age_seconds,
    format_ms,
    redact_url_for_display,
    safe_error_text,
    summarize_camera_status,
    summarize_gesture_payload,
)
from mediapipe_sword_sign.topics import (
    CAMERA_IMAGE_COMPRESSED_TOPIC,
    MSG_TYPE_COMPRESSED_IMAGE,
    binary_topic_message,
)


class CameraHubGuiTests(unittest.TestCase):
    def test_summarize_gesture_payload_reads_raw_and_stable_state(self):
        summary = summarize_gesture_payload(
            {
                "type": "gesture_state",
                "primary": "sword_sign",
                "gestures": {
                    "sword_sign": {"active": True, "confidence": 0.95},
                    "victory": {"active": False, "confidence": 0.1},
                },
                "stable": {
                    "gestures": {
                        "sword_sign": {
                            "active": True,
                            "activated": True,
                            "released": False,
                            "held_for": 0.7,
                        }
                    }
                },
            }
        )

        self.assertEqual(summary.primary, "sword_sign")
        self.assertEqual(summary.best_name, "sword_sign")
        self.assertAlmostEqual(summary.best_confidence, 0.95)
        self.assertTrue(summary.target_active)
        self.assertTrue(summary.stable_active)
        self.assertTrue(summary.stable_activated)
        self.assertAlmostEqual(summary.stable_held_for, 0.7)

    def test_summarize_camera_status_reads_frame_state(self):
        summary = summarize_camera_status(
            {
                "type": "camera_status",
                "frame_id": 12,
                "fps": 29.97,
                "camera": {
                    "selected_index": 0,
                    "opened": True,
                    "frame_read_ok": False,
                },
                "capture": {
                    "frame_age_ms": 34.5,
                    "read_latency_ms": 2.25,
                    "read_failures": 3,
                },
                "processors": {
                    "sword_sign": {
                        "inference_ms": 11.5,
                        "publish_age_ms": 46.75,
                    }
                },
            }
        )

        self.assertEqual(summary.camera_index, 0)
        self.assertTrue(summary.opened)
        self.assertFalse(summary.frame_read_ok)
        self.assertAlmostEqual(summary.fps, 29.97)
        self.assertEqual(summary.frame_id, 12)
        self.assertAlmostEqual(summary.frame_age_ms, 34.5)
        self.assertAlmostEqual(summary.read_latency_ms, 2.25)
        self.assertEqual(summary.read_failures, 3)
        self.assertAlmostEqual(summary.gesture_inference_ms, 11.5)
        self.assertAlmostEqual(summary.gesture_publish_age_ms, 46.75)

    def test_summarize_camera_status_rejects_non_finite_metrics(self):
        summary = summarize_camera_status(
            {
                "type": "camera_status",
                "fps": "nan",
                "frame_id": float("inf"),
                "camera": {"selected_index": 0, "opened": True},
                "capture": {
                    "frame_age_ms": float("nan"),
                    "read_latency_ms": float("inf"),
                    "read_failures": float("inf"),
                },
                "processors": {
                    "sword_sign": {
                        "inference_ms": float("nan"),
                        "publish_age_ms": float("inf"),
                    }
                },
            }
        )

        self.assertIsNone(summary.fps)
        self.assertIsNone(summary.frame_id)
        self.assertIsNone(summary.frame_age_ms)
        self.assertIsNone(summary.read_latency_ms)
        self.assertIsNone(summary.read_failures)
        self.assertIsNone(summary.gesture_inference_ms)
        self.assertIsNone(summary.gesture_publish_age_ms)

    def test_decode_compressed_image_decodes_jpeg_frame(self):
        frame = np.full((12, 16, 3), 128, dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", frame)
        self.assertTrue(ok)

        decoded = decode_compressed_image(
            {
                "type": "compressed_image",
                "format": "jpeg",
                "data_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
            }
        )

        self.assertEqual(decoded.shape, frame.shape)

    def test_decode_binary_compressed_image_decodes_jpeg_frame(self):
        frame = np.full((12, 16, 3), 96, dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", frame)
        self.assertTrue(ok)

        decoded = decode_binary_compressed_image(
            {"type": "compressed_image", "format": "jpeg"},
            encoded.tobytes(),
        )

        self.assertEqual(decoded.shape, frame.shape)

    def test_decode_jpeg_bytes_rejects_invalid_bytes(self):
        with self.assertRaises(ValueError):
            decode_jpeg_bytes(b"not-jpeg")

    def test_decode_compressed_image_rejects_non_jpeg_payload(self):
        with self.assertRaises(ValueError):
            decode_compressed_image({"format": "png", "data_base64": "abc"})

    def test_compact_envelope_for_display_omits_base64_data(self):
        compact = compact_envelope_for_display(
            {
                "topic": "/camera/color/image_raw/compressed",
                "payload": {"data_base64": "abcdef", "format": "jpeg"},
            }
        )

        self.assertEqual(compact["payload"]["data_base64"], "<omitted 6 base64 chars>")

    def test_envelope_age_seconds_uses_header_stamp(self):
        age = envelope_age_seconds({"header": {"stamp": time.time() - 0.5}})

        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 0.0)
        self.assertLess(age, 2.0)

    def test_envelope_age_seconds_returns_none_without_stamp(self):
        self.assertIsNone(envelope_age_seconds({}))
        self.assertIsNone(envelope_age_seconds({"header": {"stamp": "bad"}}))
        self.assertIsNone(envelope_age_seconds({"header": {"stamp": math.nan}}))

    def test_format_ms_rejects_non_finite_values(self):
        self.assertEqual(format_ms(None), "-")
        self.assertEqual(format_ms(math.nan), "-")
        self.assertEqual(format_ms(math.inf), "-")
        self.assertEqual(format_ms(12.34), "12.3 ms")

    def test_safe_error_text_redacts_url_credentials_and_query_tokens(self):
        error = RuntimeError(
            "failed ws://user:pass@example.test:8765/path?token=secret&debug=1"
        )

        text = safe_error_text(error)

        self.assertNotIn("secret", text)
        self.assertNotIn("user:pass", text)
        self.assertIn("token=%3Credacted%3E", text)
        self.assertIn("debug=1", text)

    def test_redact_url_for_display_redacts_query_token(self):
        url = redact_url_for_display("ws://127.0.0.1:8765?token=secret")

        self.assertNotIn("secret", url)
        self.assertIn("token=%3Credacted%3E", url)

    def test_websocket_client_limits_are_bounded(self):
        self.assertGreater(MAX_WS_MESSAGE_BYTES, 0)
        self.assertLessEqual(MAX_WS_MESSAGE_BYTES, 8 * 1024 * 1024)
        self.assertGreater(MAX_WS_QUEUE, 0)
        self.assertLessEqual(MAX_WS_QUEUE, 8)

    def test_runtime_keeps_latest_binary_frame_outside_event_queue(self):
        frame = np.full((12, 16, 3), 64, dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", frame)
        self.assertTrue(ok)
        encoded_bytes = encoded.tobytes()
        runtime = HubTopicRuntime()

        runtime._handle_binary_message(
            binary_topic_message(
                CAMERA_IMAGE_COMPRESSED_TOPIC,
                MSG_TYPE_COMPRESSED_IMAGE,
                {
                    "type": "compressed_image",
                    "format": "jpeg",
                    "encoding": "bgr8",
                    "transport": "binary",
                    "byte_length": len(encoded_bytes),
                },
                encoded_bytes,
                sequence=7,
                stamp=1.5,
            )
        )

        update = runtime.take_latest_frame()

        self.assertIsNotNone(update)
        envelope, decoded = update
        self.assertEqual(envelope["topic"], CAMERA_IMAGE_COMPRESSED_TOPIC)
        self.assertEqual(envelope["header"]["seq"], 7)
        self.assertEqual(decoded.shape, frame.shape)
        self.assertIsNone(runtime.take_latest_frame())


if __name__ == "__main__":
    unittest.main()
