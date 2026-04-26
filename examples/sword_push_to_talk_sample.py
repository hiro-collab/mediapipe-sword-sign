from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

import cv2

from mediapipe_sword_sign import GestureState, SwordSignDetector


class DifyClientLike(Protocol):
    async def send_message(self, text: str, context: dict[str, Any]) -> str:
        """Send transcript text to a Dify-like backend."""


@dataclass(frozen=True)
class GateUpdate:
    mic_enabled: bool
    changed: bool
    ended_recording: bool
    reason: str


class SwordSignInputGate:
    def __init__(self) -> None:
        self.mic_enabled = False

    def update(self, state: GestureState) -> GateUpdate:
        previous = self.mic_enabled
        self.mic_enabled = state.sword_sign.active
        return GateUpdate(
            mic_enabled=self.mic_enabled,
            changed=previous != self.mic_enabled,
            ended_recording=previous and not self.mic_enabled,
            reason="sword_sign" if self.mic_enabled else "gesture_released",
        )


class SampleVoiceSession:
    def set_input_enabled(self, enabled: bool) -> None:
        print(f"voice mic_enabled={enabled}")

    def consume_transcript(self) -> str:
        return "これは音声認識結果のサンプルです"


class SampleDifyClient:
    async def send_message(self, text: str, context: dict[str, Any]) -> str:
        print(f"dify request text={text!r} context_primary={context['gesture_state']['primary']!r}")
        return "これはDify応答のサンプルです"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reference sample: sword_sign.active controls mic_enabled."
    )
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--threshold", type=float, default=0.9)
    parser.add_argument("--model-path")
    parser.add_argument("--interval", type=float, default=1 / 30)
    return parser


async def run(args: argparse.Namespace) -> None:
    cap = cv2.VideoCapture(args.camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"camera not available: {args.camera_index}")

    gate = SwordSignInputGate()
    voice = SampleVoiceSession()
    dify = SampleDifyClient()

    try:
        with SwordSignDetector(model_path=args.model_path, threshold=args.threshold) as detector:
            while True:
                success, frame = cap.read()
                if not success:
                    await asyncio.sleep(0.05)
                    continue

                state = detector.detect(frame, flip=True)
                update = gate.update(state)
                if update.changed:
                    voice.set_input_enabled(update.mic_enabled)

                if update.ended_recording:
                    transcript = voice.consume_transcript()
                    response = await dify.send_message(
                        transcript,
                        context={"gesture_state": state.to_dict()},
                    )
                    print(f"dify response={response!r}")

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
