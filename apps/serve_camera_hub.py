from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
import time
from math import isfinite
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mediapipe_sword_sign import (  # noqa: E402
    GESTURE_SWORD_SIGN,
    GestureHoldTracker,
    RoomLightDetector,
    SwordSignDetector,
    UnsafeModelError,
    gesture_state_payload,
)
from mediapipe_sword_sign.adapters import WebSocketTopicBroadcaster  # noqa: E402
from mediapipe_sword_sign.topics import (  # noqa: E402
    CAMERA_IMAGE_COMPRESSED_TOPIC,
    CAMERA_STATUS_TOPIC,
    MSG_TYPE_CAMERA_STATUS,
    MSG_TYPE_COMPRESSED_IMAGE,
    MSG_TYPE_GESTURE_STATE,
    MSG_TYPE_ROOM_LIGHT_STATE,
    ROOM_LIGHT_STATE_TOPIC,
    SWORD_SIGN_STATE_TOPIC,
    topic_json,
)


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


def parse_window_size(value: str) -> int:
    parsed = parse_positive_int(value, name="--room-light-window-size")
    if parsed < 2:
        raise argparse.ArgumentTypeError("--room-light-window-size must be 2 or greater")
    return parsed


def parse_threshold(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("threshold must be a number") from exc
    if not isfinite(parsed) or not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("threshold must be between 0 and 1")
    return parsed


def parse_interval(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("interval must be a number") from exc
    if not isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("interval must be 0 or greater")
    return parsed


def parse_positive_interval(value: str) -> float:
    parsed = parse_interval(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("interval must be greater than 0")
    return parsed


def parse_jpeg_quality(value: str) -> int:
    parsed = parse_positive_int(value, name="--jpeg-quality")
    if parsed > 100:
        raise argparse.ArgumentTypeError("--jpeg-quality must be between 1 and 100")
    return parsed


def resolve_auth_token(auth_token_env: str | None) -> str | None:
    if not auth_token_env:
        return None
    env_name = auth_token_env.strip()
    if not env_name:
        return None
    token = os.environ.get(env_name)
    if token is None:
        return None
    return token.strip() or None


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


def camera_status_payload(
    *,
    camera_index: int,
    frame_number: int,
    fps: float,
    room_light_model_loaded: bool,
) -> dict[str, object]:
    return {
        "type": "camera_status",
        "timestamp": time.time(),
        "camera": {
            "selected_index": camera_index,
            "opened": True,
        },
        "frame_id": frame_number,
        "fps": round(fps, 3),
        "processors": {
            "sword_sign": {"enabled": True},
            "room_light": {
                "enabled": True,
                "model_loaded": bool(room_light_model_loaded),
            },
        },
    }


def compressed_image_payload(frame_bgr, *, quality: int) -> dict[str, object]:
    success, encoded = cv2.imencode(
        ".jpg",
        frame_bgr,
        [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)],
    )
    if not success:
        raise RuntimeError("failed to encode camera frame")
    return {
        "type": "compressed_image",
        "format": "jpeg",
        "encoding": "bgr8",
        "data_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
    }


class FpsTracker:
    def __init__(self) -> None:
        self.last_frame_at: float | None = None
        self.fps = 0.0

    def update(self, now: float) -> float:
        if self.last_frame_at is not None:
            elapsed = now - self.last_frame_at
            if elapsed > 0:
                self.fps = 1.0 / elapsed
        self.last_frame_at = now
        return self.fps


def due(last_published_at: float | None, interval: float, now: float) -> bool:
    return interval > 0 and (
        last_published_at is None or now - last_published_at >= interval
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a ROS-like local camera hub and publish vision topics over WebSocket.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=parse_port, default=8765)
    parser.add_argument("--camera-index", type=parse_camera_index, default=0)
    parser.add_argument("--frame-id", default="logitech_camera")
    parser.add_argument("--interval", type=parse_positive_interval, default=1 / 30)
    parser.add_argument("--status-every", type=parse_interval, default=2.0)
    parser.add_argument("--publish-jpeg-every", type=parse_interval, default=0.0)
    parser.add_argument("--jpeg-quality", type=parse_jpeg_quality, default=70)
    parser.add_argument("--gesture-threshold", type=parse_threshold, default=0.9)
    parser.add_argument("--gesture-model-path")
    parser.add_argument("--gesture-model-sha256")
    parser.add_argument("--allow-untrusted-gesture-model", action="store_true")
    parser.add_argument("--hold-seconds", type=parse_interval, default=0.5)
    parser.add_argument("--release-grace-seconds", type=parse_interval, default=0.1)
    parser.add_argument("--room-light-model-path")
    parser.add_argument("--room-light-model-sha256")
    parser.add_argument("--allow-untrusted-room-light-model", action="store_true")
    parser.add_argument("--room-light-window-size", type=parse_window_size, default=2)
    parser.add_argument("--room-light-threshold", type=parse_threshold, default=0.6)
    parser.add_argument("--room-light-publish-every", type=parse_interval, default=0.5)
    parser.add_argument("--auth-token-env", default="CAMERA_HUB_WS_TOKEN")
    parser.add_argument("--allowed-origin", action="append", dest="allowed_origins")
    parser.add_argument("--max-clients", type=lambda value: parse_positive_int(value, name="--max-clients"), default=8)
    parser.add_argument("--max-message-bytes", type=lambda value: parse_positive_int(value, name="--max-message-bytes"), default=4096)
    parser.add_argument("--max-queue", type=lambda value: parse_positive_int(value, name="--max-queue"), default=4)
    parser.add_argument("--allow-remote-unauthenticated", action="store_true")
    return parser


async def run(args: argparse.Namespace) -> None:
    cap = cv2.VideoCapture(args.camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"camera not available: {args.camera_index}")

    auth_token = resolve_auth_token(args.auth_token_env)
    broadcaster = WebSocketTopicBroadcaster(
        args.host,
        args.port,
        auth_token=auth_token,
        allowed_origins=args.allowed_origins,
        max_clients=args.max_clients,
        max_message_bytes=args.max_message_bytes,
        max_queue=args.max_queue,
        allow_remote_unauthenticated=args.allow_remote_unauthenticated,
    )
    fps_tracker = FpsTracker()
    room_light = RoomLightDetector(
        model_path=args.room_light_model_path,
        expected_model_sha256=args.room_light_model_sha256,
        allow_untrusted_model=args.allow_untrusted_room_light_model,
        window_size=args.room_light_window_size,
        threshold=args.room_light_threshold,
    )
    last_room_light_at: float | None = None
    last_status_at: float | None = None
    last_jpeg_at: float | None = None
    frame_number = 0

    try:
        async with broadcaster:
            print(f"Camera hub listening on ws://{args.host}:{args.port}")
            print(
                "topics: "
                f"{SWORD_SIGN_STATE_TOPIC}, {ROOM_LIGHT_STATE_TOPIC}, {CAMERA_STATUS_TOPIC}"
            )
            with SwordSignDetector(
                model_path=args.gesture_model_path,
                expected_model_sha256=args.gesture_model_sha256,
                allow_untrusted_model=args.allow_untrusted_gesture_model,
                threshold=args.gesture_threshold,
            ) as sword_detector:
                hold_tracker = GestureHoldTracker(
                    target=GESTURE_SWORD_SIGN,
                    hold_seconds=args.hold_seconds,
                    release_grace_seconds=args.release_grace_seconds,
                )
                while True:
                    success, frame = cap.read()
                    if not success:
                        await asyncio.sleep(0.05)
                        continue

                    frame_number += 1
                    stamp = time.time()
                    now = time.monotonic()
                    fps = fps_tracker.update(now)

                    gesture_state = sword_detector.detect(frame, flip=True, timestamp=stamp)
                    stable = hold_tracker.update(gesture_state)
                    gesture_payload = gesture_state_payload(
                        gesture_state,
                        sequence=frame_number,
                        stable=stable,
                    )
                    await broadcaster.publish_message(
                        topic_json(
                            SWORD_SIGN_STATE_TOPIC,
                            MSG_TYPE_GESTURE_STATE,
                            gesture_payload,
                            sequence=frame_number,
                            stamp=stamp,
                            frame_id=args.frame_id,
                        )
                    )

                    light_state = room_light.observe(
                        frame,
                        frame_id=frame_number,
                        timestamp=stamp,
                    )
                    if due(last_room_light_at, args.room_light_publish_every, now):
                        await broadcaster.publish_message(
                            topic_json(
                                ROOM_LIGHT_STATE_TOPIC,
                                MSG_TYPE_ROOM_LIGHT_STATE,
                                light_state.to_dict(),
                                sequence=frame_number,
                                stamp=stamp,
                                frame_id=args.frame_id,
                            )
                        )
                        last_room_light_at = now

                    if due(last_status_at, args.status_every, now):
                        await broadcaster.publish_message(
                            topic_json(
                                CAMERA_STATUS_TOPIC,
                                MSG_TYPE_CAMERA_STATUS,
                                camera_status_payload(
                                    camera_index=args.camera_index,
                                    frame_number=frame_number,
                                    fps=fps,
                                    room_light_model_loaded=room_light.model_loaded,
                                ),
                                sequence=frame_number,
                                stamp=stamp,
                                frame_id=args.frame_id,
                            )
                        )
                        last_status_at = now

                    if due(last_jpeg_at, args.publish_jpeg_every, now):
                        await broadcaster.publish_message(
                            topic_json(
                                CAMERA_IMAGE_COMPRESSED_TOPIC,
                                MSG_TYPE_COMPRESSED_IMAGE,
                                compressed_image_payload(frame, quality=args.jpeg_quality),
                                sequence=frame_number,
                                stamp=stamp,
                                frame_id=args.frame_id,
                            )
                        )
                        last_jpeg_at = now

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
