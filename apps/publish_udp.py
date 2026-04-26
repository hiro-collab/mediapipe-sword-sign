from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mediapipe_sword_sign import SwordSignDetector
from mediapipe_sword_sign.adapters import UdpGesturePublisher


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish GestureState JSON over UDP.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--threshold", type=float, default=0.9)
    parser.add_argument("--model-path")
    parser.add_argument("--interval", type=float, default=0.0)
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    cap = cv2.VideoCapture(args.camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"camera not available: {args.camera_index}")

    try:
        with (
            SwordSignDetector(model_path=args.model_path, threshold=args.threshold) as detector,
            UdpGesturePublisher(args.host, args.port) as publisher,
        ):
            while True:
                success, frame = cap.read()
                if not success:
                    time.sleep(0.05)
                    continue

                state = detector.detect(frame, flip=True)
                publisher.publish(state)
                if args.print_json:
                    print(state.to_json(), flush=True)

                if args.interval > 0:
                    time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()


if __name__ == "__main__":
    main()
