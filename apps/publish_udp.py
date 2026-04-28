from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
import warnings
from dataclasses import dataclass, replace
from functools import cache
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Mapping

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mediapipe_sword_sign import SwordSignDetector, UnsafeModelError
from mediapipe_sword_sign.adapters import UdpGesturePublisher
from mediapipe_sword_sign.types import GestureState


PACKAGE_NAME = "mediapipe-sword-sign"
SOURCE_NAME = "mediapipe_sword_sign"
PREVIEW_WINDOW = "Gesture UDP Preview"
DEFAULT_CAMERA_SCAN_LIMIT = 5
MAX_CAMERA_SCAN_LIMIT = 16
LOCAL_UDP_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class DebugEvery:
    value: float
    unit: str

    def __str__(self) -> str:
        if self.unit == "frames":
            return str(int(self.value))
        return f"{self.value:g}s"


@dataclass
class FpsTracker:
    last_frame_at: float | None = None
    fps: float = 0.0

    def update(self, now: float) -> float:
        if self.last_frame_at is not None:
            elapsed = now - self.last_frame_at
            if elapsed > 0:
                self.fps = 1.0 / elapsed
        self.last_frame_at = now
        return self.fps


@cache
def get_version() -> str:
    try:
        return importlib_metadata.version(PACKAGE_NAME)
    except importlib_metadata.PackageNotFoundError:
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
            return str(tomllib.load(file)["project"]["version"])


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


def parse_optional_interval(value: str) -> DebugEvery | None:
    text = value.strip().lower()
    if text in {"0", "off", "none", "disabled"}:
        return None
    return parse_debug_every(value)


def parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("--port must be between 1 and 65535")
    return port


def parse_non_negative_int(value: str, *, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"{name} must be 0 or greater")
    return parsed


def parse_camera_scan_limit(value: str) -> int:
    parsed = parse_non_negative_int(value, name="--camera-scan-limit")
    if parsed > MAX_CAMERA_SCAN_LIMIT:
        raise argparse.ArgumentTypeError(
            f"--camera-scan-limit must be {MAX_CAMERA_SCAN_LIMIT} or lower"
        )
    return parsed


