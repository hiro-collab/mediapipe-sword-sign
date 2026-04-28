import argparse
import unittest

from apps.publish_udp import (
    DebugEvery,
    format_debug_summary,
    parse_debug_every,
    should_print_debug,
)
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


if __name__ == "__main__":
    unittest.main()
