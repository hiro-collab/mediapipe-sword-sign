import json
from pathlib import Path
import tempfile
import unittest

from apps.extract_debug_frames import (
    build_parser,
    extract_frames,
    frame_filename,
    parse_image_format,
    parse_jpeg_quality,
)
from mediapipe_sword_sign import (
    GESTURE_NONE,
    GESTURE_SWORD_SIGN,
    GesturePrediction,
    GestureState,
)


class FakeCapture:
    def __init__(self, frames):
        self.frames = list(frames)
        self.index = 0
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        if self.index >= len(self.frames):
            return False, None
        frame = self.frames[self.index]
        self.index += 1
        return True, frame

    def release(self):
        self.released = True


class FakeDetector:
    def __init__(self):
        self.closed = False
        self.calls = []

    def __enter__(self):
        return self

    def detect(self, frame, *, flip, timestamp):
        self.calls.append((frame, flip, timestamp))
        if timestamp == 0:
            return GestureState.no_hand(
                source="fake-detector",
                timestamp=timestamp,
            )
        return GestureState(
            timestamp=timestamp,
            source="fake-detector",
            hand_detected=True,
            primary=GESTURE_SWORD_SIGN,
            gestures={
                GESTURE_SWORD_SIGN: GesturePrediction(
                    name=GESTURE_SWORD_SIGN,
                    active=True,
                    confidence=0.98765,
                    label=0,
                ),
                GESTURE_NONE: GesturePrediction(
                    name=GESTURE_NONE,
                    active=False,
                    confidence=0.01235,
                    label=2,
                ),
            },
        )

    def close(self):
        self.closed = True


class ExtractDebugFramesTests(unittest.TestCase):
    def test_image_format_and_jpeg_quality_validation(self):
        self.assertEqual(parse_image_format(" JPEG "), "jpg")
        self.assertEqual(parse_image_format(".png"), "png")
        self.assertEqual(parse_jpeg_quality("100"), 100)
        with self.assertRaises(Exception):
            parse_image_format("gif")
        with self.assertRaises(Exception):
            parse_jpeg_quality("101")

    def test_frame_filename_uses_safe_prefix_label_and_extension(self):
        self.assertEqual(
            frame_filename(
                "Hand Movie",
                frame_number=12,
                label="Sword Sign",
                confidence=0.98765,
                image_format="jpeg",
            ),
            "hand_movie_f000012_sword_sign_0.988.jpg",
        )

    def test_extract_frames_writes_sampled_fixtures_manifest_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "hand_movie.mp4"
            video.write_bytes(b"local video placeholder")
            output_dir = root / "fixtures"
            detector = FakeDetector()
            capture = FakeCapture(["frame-0", "frame-1", "frame-2", "frame-3"])
            written_images = []

            def write_image(path, frame, *, image_format, jpeg_quality):
                written_images.append((path.name, frame, image_format, jpeg_quality))
                path.write_text(f"fixture {frame}\n", encoding="utf-8")
                return True

            args = build_parser().parse_args(
                [
                    str(video),
                    "--output-dir",
                    str(output_dir),
                    "--every-frames",
                    "2",
                    "--max-frames",
                    "2",
                    "--prefix",
                    "Hand Movie",
                    "--image-format",
                    "jpg",
                    "--jpeg-quality",
                    "80",
                    "--no-flip",
                ]
            )

            summary = extract_frames(
                args,
                capture_factory=lambda _path: capture,
                detector_factory=lambda _args: detector,
                image_writer=write_image,
            )

            manifest_rows = [
                json.loads(line)
                for line in (output_dir / "manifest.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            saved_summary = json.loads(
                (output_dir / "summary.json").read_text(encoding="utf-8")
            )

        self.assertEqual(len(written_images), 2)
        self.assertEqual([call[2] for call in detector.calls], [0, 2])
        self.assertFalse(detector.calls[0][1])
        self.assertTrue(detector.closed)
        self.assertTrue(capture.released)
        self.assertEqual(summary["sampled_frames"], 2)
        self.assertEqual(saved_summary["labels"], {"no_hand": 1, "sword_sign": 1})
        self.assertEqual(manifest_rows[0]["source_video"], "hand_movie.mp4")
        self.assertEqual(manifest_rows[0]["label"], "no_hand")
        self.assertEqual(manifest_rows[1]["label"], "sword_sign")
        self.assertEqual(manifest_rows[1]["best_confidence"], 0.98765)
        self.assertTrue(manifest_rows[1]["image"].endswith(".jpg"))


if __name__ == "__main__":
    unittest.main()
