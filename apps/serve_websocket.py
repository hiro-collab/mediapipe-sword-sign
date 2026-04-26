from __future__ import annotations

import argparse
import asyncio

import cv2

from mediapipe_sword_sign import SwordSignDetector
from mediapipe_sword_sign.adapters import WebSocketGestureBroadcaster


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Broadcast GestureState JSON over WebSocket.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--threshold", type=float, default=0.9)
    parser.add_argument("--model-path")
    parser.add_argument("--interval", type=float, default=1 / 30)
    return parser


async def run(args: argparse.Namespace) -> None:
    cap = cv2.VideoCapture(args.camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"camera not available: {args.camera_index}")

    broadcaster = WebSocketGestureBroadcaster(args.host, args.port)
    try:
        async with broadcaster:
            print(f"WebSocket gesture broadcaster listening on ws://{args.host}:{args.port}")
            with SwordSignDetector(model_path=args.model_path, threshold=args.threshold) as detector:
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
