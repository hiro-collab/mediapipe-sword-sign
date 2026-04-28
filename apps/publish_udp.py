from __future__ import annotations

import argparse
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mediapipe_sword_sign import SwordSignDetector
from mediapipe_sword_sign.adapters import UdpGesturePublisher
from mediapipe_sword_sign.types import GestureState


PREVIEW_WINDOW = "Gesture UDP Preview"


@dataclass(frozen=True)
class DebugEvery:
    value: float
    unit: str

    def __str__(self) -> str:
        if self.unit == "frames":
            return str(int(self.value))
        return f"{self.value:g}s"


def parse_debug_every(value: str) -> DebugEvery:
    text = value.strip().lower()
    if not text:
        raise argparse.ArgumentTypeError("--debug-every must be a positive interval")

    unit = "frames"
    number_text = text
    suffixes = (
        ("seconds", "seconds"),
        ("second", "seconds"),
        ("secs", "seconds"),
        ("sec", "seconds"),
        ("frames", "frames"),
        ("frame", "frames"),
        ("f", "frames"),
        ("s", "seconds"),
    )
    for suffix, suffix_unit in suffixes:
        if text.endswith(suffix):
            unit = suffix_unit
            number_text = text[: -len(suffix)].strip()
            break

    try:
        parsed = float(number_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--debug-every must be a number, optionally suffixed with frames/f or seconds/s"
        ) from exc

    if parsed <= 0:
        raise argparse.ArgumentTypeError("--debug-every must be greater than 0")
    if unit == "frames" and not parsed.is_integer():
        raise argparse.ArgumentTypeError("frame-based --debug-every must be an integer")

    return DebugEvery(value=int(parsed) if unit == "frames" else parsed, unit=unit)


def format_bool(value: bool) -> str:
    return "true" if value else "false"


def format_debug_summary(
    state: GestureState,
    *,
    frame_number: int,
    camera_index: int,
    destination: tuple[str, int],
) -> str:
    sword = state.sword_sign
    if state.hand_detected:
        best = state.best_gesture()
        best_name = best.name if best else "none"
        best_confidence = best.confidence if best else 0.0
    else:
        best_name = "none"
        best_confidence = 0.0
    host, port = destination
    return (
        f"frame={frame_number} "
        f"camera_index={camera_index} "
        f"hand_detected={format_bool(state.hand_detected)} "
        f"primary={state.primary or 'none'} "
        f"sword_sign.active={format_bool(sword.active)} "
        f"sword_sign.confidence={sword.confidence:.3f} "
        f"best={best_name} "
        f"best.confidence={best_confidence:.3f} "
        f"udp={host}:{port}"
    )


def should_print_debug(
    debug_every: DebugEvery,
    *,
    frame_number: int,
    last_debug_at: float | None,
    now: float,
) -> bool:
    if debug_every.unit == "frames":
        return frame_number == 1 or frame_number % int(debug_every.value) == 0
    return last_debug_at is None or now - last_debug_at >= debug_every.value


def suppress_protobuf_deprecation_warnings() -> None:
    for category in (DeprecationWarning, UserWarning):
        warnings.filterwarnings(
            "ignore",
            message=r".*SymbolDatabase\.GetPrototype\(\).*deprecated.*",
            category=category,
            module=r"google\.protobuf\..*",
        )
        warnings.filterwarnings(
            "ignore",
            message=r".*MessageFactory.*deprecated.*",
            category=category,
            module=r"google\.protobuf\..*",
        )


def draw_preview_overlay(
    frame,
    state: GestureState,
    *,
    destination: tuple[str, int],
) -> None:
    sword = state.sword_sign
    host, port = destination
    color = (0, 180, 0) if sword.active else (0, 0, 220)
    lines = (
        f"primary: {state.primary or 'none'}",
        f"sword confidence: {sword.confidence:.3f}",
        f"hand detected: {format_bool(state.hand_detected)}",
        f"destination: {host}:{port}",
    )
    for index, line in enumerate(lines):
        cv2.putText(
            frame,
            line,
            (10, 30 + index * 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            color,
            2,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish GestureState JSON over UDP.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--threshold", type=float, default=0.9)
    parser.add_argument("--model-path")
    parser.add_argument("--model-sha256")
    parser.add_argument("--allow-untrusted-model", action="store_true")
    parser.add_argument("--interval", type=float, default=0.0)
    parser.add_argument("--print-json", action="store_true")
    parser.add_argument("--debug", action="store_true", help="print one-line state summaries")
    parser.add_argument(
        "--debug-every",
        type=parse_debug_every,
        default=DebugEvery(1, "frames"),
        help="debug interval; bare numbers are frames, add s/sec/seconds for time",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="show an OpenCV preview window with gesture overlay",
    )
    parser.add_argument(
        "--suppress-protobuf-warnings",
        action="store_true",
        help="hide known protobuf deprecation warnings emitted by MediaPipe",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.suppress_protobuf_warnings:
        suppress_protobuf_deprecation_warnings()

    cap = cv2.VideoCapture(args.camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"camera not available: {args.camera_index}")

    frame_number = 0
    last_debug_at: float | None = None
    try:
        with (
            SwordSignDetector(
                model_path=args.model_path,
                expected_model_sha256=args.model_sha256,
                allow_untrusted_model=args.allow_untrusted_model,
                threshold=args.threshold,
            ) as detector,
            UdpGesturePublisher(args.host, args.port) as publisher,
        ):
            while True:
                success, frame = cap.read()
                if not success:
                    time.sleep(0.05)
                    continue

                frame_number += 1
                state = detector.detect(frame, flip=True)
                publisher.publish(state)
                if args.print_json:
                    print(state.to_json(), flush=True)

                now = time.monotonic()
                if args.debug and should_print_debug(
                    args.debug_every,
                    frame_number=frame_number,
                    last_debug_at=last_debug_at,
                    now=now,
                ):
                    print(
                        format_debug_summary(
                            state,
                            frame_number=frame_number,
                            camera_index=args.camera_index,
                            destination=publisher.address,
                        ),
                        flush=True,
                    )
                    last_debug_at = now

                if args.preview:
                    display = cv2.flip(frame, 1)
                    draw_preview_overlay(display, state, destination=publisher.address)
                    cv2.imshow(PREVIEW_WINDOW, display)
                    if cv2.waitKey(1) & 0xFF == 27:
                        break

                if args.interval > 0:
                    time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if args.preview:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
