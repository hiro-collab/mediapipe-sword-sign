from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tomllib
import uuid
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import cache
from importlib import metadata as importlib_metadata
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mediapipe_sword_sign import SwordSignDetector, UnsafeModelError
from mediapipe_sword_sign.adapters import UdpGesturePublisher
from mediapipe_sword_sign.temporal import GestureHoldState, GestureHoldTracker
from mediapipe_sword_sign.types import GESTURE_SWORD_SIGN, GESTURE_VICTORY, GestureState


PACKAGE_NAME = "mediapipe-sword-sign"
SOURCE_NAME = "mediapipe_sword_sign"
PREVIEW_WINDOW = "Gesture UDP Preview"
DEFAULT_CAMERA_SCAN_LIMIT = 5
MAX_CAMERA_SCAN_LIMIT = 16
LOCAL_UDP_HOSTS = {"127.0.0.1", "localhost", "::1"}
UDP_AUTH_TOKEN_ENV = "SWORD_VOICE_AGENT_AUTH_TOKEN"
DEFAULT_THRESHOLD = 0.9
DEFAULT_HOLD_SECONDS = 0.5
DEFAULT_RELEASE_GRACE_SECONDS = 0.1
LOW_LATENCY_THRESHOLD = 0.8
LOW_LATENCY_HOLD_SECONDS = 0.1
LOW_LATENCY_RELEASE_GRACE_SECONDS = 0.05
EDGE_GESTURE_ACTIVE = "gesture_active"
EDGE_GESTURE_RELEASED = "gesture_released"
REDACTED_OUTPUT_VALUE = "[redacted]"
REDACTED_OUTPUT_KEYS = {
    "api_key",
    "auth_token",
    "detected_at",
    "detected_at_monotonic",
    "sent_at",
    "sent_at_monotonic",
    "timestamp",
    "token",
    "turn_id",
}


@dataclass(frozen=True)
class LatencyProfile:
    threshold: float
    hold_seconds: float
    release_grace_seconds: float


LATENCY_PROFILES: dict[str, LatencyProfile] = {
    "default": LatencyProfile(
        threshold=DEFAULT_THRESHOLD,
        hold_seconds=DEFAULT_HOLD_SECONDS,
        release_grace_seconds=DEFAULT_RELEASE_GRACE_SECONDS,
    ),
    "low": LatencyProfile(
        threshold=LOW_LATENCY_THRESHOLD,
        hold_seconds=LOW_LATENCY_HOLD_SECONDS,
        release_grace_seconds=LOW_LATENCY_RELEASE_GRACE_SECONDS,
    ),
}


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


def apply_latency_profile(args: argparse.Namespace) -> argparse.Namespace:
    profile = LATENCY_PROFILES[args.latency_profile]
    if args.threshold is None:
        args.threshold = profile.threshold
    if args.hold_seconds is None:
        args.hold_seconds = profile.hold_seconds
    if args.release_grace_seconds is None:
        args.release_grace_seconds = profile.release_grace_seconds
    return args


def apply_publish_options(args: argparse.Namespace) -> argparse.Namespace:
    if getattr(args, "edge_only", False):
        args.state_every = None
    return args


def resolve_udp_auth_token(
    *,
    auth_token: str | None,
    auth_token_env: str | None,
) -> str | None:
    if auth_token is not None:
        token = auth_token.strip()
        if not token:
            raise ValueError("--auth-token must not be empty")
        return token

    if not auth_token_env:
        return None
    env_name = auth_token_env.strip()
    if not env_name:
        return None
    token = os.environ.get(env_name)
    if token is None:
        return None
    token = token.strip()
    return token or None


def format_bool(value: bool) -> str:
    return "true" if value else "false"


def format_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def print_json(payload: Mapping[str, object]) -> None:
    print(format_json(payload), flush=True)


def should_redact_output_key(key: str) -> bool:
    normalized = key.lower()
    return (
        normalized in REDACTED_OUTPUT_KEYS
        or normalized.endswith("_token")
        or normalized.endswith("_key")
    )


def redact_output_value(value: object) -> object:
    if isinstance(value, Mapping):
        return redact_output_payload(value)
    if isinstance(value, list):
        return [redact_output_value(item) for item in value]
    return value


