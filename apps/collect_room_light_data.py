from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mediapipe_sword_sign.room_light import (
    DEFAULT_ROOM_LIGHT_LABELS,
    RoomLightFeatureExtractor,
)


METADATA_COLUMNS = (
    "label",
    "timestamp",
    "frame_count",
    "first_frame_id",
    "last_frame_id",
    "duration_seconds",
)


def parse_positive_int(value: str, *, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"{name} must be greater than 0")
    return parsed


def parse_non_negative_int(value: str, *, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"{name} must be 0 or greater")
    return parsed


def parse_window_size(value: str) -> int:
    parsed = parse_positive_int(value, name="--window-size")
    if parsed < 2:
        raise argparse.ArgumentTypeError("--window-size must be 2 or greater")
    return parsed


def parse_positive_float(value: str, *, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be a number") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"{name} must be greater than 0")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect fixed-camera room-light training rows from 2+ frame sequences.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--label", required=True, help=f"one of: {', '.join(DEFAULT_ROOM_LIGHT_LABELS)}")
    parser.add_argument("--output", default="room_light_data.csv")
    parser.add_argument("--camera-index", type=lambda value: parse_non_negative_int(value, name="--camera-index"), default=0)
    parser.add_argument("--window-size", type=parse_window_size, default=2)
    parser.add_argument("--sample-interval", type=lambda value: parse_positive_float(value, name="--sample-interval"), default=1.0)
    parser.add_argument("--max-samples", type=lambda value: parse_non_negative_int(value, name="--max-samples"), default=0)
    parser.add_argument("--save-frames-dir", help="optional directory for sampled JPEG frame sequences")
    parser.add_argument("--jpeg-quality", type=lambda value: parse_positive_int(value, name="--jpeg-quality"), default=80)
    return parser


def append_row(path: Path, row: dict[str, object], fieldnames: list[str]) -> None:
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def save_frame_sequence(
    frames: list[object],
    *,
    directory: Path,
    sample_index: int,
    quality: int,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for index, frame in enumerate(frames):
        path = directory / f"sample_{sample_index:06d}_frame_{index:02d}.jpg"
        cv2.imwrite(
            str(path),
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)],
        )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.label not in DEFAULT_ROOM_LIGHT_LABELS:
        print(
            f"warning: label '{args.label}' is not in the default label set; keeping it as a custom class",
            flush=True,
        )

    output = Path(args.output)
    extractor = RoomLightFeatureExtractor()
    feature_names = extractor.sequence_feature_names
    fieldnames = list(METADATA_COLUMNS) + feature_names
    cap = cv2.VideoCapture(args.camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"camera not available: {args.camera_index}")

    frames: list[object] = []
    feature_history: list[tuple[float, tuple[float, ...]]] = []
    frame_id = 0
    sample_count = 0
    last_sample_at: float | None = None
    save_dir = Path(args.save_frames_dir) if args.save_frames_dir else None

    try:
        print("Collecting room-light samples. Press Ctrl+C to stop.", flush=True)
        while True:
            success, frame = cap.read()
            if not success:
                time.sleep(0.05)
                continue

            frame_id += 1
            stamp = time.time()
            frames.append(frame.copy())
            feature_history.append((stamp, extractor.extract_frame_features(frame)))
            if len(frames) > args.window_size:
                frames.pop(0)
                feature_history.pop(0)

            if len(feature_history) < args.window_size:
                continue
            if last_sample_at is not None and stamp - last_sample_at < args.sample_interval:
                continue

            sequence_features = extractor.extract_sequence_features(
                [features for _stamp, features in feature_history]
            )
            first_stamp = feature_history[0][0]
            row: dict[str, object] = {
                "label": args.label,
                "timestamp": stamp,
                "frame_count": args.window_size,
                "first_frame_id": frame_id - args.window_size + 1,
                "last_frame_id": frame_id,
                "duration_seconds": max(0.0, stamp - first_stamp),
            }
            row.update(
                {
                    name: value
                    for name, value in zip(feature_names, sequence_features)
                }
            )
            append_row(output, row, fieldnames)
            sample_count += 1
            last_sample_at = stamp
            if save_dir is not None:
                save_frame_sequence(
                    frames,
                    directory=save_dir,
                    sample_index=sample_count,
                    quality=args.jpeg_quality,
                )
            print(f"sample={sample_count} label={args.label} output={output}", flush=True)

            if args.max_samples and sample_count >= args.max_samples:
                break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
