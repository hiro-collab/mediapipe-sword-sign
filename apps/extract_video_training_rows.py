from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import mediapipe as mp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mediapipe_sword_sign import FEATURE_DIMENSION, features_from_hand_landmarks  # noqa: E402


LABELS_BY_NAME = {
    "sword": 0,
    "sword_sign": 0,
    "victory": 1,
    "none": 2,
    "other": 2,
}
LABEL_NAMES_BY_ID = {
    0: "sword",
    1: "victory",
    2: "none",
}
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / ".runtime" / "training_rows"


def parse_positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def parse_non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be 0 or greater")
    return parsed


def parse_label(value: str) -> int:
    normalized = str(value).strip().lower()
    if normalized.isdigit():
        parsed = int(normalized)
        if parsed in LABEL_NAMES_BY_ID:
            return parsed
    if normalized in LABELS_BY_NAME:
        return LABELS_BY_NAME[normalized]
    choices = ", ".join(["sword", "victory", "none", "0", "1", "2"])
    raise argparse.ArgumentTypeError(f"--label must be one of: {choices}")


def safe_stem(value: str) -> str:
    allowed: list[str] = []
    for char in str(value).strip().lower():
        if char.isalnum() or char in {"-", "_"}:
            allowed.append(char)
        elif char.isspace() or char in {".", "/"}:
            allowed.append("_")
    return "".join(allowed).strip("_") or "video"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_output_csv(video_path: Path, label_id: int, output_dir: Path) -> Path:
    label_name = LABEL_NAMES_BY_ID[label_id]
    return output_dir / f"{safe_stem(video_path.stem)}_{label_name}_rows.csv"


def resolve_output_csv(
    *,
    requested_output_csv: str,
    output_dir: Path,
    video_path: Path,
    label_id: int,
) -> Path:
    if not requested_output_csv:
        return default_output_csv(video_path, label_id, output_dir)

    requested_csv = Path(requested_output_csv).expanduser()
    if requested_csv.is_absolute():
        raise ValueError("--output-csv must be relative to --output-dir")

    output_csv = (output_dir / requested_csv).resolve()
    try:
        output_csv.relative_to(output_dir)
    except ValueError as exc:
        raise ValueError("--output-csv must stay under --output-dir") from exc
    return output_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract MediaPipe hand landmark training rows from a local video. "
            "Only summary metadata is printed; raw row values are written to CSV."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("video", help="Input local video path.")
    parser.add_argument(
        "--label",
        required=True,
        type=parse_label,
        help="Training label: 0=sword, 1=victory, 2=none.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--output-csv",
        default="",
        help="Optional CSV filename/path. Defaults under --output-dir.",
    )
    parser.add_argument("--every-frames", type=parse_positive_int, default=5)
    parser.add_argument("--start-frame", type=parse_non_negative_int, default=0)
    parser.add_argument(
        "--max-rows",
        type=parse_positive_int,
        default=500,
        help="Maximum hand rows to write. Frames without hands do not count.",
    )
    parser.add_argument(
        "--flip",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Mirror frames before MediaPipe processing, matching collect_data.py.",
    )
    parser.add_argument(
        "--model-complexity",
        type=parse_non_negative_int,
        default=1,
        choices=[0, 1],
        help="MediaPipe Hands model_complexity. collect_data.py uses 1.",
    )
    return parser


def preprocess_frame(frame, *, flip: bool):
    if flip:
        frame = cv2.flip(frame, 1)
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def build_summary(
    *,
    video_path: Path,
    source_sha256: str,
    output_csv: Path,
    summary_path: Path,
    label_id: int,
    every_frames: int,
    start_frame: int,
    max_rows: int,
    flip: bool,
    model_complexity: int,
    frames_read: int,
    sampled_frames: int,
    frames_with_hand: int,
    rows_written: int,
) -> dict[str, object]:
    return {
        "source_video": video_path.name,
        "source_sha256": source_sha256,
        "deterministic": True,
        "label_id": label_id,
        "label_name": LABEL_NAMES_BY_ID[label_id],
        "output_csv": output_csv.name,
        "summary": summary_path.name,
        "feature_dimension": FEATURE_DIMENSION,
        "row_width": FEATURE_DIMENSION + 1,
        "rows_written": rows_written,
        "frames_read": frames_read,
        "sampled_frames": sampled_frames,
        "frames_with_hand": frames_with_hand,
        "frames_without_hand": sampled_frames - frames_with_hand,
        "every_frames": every_frames,
        "start_frame": start_frame,
        "max_rows": max_rows,
        "flip": flip,
        "model_complexity": model_complexity,
        "label_mapping": {
            "0": "sword",
            "1": "victory",
            "2": "none",
        },
    }


def extract_training_rows(
    args: argparse.Namespace,
    *,
    capture_factory=None,
    hands_factory=None,
) -> dict[str, object]:
    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"video not found: {video_path.name}")

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_csv = resolve_output_csv(
        requested_output_csv=args.output_csv,
        output_dir=output_dir,
        video_path=video_path,
        label_id=args.label,
    )
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    summary_path = output_csv.with_suffix(".summary.json")

    capture_factory = capture_factory or cv2.VideoCapture
    hands_factory = hands_factory or mp.solutions.hands.Hands

    capture = capture_factory(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"failed to open video: {video_path.name}")

    counts: Counter[str] = Counter()
    frames_read = 0
    sampled_frames = 0
    frame_number = -1
    rows_written = 0
    source_sha256 = file_sha256(video_path)

    try:
        with hands_factory(model_complexity=args.model_complexity) as hands:
            with output_csv.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                while rows_written < args.max_rows:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    frames_read += 1
                    frame_number += 1
                    if frame_number < args.start_frame:
                        continue
                    if (frame_number - args.start_frame) % args.every_frames != 0:
                        continue

                    sampled_frames += 1
                    image = preprocess_frame(frame, flip=args.flip)
                    results = hands.process(image)
                    hand_landmarks = list(results.multi_hand_landmarks or [])
                    if not hand_landmarks:
                        counts["no_hand"] += 1
                        continue

                    counts["with_hand_frames"] += 1
                    for hand in hand_landmarks:
                        if rows_written >= args.max_rows:
                            break
                        features = features_from_hand_landmarks(hand)
                        writer.writerow([args.label, *features])
                        rows_written += 1
    finally:
        capture.release()

    summary = build_summary(
        video_path=video_path,
        source_sha256=source_sha256,
        output_csv=output_csv,
        summary_path=summary_path,
        label_id=args.label,
        every_frames=args.every_frames,
        start_frame=args.start_frame,
        max_rows=args.max_rows,
        flip=bool(args.flip),
        model_complexity=args.model_complexity,
        frames_read=frames_read,
        sampled_frames=sampled_frames,
        frames_with_hand=int(counts["with_hand_frames"]),
        rows_written=rows_written,
    )
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = extract_training_rows(args)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
