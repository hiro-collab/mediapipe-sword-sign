import csv
import json
from dataclasses import dataclass
from pathlib import Path
import tempfile
import unittest

import numpy as np

from apps.extract_video_training_rows import (
    FEATURE_DIMENSION,
    build_parser,
    extract_training_rows,
    parse_label,
)


@dataclass
class Landmark:
    x: float
    y: float
    z: float


class HandLandmarks:
    def __init__(self):
        self.landmark = [
            Landmark(float(index), float(index + 10), float(index + 20))
            for index in range(21)
        ]


class FakeResults:
    def __init__(self, hands):
        self.multi_hand_landmarks = hands


class FakeHands:
    def __init__(self, *, model_complexity):
        self.model_complexity = model_complexity
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def process(self, image):
        self.calls.append(image)
        return FakeResults([HandLandmarks()])


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


class ExtractVideoTrainingRowsTests(unittest.TestCase):
    def test_parse_label_accepts_names_and_ids(self):
        self.assertEqual(parse_label("sword"), 0)
        self.assertEqual(parse_label("victory"), 1)
        self.assertEqual(parse_label("none"), 2)
        self.assertEqual(parse_label("0"), 0)
        with self.assertRaises(Exception):
            parse_label("open_hand")

    def test_extract_training_rows_writes_csv_and_redacted_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "gesture.victory.20260603.mp4"
            video.write_bytes(b"local video fixture")
            output_dir = root / "generated"
            frames = [
                np.zeros((2, 2, 3), dtype=np.uint8),
                np.ones((2, 2, 3), dtype=np.uint8),
                np.full((2, 2, 3), 2, dtype=np.uint8),
            ]
            capture = FakeCapture(frames)
            fake_hands = FakeHands(model_complexity=1)

            args = build_parser().parse_args(
                [
                    str(video),
                    "--label",
                    "victory",
                    "--output-dir",
                    str(output_dir),
                    "--every-frames",
                    "2",
                    "--max-rows",
                    "2",
                    "--no-flip",
                ]
            )

            summary = extract_training_rows(
                args,
                capture_factory=lambda _path: capture,
                hands_factory=lambda model_complexity: fake_hands,
            )

            with (output_dir / summary["output_csv"]).open(
                newline="", encoding="utf-8"
            ) as handle:
                rows = list(csv.reader(handle))
            saved_summary = json.loads(
                (output_dir / summary["summary"]).read_text(encoding="utf-8")
            )

        self.assertTrue(capture.released)
        self.assertEqual(len(fake_hands.calls), 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]), FEATURE_DIMENSION + 1)
        self.assertEqual(rows[0][0], "1")
        self.assertEqual(summary["source_video"], "gesture.victory.20260603.mp4")
        self.assertEqual(summary["label_name"], "victory")
        self.assertEqual(summary["sampled_frames"], 2)
        self.assertEqual(summary["rows_written"], 2)
        self.assertEqual(summary["frames_with_hand"], 2)
        self.assertRegex(summary["source_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn(str(root), json.dumps(summary))
        self.assertEqual(saved_summary["output_csv"], summary["output_csv"])

    def test_output_csv_must_stay_under_output_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "gesture.mp4"
            video.write_bytes(b"local video fixture")
            args = build_parser().parse_args(
                [
                    str(video),
                    "--label",
                    "sword",
                    "--output-dir",
                    str(root / "generated"),
                    "--output-csv",
                    "..\\outside.csv",
                ]
            )

            with self.assertRaisesRegex(ValueError, "--output-csv"):
                extract_training_rows(args)

    def test_missing_video_error_uses_basename_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "private" / "missing-source.mp4"
            args = build_parser().parse_args(
                [
                    str(missing),
                    "--label",
                    "sword",
                ]
            )

            with self.assertRaises(FileNotFoundError) as caught:
                extract_training_rows(args)

        message = str(caught.exception)
        self.assertIn("missing-source.mp4", message)
        self.assertNotIn(str(root), message)


if __name__ == "__main__":
    unittest.main()
