from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mediapipe_sword_sign import SwordSignDetector
from mediapipe_sword_sign.adapters import WebSocketGestureBroadcaster


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Broadcast GestureState JSON over WebSocket.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--threshold", type=float, default=0.9)
    parser.add_argument("--model-path")
    parser.add_argument("--model-sha256")
    parser.add_argument("--allow-untrusted-model", action="store_true")
    parser.add_argument("--interval", type=float, default=1 / 30)
    parser.add_argument("--auth-token-env", default="GESTURE_WS_TOKEN")
    parser.add_argument("--allowed-origin", action="append", dest="allowed_origins")
    parser.add_argument("--max-clients", type=int, default=8)
    parser.add_argument("--allow-remote-unauthenticated", action="store_true")
    return parser


async def run(args: argparse.Namespace) -> None:
    cap = cv2.VideoCapture(args.camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"camera not available: {args.camera_index}")

    auth_token = os.environ.get(args.auth_token_env) if args.auth_token_env else None
    broadcaster = WebSocketGestureBroadcaster(
        args.host,
        args.port,
        auth_token=auth_token,
        allowed_origins=args.allowed_origins,
        max_clients=args.max_clients,
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
                while True:
                    success, frame = cap.read()
                    if success:
                        await broadcaster.publish(detector.detect(frame, flip=True))
                    await asyncio.sleep(args.interval)
    finally:
        cap.release()


def main() -> None:
    try:
        asyncio.run(run(build_parser().parse_args()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
