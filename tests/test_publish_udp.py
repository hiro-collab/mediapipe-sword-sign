import argparse
import unittest

from apps.publish_udp import (
    DebugEvery,
    FpsTracker,
    format_debug_summary,
    heartbeat_payload,
    parse_debug_every,
    parse_camera_scan_limit,
    parse_optional_interval,
    parse_port,
    parse_threshold,
    runtime_metadata,
    safe_model_error,
    schema_payload,
    should_print_debug,
    state_with_runtime_metadata,
    status_payload,
    validate_runtime_args,
)
from mediapipe_sword_sign.model_loader import UnsafeModelError
from mediapipe_sword_sign.types import GesturePrediction, GestureState


def make_state() -> GestureState:
    return GestureState(
        timestamp=123.0,
        source="test",
        hand_detected=True,
        primary=None,
        gestures={
            "sword_sign": GesturePrediction(
                name="sword_sign",
                active=False,
                confidence=0.42,
                label=0,
            ),
            "victory": GesturePrediction(
                name="victory",
                active=False,
                confidence=0.84,
                label=1,
            ),
            "none": GesturePrediction(
                name="none",
                active=False,
                confidence=0.02,
                label=2,
            ),
        },
    )


class PublishUdpDebugTests(unittest.TestCase):
    def test_parse_debug_every_accepts_frame_and_second_intervals(self):
        self.assertEqual(parse_debug_every("30"), DebugEvery(30, "frames"))
        self.assertEqual(parse_debug_every("30 frames"), DebugEvery(30, "frames"))
        self.assertEqual(parse_debug_every("2.5s"), DebugEvery(2.5, "seconds"))

    def test_parse_debug_every_rejects_invalid_intervals(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_debug_every("0")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_debug_every("1.5")

    def test_parse_optional_interval_accepts_disabled_values(self):
        self.assertIsNone(parse_optional_interval("off"))
        self.assertIsNone(parse_optional_interval("0"))
        self.assertEqual(parse_optional_interval("5s"), DebugEvery(5.0, "seconds"))

    def test_parse_port_rejects_out_of_range_values(self):
        self.assertEqual(parse_port("8765"), 8765)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_port("0")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_port("65536")

    def test_parse_camera_scan_limit_is_bounded(self):
        self.assertEqual(parse_camera_scan_limit("5"), 5)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_camera_scan_limit("17")

    def test_parse_threshold_is_probability(self):
        self.assertEqual(parse_threshold("0.9"), 0.9)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_threshold("-0.1")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_threshold("1.1")

    def test_validate_runtime_args_requires_remote_udp_opt_in(self):
        validate_runtime_args(argparse.Namespace(host="127.0.0.1", allow_remote_udp=False))
        validate_runtime_args(argparse.Namespace(host="192.0.2.10", allow_remote_udp=True))

        with self.assertRaises(ValueError):
            validate_runtime_args(argparse.Namespace(host="192.0.2.10", allow_remote_udp=False))

    def test_safe_model_error_does_not_return_internal_paths(self):
        self.assertEqual(
            safe_model_error(FileNotFoundError("model file not found: C:/secret/model.pkl")),
            "model_not_found",
        )
        self.assertEqual(
            safe_model_error(UnsafeModelError("refusing C:/secret/model.pkl")),
            "unsafe_model",
        )

    def test_fps_tracker_updates_from_elapsed_time(self):
        tracker = FpsTracker()
        self.assertEqual(tracker.update(10.0), 0.0)
        self.assertAlmostEqual(tracker.update(10.5), 2.0)

    def test_should_print_debug_supports_frame_and_second_intervals(self):
        every_30_frames = DebugEvery(30, "frames")
        self.assertTrue(
            should_print_debug(
                every_30_frames,
                frame_number=1,
                last_debug_at=None,
                now=10.0,
            )
        )
        self.assertFalse(
            should_print_debug(
                every_30_frames,
                frame_number=29,
                last_debug_at=10.0,
                now=11.0,
            )
        )
        self.assertTrue(
            should_print_debug(
                every_30_frames,
                frame_number=30,
                last_debug_at=10.0,
                now=11.0,
            )
        )

        every_2_seconds = DebugEvery(2.0, "seconds")
        self.assertTrue(
            should_print_debug(
                every_2_seconds,
                frame_number=4,
                last_debug_at=None,
                now=10.0,
            )
        )
        self.assertFalse(
            should_print_debug(
                every_2_seconds,
                frame_number=5,
                last_debug_at=10.0,
                now=11.9,
            )
        )
        self.assertTrue(
            should_print_debug(
                every_2_seconds,
                frame_number=6,
                last_debug_at=10.0,
                now=12.0,
            )
        )

    def test_format_debug_summary_includes_required_fields(self):
        summary = format_debug_summary(
            make_state(),
            frame_number=42,
            camera_index=1,
            destination=("127.0.0.1", 8765),
        )

        self.assertEqual(
            summary,
            "frame=42 camera_index=1 hand_detected=true primary=none "
            "sword_sign.active=false sword_sign.confidence=0.420 "
            "best=victory best.confidence=0.840 udp=127.0.0.1:8765",
        )

    def test_format_debug_summary_reports_no_best_gesture_without_hand(self):
        summary = format_debug_summary(
            GestureState.no_hand(source="test", timestamp=123.0),
            frame_number=7,
            camera_index=0,
            destination=("127.0.0.1", 8765),
        )

        self.assertIn("hand_detected=false", summary)
        self.assertIn("best=none best.confidence=0.000", summary)

    def test_runtime_metadata_is_attached_to_gesture_state(self):
        state = state_with_runtime_metadata(
            make_state(),
            frame_number=12,
            fps=29.9876,
        )

        self.assertEqual(
            state.metadata,
            {
                "frame_id": 12,
                "hand_detected": True,
                "primary_gesture": None,
                "fps": 29.988,
            },
        )
        self.assertEqual(runtime_metadata(make_state(), frame_number=1, fps=2.0)["fps"], 2.0)

    def test_status_payload_includes_runtime_fields(self):
        payload = status_payload(
            make_state(),
            frame_number=42,
            camera_index=1,
            destination=("127.0.0.1", 8765),
            fps=30.0,
        )

        self.assertEqual(payload["type"], "gesture_status")
        self.assertEqual(payload["camera"]["selected_index"], 1)
        self.assertEqual(payload["udp"], {"host": "127.0.0.1", "port": 8765})
        self.assertEqual(payload["frame_id"], 42)
        self.assertEqual(payload["best_gesture"]["name"], "victory")
        self.assertEqual(payload["fps"], 30.0)

    def test_heartbeat_payload_reports_sending_destination(self):
        payload = heartbeat_payload(
            frame_number=3,
            camera_index=0,
            destination=("127.0.0.1", 8765),
            fps=15.5,
        )

        self.assertEqual(payload["type"], "gesture_heartbeat")
        self.assertEqual(payload["status"], "sending")
        self.assertEqual(payload["udp"], {"host": "127.0.0.1", "port": 8765})

    def test_schema_payload_documents_message_types(self):
        schema = schema_payload()
        titles = {item["title"] for item in schema["oneOf"]}

        self.assertIn("GestureState", titles)
        self.assertIn("GestureStatus", titles)
        self.assertIn("GestureHeartbeat", titles)


if __name__ == "__main__":
    unittest.main()
