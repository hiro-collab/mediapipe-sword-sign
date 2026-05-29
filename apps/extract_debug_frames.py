from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mediapipe_sword_sign import SwordSignDetector  # noqa: E402


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "tests" / "pict_for_debug" / "hand_movie"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "gesture_model.pkl"
IMAGE_FORMATS = {"jpg", "jpeg", "png"}


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


def parse_probability(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a number") from exc
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("value must be between 0 and 1")
    return parsed


def parse_jpeg_quality(value: str) -> int:
    parsed = parse_positive_int(value)
    if parsed > 100:
        raise argparse.ArgumentTypeError("--jpeg-quality must be between 1 and 100")
    return parsed


def parse_image_format(value: str) -> str:
    parsed = value.strip().lower().lstrip(".")
    if parsed not in IMAGE_FORMATS:
        choices = ", ".join(sorted(IMAGE_FORMATS))
        raise argparse.ArgumentTypeError(f"--image-format must be one of: {choices}")
    return "jpg" if parsed == "jpeg" else parsed


def safe_label(value: object) -> str:
    text = str(value or "inactive").strip().lower()
    allowed = []
    for char in text:
        if char.isalnum() or char in {"-", "_"}:
            allowed.append(char)
        elif char.isspace():
            allowed.append("_")
    label = "".join(allowed).strip("_")
    return label or "inactive"


def best_confidence(state) -> float:
    best = state.best_gesture()
    return float(best.confidence) if best is not None else 0.0


def state_label(state) -> str:
    if not state.hand_detected:
        return "no_hand"
    return safe_label(state.primary or "inactive")


def frame_filename(
    prefix: str,
    *,
    frame_number: int,
    label: str,
    confidence: float,
    image_format: str,
) -> str:
    return (
        f"{safe_label(prefix)}_f{frame_number:06d}_"
        f"{safe_label(label)}_{confidence:.3f}.{parse_image_format(image_format)}"
    )


def state_manifest_fields(state) -> dict[str, object]:
    confidence = best_confidence(state)
    return {
        "label": state_label(state),
        "hand_detected": bool(state.hand_detected),
        "primary": state.primary,
        "best_confidence": round(confidence, 6),
        "gestures": {
            name: {
                "active": bool(prediction.active),
                "confidence": round(float(prediction.confidence), 6),
            }
            for name, prediction in state.gestures.items()
        },
    }


def build_manifest_row(
    *,
    source_video: Path,
    frame_number: int,
    image: str,
    state,
) -> dict[str, object]:
    return {
        "source_video": source_video.name,
        "frame_number": frame_number,
        "image": image,
        **state_manifest_fields(state),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract sampled frames from a local video and write detector-labeled "
            "debug fixtures plus JSON manifests."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("video", help="Input video path, for example hand_movie.mp4.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--prefix", default="")
    parser.add_argument("--every-frames", type=parse_positive_int, default=15)
    parser.add_argument("--start-frame", type=parse_non_negative_int, default=0)
    parser.add_argument(
        "--max-frames",
        type=parse_positive_int,
        default=120,
        help="Maximum sampled frames to save.",
    )
    parser.add_argument(
        "--image-format",
        type=parse_image_format,
        default="png",
        help="Image format for extracted fixtures.",
    )
    parser.add_argument("--jpeg-quality", type=parse_jpeg_quality, default=95)
    parser.add_argument("--gesture-threshold", type=parse_probability, default=0.9)
    parser.add_argument("--gesture-model-path", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument(
        "--gesture-model-complexity",
        type=parse_non_negative_int,
        default=0,
        choices=[0, 1],
    )
    parser.add_argument(
        "--flip",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Mirror frames before detection, matching Camera Hub runtime behavior.",
    )
    return parser


def default_detector(args: argparse.Namespace) -> SwordSignDetector:
    return SwordSignDetector(
        model_path=args.gesture_model_path,
        threshold=args.gesture_threshold,
        model_complexity=args.gesture_model_complexity,
    )


def write_image(path: Path, frame, *, image_format: str, jpeg_quality: int) -> bool:
    params = []
    if parse_image_format(image_format) == "jpg":
        params = [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)]
    return bool(cv2.imwrite(str(path), frame, params))


def extract_frames(
    args: argparse.Namespace,
    *,
    capture_factory=None,
    detector_factory=None,
    image_writer=None,
) -> dict[str, object]:
    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"video not found: {video_path}")

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix.strip() or video_path.stem
    image_format = parse_image_format(args.image_format)
    manifest_path = output_dir / "manifest.jsonl"
    summary_path = output_dir / "summary.json"

    capture_factory = capture_factory or cv2.VideoCapture
    detector_factory = detector_factory or default_detector
    image_writer = image_writer or write_image

    capture = capture_factory(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"failed to open video: {video_path}")

    detector = detector_factory(args)
    detector.__enter__()

    counts: Counter[str] = Counter()
    saved = 0
    frame_number = -1
    try:
        with manifest_path.open("w", encoding="utf-8") as manifest:
            while saved < args.max_frames:
                ok, frame = capture.read()
                if not ok:
                    break
                frame_number += 1
                if frame_number < args.start_frame:
                    continue
                if (frame_number - args.start_frame) % args.every_frames != 0:
                    continue

                state = detector.detect(frame, flip=args.flip, timestamp=frame_number)
                label = state_label(state)
                confidence = best_confidence(state)
                filename = frame_filename(
                    prefix,
                    frame_number=frame_number,
                    label=label,
                    confidence=confidence,
                    image_format=image_format,
                )
                image_path = output_dir / filename
                if not image_writer(
                    image_path,
                    frame,
                    image_format=image_format,
                    jpeg_quality=args.jpeg_quality,
                ):
                    raise RuntimeError(f"failed to write image: {image_path}")
                row = build_manifest_row(
                    source_video=video_path,
                    frame_number=frame_number,
                    image=filename,
                    state=state,
                )
                manifest.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                counts[label] += 1
                saved += 1
    finally:
        detector.close()
        capture.release()

    summary = {
        "source_video": video_path.name,
        "output_dir": str(output_dir),
        "sampled_frames": saved,
        "every_frames": args.every_frames,
        "start_frame": args.start_frame,
        "flip": bool(args.flip),
        "image_format": image_format,
        "labels": dict(sorted(counts.items())),
        "manifest": str(manifest_path),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = extract_frames(args)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