def parse_non_negative_float(value: str, *, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be a number") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"{name} must be 0 or greater")
    return parsed


def parse_threshold(value: str) -> float:
    parsed = parse_non_negative_float(value, name="--threshold")
    if parsed > 1:
        raise argparse.ArgumentTypeError("--threshold must be between 0 and 1")
    return parsed


def parse_interval(value: str) -> float:
    return parse_non_negative_float(value, name="--interval")


def parse_camera_index(value: str) -> int:
    return parse_non_negative_int(value, name="--camera-index")


def is_local_udp_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in LOCAL_UDP_HOSTS or normalized.startswith("127.")


def validate_runtime_args(args: argparse.Namespace) -> None:
    if not is_local_udp_host(args.host) and not args.allow_remote_udp:
        raise ValueError(
            "refusing to send gesture UDP to a non-local host without --allow-remote-udp"
        )


def format_bool(value: bool) -> str:
    return "true" if value else "false"


def format_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def print_json(payload: Mapping[str, object]) -> None:
    print(format_json(payload), flush=True)


def eprint(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def destination_payload(destination: tuple[str, int]) -> dict[str, object]:
    host, port = destination
    return {"host": host, "port": port}


def best_gesture_snapshot(state: GestureState) -> tuple[str, float]:
    if not state.hand_detected:
        return "none", 0.0
    best = state.best_gesture()
    if best is None:
        return "none", 0.0
    return best.name, best.confidence


def format_debug_summary(
    state: GestureState,
    *,
    frame_number: int,
    camera_index: int,
    destination: tuple[str, int],
) -> str:
    sword = state.sword_sign
    best_name, best_confidence = best_gesture_snapshot(state)
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


def probe_camera(index: int) -> dict[str, object]:
    cap = cv2.VideoCapture(index)
    try:
        available = cap.isOpened()
        payload: dict[str, object] = {
            "index": index,
            "available": bool(available),
        }
        if available:
            payload["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            payload["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            payload["fps"] = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        return payload
    finally:
        cap.release()


def list_camera_payload(*, selected_index: int, scan_limit: int) -> dict[str, object]:
    return {
        "type": "camera_list",
        "source": SOURCE_NAME,
        "selected_camera_index": selected_index,
        "cameras": [
            probe_camera(index)
            for index in range(max(0, scan_limit) + 1)
        ],
    }


def check_model(args: argparse.Namespace) -> dict[str, object]:
    try:
        detector = SwordSignDetector(
            model_path=args.model_path,
            expected_model_sha256=args.model_sha256,
            allow_untrusted_model=args.allow_untrusted_model,
            threshold=args.threshold,
        )
        detector.close()
    except Exception as exc:
        return {"available": False, "error": safe_model_error(exc)}
    return {"available": True}


def safe_model_error(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return "model_not_found"
    if isinstance(exc, UnsafeModelError):
        return "unsafe_model"
    return "model_load_failed"


def health_payload(args: argparse.Namespace) -> dict[str, object]:
    camera = probe_camera(args.camera_index)
    model = check_model(args)
    ok = bool(camera["available"]) and bool(model["available"])
    return {
        "type": "gesture_health",
        "source": SOURCE_NAME,
        "status": "ok" if ok else "error",
        "version": get_version(),
        "camera": {
            "selected_index": args.camera_index,
            "available": bool(camera["available"]),
        },
        "model": model,
        "udp": destination_payload((args.host, args.port)),
    }


def runtime_metadata(
    state: GestureState,
    *,
    frame_number: int,
    fps: float,
) -> dict[str, object]:
    return {
        "frame_id": frame_number,
        "hand_detected": bool(state.hand_detected),
        "primary_gesture": state.primary,
        "fps": round(fps, 3),
    }


def state_with_runtime_metadata(
    state: GestureState,
    *,
    frame_number: int,
    fps: float,
) -> GestureState:
    metadata = dict(state.metadata or {})
    metadata.update(runtime_metadata(state, frame_number=frame_number, fps=fps))
    return replace(state, metadata=metadata)


def status_payload(
    state: GestureState,
    *,
    frame_number: int,
    camera_index: int,
    destination: tuple[str, int],
    fps: float,
) -> dict[str, object]:
    best_name, best_confidence = best_gesture_snapshot(state)
    sword = state.sword_sign
    return {
        "type": "gesture_status",
        "source": SOURCE_NAME,
        "status": "running",
        "timestamp": time.time(),
        "version": get_version(),
        "camera": {
            "selected_index": camera_index,
            "opened": True,
        },
        "udp": destination_payload(destination),
        "frame_id": frame_number,
        "hand_detected": bool(state.hand_detected),
        "primary_gesture": state.primary,
        "sword_sign": {
            "active": bool(sword.active),
            "confidence": float(sword.confidence),
        },
        "best_gesture": {
            "name": best_name,
            "confidence": float(best_confidence),
        },
        "fps": round(fps, 3),
    }


def heartbeat_payload(
    *,
    frame_number: int,
    camera_index: int,
    destination: tuple[str, int],
    fps: float,
) -> dict[str, object]:
    return {
        "type": "gesture_heartbeat",
        "source": SOURCE_NAME,
        "status": "sending",
        "timestamp": time.time(),
        "camera": {
            "selected_index": camera_index,
            "opened": True,
        },
        "udp": destination_payload(destination),
        "frame_id": frame_number,
        "fps": round(fps, 3),
    }


def schema_payload() -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "MediaPipe Sword Sign UDP messages",
        "oneOf": [
            {
                "title": "GestureState",
                "type": "object",
                "required": ["type", "timestamp", "source", "hand_detected", "primary", "gestures"],
                "properties": {
                    "type": {"const": "gesture_state"},
                    "timestamp": {"type": "number"},
                    "source": {"type": "string"},
                    "hand_detected": {"type": "boolean"},
                    "primary": {"type": ["string", "null"]},
                    "gestures": {"type": "object"},
                    "metadata": {
                        "type": "object",
                        "properties": {
                            "frame_id": {"type": "integer"},
                            "hand_detected": {"type": "boolean"},
                            "primary_gesture": {"type": ["string", "null"]},
                            "fps": {"type": "number"},
                        },
                    },
                },
            },
            {
                "title": "GestureStatus",
                "type": "object",
                "required": ["type", "status", "camera", "udp", "frame_id", "fps"],
                "properties": {
                    "type": {"const": "gesture_status"},
                    "status": {"const": "running"},
                    "camera": {"type": "object"},
                    "udp": {"type": "object"},
                    "frame_id": {"type": "integer"},
                    "fps": {"type": "number"},
                },
            },
            {
                "title": "GestureHeartbeat",
                "type": "object",
                "required": ["type", "status", "camera", "udp"],
                "properties": {
                    "type": {"const": "gesture_heartbeat"},
                    "status": {"const": "sending"},
                    "camera": {"type": "object"},
                    "udp": {"type": "object"},
                },
            },
        ],
    }


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
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {get_version()}",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=parse_port, default=8765)
    parser.add_argument("--camera-index", type=parse_camera_index, default=0)
    parser.add_argument(
        "--camera-scan-limit",
        type=parse_camera_scan_limit,
        default=DEFAULT_CAMERA_SCAN_LIMIT,
        help="highest camera index to probe for --list-cameras",
    )
    parser.add_argument("--threshold", type=parse_threshold, default=0.9)
    parser.add_argument("--model-path")
    parser.add_argument("--model-sha256")
    parser.add_argument("--allow-untrusted-model", action="store_true")
    parser.add_argument("--interval", type=parse_interval, default=0.0)
    parser.add_argument(
        "--allow-remote-udp",
        action="store_true",
        help="allow sending gesture state UDP packets to a non-local host",
    )
    parser.add_argument("--print-json", action="store_true")
    parser.add_argument("--debug", action="store_true", help="print one-line state summaries")
    parser.add_argument(
        "--debug-every",
        type=parse_debug_every,
        default=DebugEvery(1, "frames"),
        help="debug interval; bare numbers are frames, add s/sec/seconds for time",
    )
    parser.add_argument(
        "--status-json",
        action="store_true",
        help="print runtime status JSON lines",
    )
    parser.add_argument(
        "--status-every",
        type=parse_debug_every,
        default=DebugEvery(1.0, "seconds"),
        help="status JSON interval; bare numbers are frames, add s/sec/seconds for time",
    )
    parser.add_argument(
        "--heartbeat-every",
        type=parse_optional_interval,
        default=None,
        help="send UDP heartbeat interval; use 0/off/none to disable",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="show an OpenCV preview window with gesture overlay",
    )
    parser.add_argument(
        "--health-json",
        action="store_true",
        help="print one health check JSON object and exit",
    )
    parser.add_argument(
        "--schema-json",
        action="store_true",
        help="print JSON schema for UDP/status messages and exit",
    )
    parser.add_argument(
        "--list-cameras",
        action="store_true",
        help="probe camera indexes and print JSON",
    )
    parser.add_argument(
        "--dry-run",
        "--check-config",
        dest="dry_run",
        action="store_true",
        help="validate model, camera, and UDP config without publishing",
    )
    parser.add_argument(
        "--suppress-protobuf-warnings",
        action="store_true",
        help="hide known protobuf deprecation warnings emitted by MediaPipe",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.suppress_protobuf_warnings:
        suppress_protobuf_deprecation_warnings()

    if args.schema_json:
        print_json(schema_payload())
        return 0

    if args.list_cameras:
        print_json(
            list_camera_payload(
                selected_index=args.camera_index,
                scan_limit=args.camera_scan_limit,
            )
        )
        return 0

    try:
        validate_runtime_args(args)
    except ValueError as exc:
        parser.error(str(exc))

    if args.health_json or args.dry_run:
        payload = health_payload(args)
        print_json(payload)
        return 0 if payload["status"] == "ok" else 1

    cap = cv2.VideoCapture(args.camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"camera not available: {args.camera_index}")

    frame_number = 0
    fps_tracker = FpsTracker()
    last_debug_at: float | None = None
    last_status_at: float | None = None
    last_heartbeat_at: float | None = None
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
            eprint(
                "selected_camera="
                f"{args.camera_index} udp={args.host}:{args.port} version={get_version()}"
            )
            eprint(f"udp_sending destination={args.host}:{args.port}")
            if args.heartbeat_every is not None:
                publisher.publish_payload(
                    heartbeat_payload(
                        frame_number=frame_number,
                        camera_index=args.camera_index,
                        destination=publisher.address,
                        fps=0.0,
                    )
                )
                last_heartbeat_at = time.monotonic()

            while True:
                success, frame = cap.read()
                if not success:
                    time.sleep(0.05)
                    continue

                frame_number += 1
                now = time.monotonic()
                fps = fps_tracker.update(now)
                state = state_with_runtime_metadata(
                    detector.detect(frame, flip=True),
                    frame_number=frame_number,
                    fps=fps,
                )
                publisher.publish(state)
                if args.print_json:
                    print(state.to_json(), flush=True)

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

                if args.status_json and should_print_debug(
                    args.status_every,
                    frame_number=frame_number,
                    last_debug_at=last_status_at,
                    now=now,
                ):
                    print_json(
                        status_payload(
                            state,
                            frame_number=frame_number,
                            camera_index=args.camera_index,
                            destination=publisher.address,
                            fps=fps,
                        )
                    )
                    last_status_at = now

                if args.heartbeat_every is not None and should_print_debug(
                    args.heartbeat_every,
                    frame_number=frame_number,
                    last_debug_at=last_heartbeat_at,
                    now=now,
                ):
                    publisher.publish_payload(
                        heartbeat_payload(
                            frame_number=frame_number,
                            camera_index=args.camera_index,
                            destination=publisher.address,
                            fps=fps,
                        )
                    )
                    last_heartbeat_at = now

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
        eprint("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
