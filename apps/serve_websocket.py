from __future__ import annotations

import argparse
import asyncio
import os
import sys
from math import isfinite
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mediapipe_sword_sign import GESTURE_SWORD_SIGN, GestureHoldTracker, SwordSignDetector, UnsafeModelError
from mediapipe_sword_sign.adapters import WebSocketGestureBroadcaster


def parse_port(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--port must be an integer") from exc
    if not 1 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("--port must be between 1 and 65535")
    return parsed


def parse_non_negative_int(value: str, *, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"{name} must be 0 or greater")
    return parsed


def parse_positive_int(value: str, *, name: str) -> int:
    parsed = parse_non_negative_int(value, name=name)
    if parsed == 0:
        raise argparse.ArgumentTypeError(f"{name} must be greater than 0")
    return parsed


def parse_camera_index(value: str) -> int:
    return parse_non_negative_int(value, name="--camera-index")


def parse_threshold(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--threshold must be a number") from exc
    if not isfinite(parsed) or not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("--threshold must be between 0 and 1")
    return parsed


def parse_interval(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--interval must be a number") from exc
    if not isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("--interval must be greater than 0")
    return parsed


def parse_non_negative_float(value: str, *, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be a number") from exc
    if not isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError(f"{name} must be 0 or greater")
    return parsed


def parse_max_clients(value: str) -> int:
    return parse_positive_int(value, name="--max-clients")


def parse_max_message_bytes(value: str) -> int:
    return parse_positive_int(value, name="--max-message-bytes")


def parse_max_queue(value: str) -> int:
    return parse_positive_int(value, name="--max-queue")


def parse_hold_seconds(value: str) -> float:
    return parse_non_negative_float(value, name="--hold-seconds")


def parse_release_grace_seconds(value: str) -> float:
    return parse_non_negative_float(value, name="--release-grace-seconds")


def resolve_ws_auth_token(auth_token_env: str | None) -> str | None:
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


def safe_runtime_error(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return "model_not_found"
    if isinstance(exc, UnsafeModelError):
        return "unsafe_model"
    if isinstance(exc, RuntimeError) and "camera not available" in str(exc):
        return str(exc)
    if isinstance(exc, ValueError):
        return str(exc)
    return "runtime_failed"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Broadcast GestureState JSON over WebSocket.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=parse_port, default=8765)
    parser.add_argument("--camera-index", type=parse_camera_index, default=0)
    parser.add_argument("--threshold", type=parse_threshold, default=0.9)
    parser.add_argument("--model-path")
    parser.add_argument("--model-sha256")
    parser.add_argument("--allow-untrusted-model", action="store_true")
    parser.add_argument("--interval", type=parse_interval, default=1 / 30)
    parser.add_argument("--hold-seconds", type=parse_hold_seconds, default=0.5)
    parser.add_argument("--release-grace-seconds", type=parse_release_grace_seconds, default=0.1)
    parser.add_argument("--auth-token-env", default="GESTURE_WS_TOKEN")
    parser.add_argument("--allowed-origin", action="append", dest="allowed_origins")
    parser.add_argument("--max-clients", type=parse_max_clients, default=8)
    parser.add_argument("--max-message-bytes", type=parse_max_message_bytes, default=4096)
    parser.add_argument("--max-queue", type=parse_max_queue, default=4)
    parser.add_argument("--allow-remote-unauthenticated", action="store_true")
    return parser


async def run(args: argparse.Namespace) -> None:
    cap = cv2.VideoCapture(args.camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"camera not available: {args.camera_index}")

    auth_token = resolve_ws_auth_token(args.auth_token_env)
    broadcaster = WebSocketGestureBroadcaster(
        args.host,
        args.port,
        auth_token=auth_token,
        allowed_origins=args.allowed_origins,
        max_clients=args.max_clients,
        max_message_bytes=args.max_message_bytes,
        max_queue=args.max_queue,
        allow_remote_unauthenticated=args.allow_remote_unauthenticated,
    )
    try:
        async with broadcaster:
            print(f"WebSocket gesture broadcaster listening on ws://{args.host}:{args.port}")
            with SwordSignDetector(
                model_path=args.model_path,
                expected_model_sha256=args.model_sha256,
                allow_untrusted_model=args.allow_untrusted_model,
                threshold=args.threshold,
            ) as detector:
                hold_tracker = GestureHoldTracker(
                    target=GESTURE_SWORD_SIGN,
                    hold_seconds=args.hold_seconds,
                    release_grace_seconds=args.release_grace_seconds,
                )
                sequence = 0
                while True:
                    success, frame = cap.read()
                    if success:
                        state = detector.detect(frame, flip=True)
                        stable = hold_tracker.update(state)
                        sequence += 1
                        await broadcaster.publish(
                            state,
                            sequence=sequence,
                            stable=stable,
                        )
                    await asyncio.sleep(args.interval)
    finally:
        cap.release()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        parser.exit(1, f"error: {safe_runtime_error(exc)}\n")


if __name__ == "__main__":
    main()