def redact_output_payload(payload: Mapping[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in payload.items():
        if should_redact_output_key(str(key)):
            redacted[key] = REDACTED_OUTPUT_VALUE
        else:
            redacted[key] = redact_output_value(value)
    return redacted


def print_output_json(payload: Mapping[str, object], *, redact: bool = False) -> None:
    print_json(redact_output_payload(payload) if redact else payload)


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


def format_edge_debug_summary(payload: Mapping[str, object]) -> str:
    fields = [
        "edge",
        f"event={payload.get('event', 'unknown')}",
        f"frame_id={payload.get('frame_id', 'unknown')}",
    ]
    try:
        pipeline_ms = (
            float(payload["sent_at_monotonic"]) - float(payload["detected_at_monotonic"])
        ) * 1000.0
    except (KeyError, TypeError, ValueError):
        pipeline_ms = None
    if pipeline_ms is not None:
        fields.append(f"pipeline_ms={pipeline_ms:.3f}")

    confidence = payload.get("confidence")
    if isinstance(confidence, (int, float)):
        fields.append(f"confidence={float(confidence):.3f}")
    return " ".join(fields)


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
        "gesture": {
            "latency_profile": args.latency_profile,
            "target": args.target_gesture,
            "threshold": args.threshold,
            "hold_seconds": args.hold_seconds,
            "release_grace_seconds": args.release_grace_seconds,
        },
        "udp": destination_payload((args.host, args.port)),
    }


def runtime_metadata(
    state: GestureState,
    *,
    frame_number: int,
    fps: float,
    target_gesture: str = GESTURE_SWORD_SIGN,
    turn_id: str | None = None,
    detected_at_monotonic: float | None = None,
) -> dict[str, object]:
    target = state.gesture(target_gesture)
    metadata: dict[str, object] = {
        "frame_id": frame_number,
        "detected_at": float(state.timestamp),
        "hand_detected": bool(state.hand_detected),
        "primary_gesture": state.primary,
        "target_gesture": target_gesture,
        "confidence": float(target.confidence),
        "fps": round(fps, 3),
    }
    if detected_at_monotonic is not None:
        metadata["detected_at_monotonic"] = float(detected_at_monotonic)
    if turn_id is not None:
        metadata["turn_id"] = turn_id
    return metadata


def state_with_runtime_metadata(
    state: GestureState,
    *,
    frame_number: int,
    fps: float,
    target_gesture: str = GESTURE_SWORD_SIGN,
    turn_id: str | None = None,
    detected_at_monotonic: float | None = None,
) -> GestureState:
    metadata = dict(state.metadata or {})
    metadata.update(
        runtime_metadata(
            state,
            frame_number=frame_number,
            fps=fps,
            target_gesture=target_gesture,
            turn_id=turn_id,
            detected_at_monotonic=detected_at_monotonic,
        )
    )
    return replace(state, metadata=metadata)


def transport_payload_fields(
    state: GestureState,
    *,
    frame_number: int,
    fps: float,
    target_gesture: str = GESTURE_SWORD_SIGN,
    turn_id: str | None = None,
    detected_at_monotonic: float | None = None,
    sent_at: float | None = None,
    sent_at_monotonic: float | None = None,
) -> dict[str, object]:
    fields = runtime_metadata(
        state,
        frame_number=frame_number,
        fps=fps,
        target_gesture=target_gesture,
        turn_id=turn_id,
        detected_at_monotonic=detected_at_monotonic,
    )
    fields["sent_at"] = time.time() if sent_at is None else float(sent_at)
    fields["sent_at_monotonic"] = (
        time.monotonic() if sent_at_monotonic is None else float(sent_at_monotonic)
    )
    return fields


def gesture_state_payload(
    state: GestureState,
    *,
    frame_number: int,
    fps: float,
    target_gesture: str = GESTURE_SWORD_SIGN,
    turn_id: str | None = None,
    detected_at_monotonic: float | None = None,
    sent_at: float | None = None,
    sent_at_monotonic: float | None = None,
) -> dict[str, object]:
    payload = state.to_dict()
    payload.update(
        transport_payload_fields(
            state,
            frame_number=frame_number,
            fps=fps,
            target_gesture=target_gesture,
            turn_id=turn_id,
            detected_at_monotonic=detected_at_monotonic,
            sent_at=sent_at,
            sent_at_monotonic=sent_at_monotonic,
        )
    )
    return payload


def new_turn_id() -> str:
    return f"turn_{uuid.uuid4().hex}"


def edge_event_name(hold: GestureHoldState) -> str | None:
    if hold.activated:
        return EDGE_GESTURE_ACTIVE
    if hold.released:
        return EDGE_GESTURE_RELEASED
    return None


def gesture_edge_payload(
    state: GestureState,
    hold: GestureHoldState,
    *,
    turn_id: str,
    frame_number: int,
    camera_index: int,
    destination: tuple[str, int],
    fps: float,
    detected_at_monotonic: float | None = None,
    sent_at: float | None = None,
    sent_at_monotonic: float | None = None,
) -> dict[str, object]:
    event = edge_event_name(hold)
    if event is None:
        raise ValueError("hold state does not represent an edge event")

    payload = {
        "type": "gesture_edge",
        "event": event,
        "source": SOURCE_NAME,
        "timestamp": float(state.timestamp),
        "status": "active" if hold.active else "released",
        "turn_id": turn_id,
        "camera": {
            "selected_index": camera_index,
            "opened": True,
        },
        "udp": destination_payload(destination),
        "target_gesture": hold.target,
        "current_active": bool(hold.current_active),
        "stable_active": bool(hold.active),
        "held_for": float(hold.held_for),
    }
    payload.update(
        transport_payload_fields(
            state,
            frame_number=frame_number,
            fps=fps,
            target_gesture=hold.target,
            turn_id=turn_id,
            detected_at_monotonic=detected_at_monotonic,
            sent_at=sent_at,
            sent_at_monotonic=sent_at_monotonic,
        )
    )
    return payload


def status_payload(
    state: GestureState,
    *,
    frame_number: int,
    camera_index: int,
    destination: tuple[str, int],
    fps: float,
    latency_profile: str = "default",
    target_gesture: str = GESTURE_SWORD_SIGN,
    threshold: float = DEFAULT_THRESHOLD,
    hold_seconds: float = DEFAULT_HOLD_SECONDS,
    release_grace_seconds: float = DEFAULT_RELEASE_GRACE_SECONDS,
) -> dict[str, object]:
    best_name, best_confidence = best_gesture_snapshot(state)
    sword = state.sword_sign
    target = state.gesture(target_gesture)
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
        "gesture": {
            "latency_profile": latency_profile,
            "target": target_gesture,
            "threshold": threshold,
            "confidence": float(target.confidence),
            "hold_seconds": hold_seconds,
            "release_grace_seconds": release_grace_seconds,
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
    hand_detected: bool = False,
    primary_gesture: str | None = None,
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
        "hand_detected": bool(hand_detected),
        "primary_gesture": primary_gesture,
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
                "required": [
                    "type",
                    "timestamp",
                    "source",
                    "hand_detected",
                    "primary",
                    "gestures",
                    "frame_id",
                    "detected_at",
                    "sent_at",
                    "fps",
                    "confidence",
                ],
                "properties": {
                    "type": {"const": "gesture_state"},
                    "timestamp": {"type": "number"},
                    "source": {"type": "string"},
                    "auth_token": {"type": "string"},
                    "hand_detected": {"type": "boolean"},
                    "primary": {"type": ["string", "null"]},
                    "gestures": {"type": "object"},
                    "frame_id": {"type": "integer"},
                    "detected_at": {"type": "number"},
                    "detected_at_monotonic": {"type": "number"},
                    "sent_at": {"type": "number"},
                    "sent_at_monotonic": {"type": "number"},
                    "fps": {"type": "number"},
                    "confidence": {"type": "number"},
                    "turn_id": {"type": "string"},
                    "metadata": {
                        "type": "object",
                        "properties": {
                            "frame_id": {"type": "integer"},
                            "detected_at": {"type": "number"},
                            "detected_at_monotonic": {"type": "number"},
                            "hand_detected": {"type": "boolean"},
                            "primary_gesture": {"type": ["string", "null"]},
                            "target_gesture": {"type": "string"},
                            "confidence": {"type": "number"},
                            "fps": {"type": "number"},
                            "turn_id": {"type": "string"},
                        },
                    },
                },
            },
            {
                "title": "GestureEdge",
                "type": "object",
                "required": [
                    "type",
                    "event",
                    "turn_id",
                    "frame_id",
                    "detected_at",
                    "sent_at",
                    "fps",
                    "confidence",
                ],
                "properties": {
                    "type": {"const": "gesture_edge"},
                    "event": {"enum": [EDGE_GESTURE_ACTIVE, EDGE_GESTURE_RELEASED]},
                    "auth_token": {"type": "string"},
                    "turn_id": {"type": "string"},
                    "target_gesture": {"type": "string"},
                    "current_active": {"type": "boolean"},
                    "stable_active": {"type": "boolean"},
                    "held_for": {"type": "number"},
                    "frame_id": {"type": "integer"},
                    "detected_at": {"type": "number"},
                    "detected_at_monotonic": {"type": "number"},
                    "sent_at": {"type": "number"},
                    "sent_at_monotonic": {"type": "number"},
                    "fps": {"type": "number"},
                    "confidence": {"type": "number"},
                },
            },
            {
                "title": "GestureStatus",
                "type": "object",
                "required": [
                    "type",
                    "status",
                    "camera",
                    "udp",
                    "frame_id",
                    "hand_detected",
                    "primary_gesture",
                    "fps",
                ],
                "properties": {
                    "type": {"const": "gesture_status"},
                    "auth_token": {"type": "string"},
                    "status": {"const": "running"},
                    "camera": {"type": "object"},
                    "udp": {"type": "object"},
                    "frame_id": {"type": "integer"},
                    "hand_detected": {"type": "boolean"},
                    "primary_gesture": {"type": ["string", "null"]},
                    "fps": {"type": "number"},
                },
            },
            {
                "title": "GestureHeartbeat",
                "type": "object",
                "required": [
                    "type",
                    "status",
                    "camera",
                    "udp",
                    "frame_id",
                    "hand_detected",
                    "primary_gesture",
                    "fps",
                ],
                "properties": {
                    "type": {"const": "gesture_heartbeat"},
                    "auth_token": {"type": "string"},
                    "status": {"const": "sending"},
                    "camera": {"type": "object"},
                    "udp": {"type": "object"},
                    "frame_id": {"type": "integer"},
                    "hand_detected": {"type": "boolean"},
                    "primary_gesture": {"type": ["string", "null"]},
                    "fps": {"type": "number"},
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
    parser.add_argument(
        "--latency-profile",
        choices=sorted(LATENCY_PROFILES),
        default="default",
        help="preset for omitted threshold/hold/grace values; explicit arguments override it",
    )
    parser.add_argument("--threshold", type=parse_threshold, default=None)
    parser.add_argument(
        "--hold-seconds",
        "--hold",
        dest="hold_seconds",
        type=parse_interval,
        default=None,
        help="seconds a current-frame gesture must remain active before stable active",
    )
    parser.add_argument(
        "--release-grace-seconds",
        "--grace",
        dest="release_grace_seconds",
        type=parse_interval,
        default=None,
        help="seconds to keep stable active through short current-frame gaps",
    )
    parser.add_argument(
        "--target-gesture",
        choices=[GESTURE_SWORD_SIGN, GESTURE_VICTORY],
        default=GESTURE_SWORD_SIGN,
        help="gesture tracked for stable active/released edge events",
    )
    parser.add_argument("--model-path")
    parser.add_argument("--model-sha256")
    parser.add_argument("--allow-untrusted-model", action="store_true")
    parser.add_argument("--interval", type=parse_interval, default=0.0)
    parser.add_argument(
        "--allow-remote-udp",
        action="store_true",
        help="allow sending gesture state UDP packets to a non-local host",
    )
    parser.add_argument(
        "--auth-token",
        help="attach this token as auth_token in UDP payloads; prefer --auth-token-env for shared use",
    )
    parser.add_argument(
        "--auth-token-env",
        default=UDP_AUTH_TOKEN_ENV,
        help="environment variable that supplies the UDP auth_token payload field",
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
        "--redact-output",
        action="store_true",
        help="redact tokens, turn IDs, and absolute timestamps from stdout/debug output; UDP payloads are unchanged",
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
        "--state-every",
        type=parse_optional_interval,
        default=DebugEvery(1, "frames"),
        help="send gesture_state interval; use 0/off/none for edge-only UDP",
    )
    parser.add_argument(
        "--edge-only",
        action="store_true",
        help="shortcut for --state-every off; still sends gesture_edge and explicit heartbeat/status output",
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
    args = apply_publish_options(apply_latency_profile(parser.parse_args()))
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
        udp_auth_token = resolve_udp_auth_token(
            auth_token=args.auth_token,
            auth_token_env=args.auth_token_env,
        )
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
    hold_tracker = GestureHoldTracker(
        target=args.target_gesture,
        hold_seconds=args.hold_seconds,
        release_grace_seconds=args.release_grace_seconds,
    )
    active_turn_id: str | None = None
    last_state_at: float | None = None
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
            UdpGesturePublisher(args.host, args.port, auth_token=udp_auth_token) as publisher,
        ):
            eprint(
                "selected_camera="
                f"{args.camera_index} udp={args.host}:{args.port} version={get_version()} "
                f"latency_profile={args.latency_profile} threshold={args.threshold:g} "
                f"hold={args.hold_seconds:g}s grace={args.release_grace_seconds:g}s "
                f"target={args.target_gesture} state_every={args.state_every or 'off'}"
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
                frame_read_at = time.monotonic()
                fps = fps_tracker.update(frame_read_at)
                detected_state = detector.detect(frame, flip=True)
                detected_at_monotonic = time.monotonic()
                hold = hold_tracker.update(detected_state, now=detected_at_monotonic)
                if hold.activated:
                    active_turn_id = new_turn_id()

                turn_id = active_turn_id if (hold.active or hold.released) else None
                state = state_with_runtime_metadata(
                    detected_state,
                    frame_number=frame_number,
                    fps=fps,
                    target_gesture=args.target_gesture,
                    turn_id=turn_id,
                    detected_at_monotonic=detected_at_monotonic,
                )
                state_payload = gesture_state_payload(
                    state,
                    frame_number=frame_number,
                    fps=fps,
                    target_gesture=args.target_gesture,
                    turn_id=turn_id,
                    detected_at_monotonic=detected_at_monotonic,
                )
                if args.state_every is not None and should_print_debug(
                    args.state_every,
                    frame_number=frame_number,
                    last_debug_at=last_state_at,
                    now=detected_at_monotonic,
                ):
                    publisher.publish_payload(state_payload)
                    if args.print_json:
                        print_output_json(state_payload, redact=args.redact_output)
                    last_state_at = detected_at_monotonic

                if hold.activated or hold.released:
                    assert turn_id is not None
                    edge_payload = gesture_edge_payload(
                        state,
                        hold,
                        turn_id=turn_id,
                        frame_number=frame_number,
                        camera_index=args.camera_index,
                        destination=publisher.address,
                        fps=fps,
                        detected_at_monotonic=detected_at_monotonic,
                    )
                    publisher.publish_payload(edge_payload)
                    if args.print_json:
                        print_output_json(edge_payload, redact=args.redact_output)
                    if args.debug:
                        eprint(format_edge_debug_summary(edge_payload))
                    if hold.released:
                        active_turn_id = None

                if args.debug and should_print_debug(
                    args.debug_every,
                    frame_number=frame_number,
                    last_debug_at=last_debug_at,
                    now=detected_at_monotonic,
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
                    last_debug_at = detected_at_monotonic

                if args.status_json and should_print_debug(
                    args.status_every,
                    frame_number=frame_number,
                    last_debug_at=last_status_at,
                    now=detected_at_monotonic,
                ):
                    print_output_json(
                        status_payload(
                            state,
                            frame_number=frame_number,
                            camera_index=args.camera_index,
                            destination=publisher.address,
                            fps=fps,
                            latency_profile=args.latency_profile,
                            target_gesture=args.target_gesture,
                            threshold=args.threshold,
                            hold_seconds=args.hold_seconds,
                            release_grace_seconds=args.release_grace_seconds,
                        ),
                        redact=args.redact_output,
                    )
                    last_status_at = detected_at_monotonic

                if args.heartbeat_every is not None and should_print_debug(
                    args.heartbeat_every,
                    frame_number=frame_number,
                    last_debug_at=last_heartbeat_at,
                    now=detected_at_monotonic,
                ):
                    publisher.publish_payload(
                        heartbeat_payload(
                            frame_number=frame_number,
                            camera_index=args.camera_index,
                            destination=publisher.address,
                            fps=fps,
                            hand_detected=state.hand_detected,
                            primary_gesture=state.primary,
                        )
                    )
                    last_heartbeat_at = detected_at_monotonic

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
