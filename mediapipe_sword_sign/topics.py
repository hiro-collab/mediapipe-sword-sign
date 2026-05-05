from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Mapping


TOPIC_ENVELOPE_SCHEMA_VERSION = 1
MAX_SEQUENCE = 2**63 - 1

CAMERA_STATUS_TOPIC = "/camera/status"
CAMERA_IMAGE_COMPRESSED_TOPIC = "/camera/color/image_raw/compressed"
SWORD_SIGN_STATE_TOPIC = "/vision/sword_sign/state"
ROOM_LIGHT_STATE_TOPIC = "/vision/room_light/state"

MSG_TYPE_CAMERA_STATUS = "sword_voice_agent/CameraStatus"
MSG_TYPE_COMPRESSED_IMAGE = "sensor_msgs/CompressedImage"
MSG_TYPE_GESTURE_STATE = "mediapipe_sword_sign/GestureState"
MSG_TYPE_ROOM_LIGHT_STATE = "mediapipe_sword_sign/RoomLightState"


@dataclass(frozen=True)
class TopicHeader:
    sequence: int
    stamp: float
    frame_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "seq": _sequence(self.sequence),
            "stamp": _finite_float(self.stamp, name="header stamp"),
            "frame_id": _non_empty_text(self.frame_id, name="frame_id"),
        }


@dataclass(frozen=True)
class TopicEnvelope:
    topic: str
    msg_type: str
    payload: Mapping[str, object]
    header: TopicHeader
    schema_version: int = TOPIC_ENVELOPE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _positive_int(
                self.schema_version,
                name="schema_version",
            ),
            "topic": _topic_name(self.topic),
            "msg_type": _non_empty_text(self.msg_type, name="msg_type"),
            "header": self.header.to_dict(),
            "payload": dict(self.payload),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )


def topic_payload(
    topic: str,
    msg_type: str,
    payload: Mapping[str, object],
    *,
    sequence: int,
    stamp: float | None = None,
    frame_id: str = "camera",
) -> dict[str, object]:
    return TopicEnvelope(
        topic=topic,
        msg_type=msg_type,
        payload=payload,
        header=TopicHeader(
            sequence=sequence,
            stamp=time.time() if stamp is None else stamp,
            frame_id=frame_id,
        ),
    ).to_dict()


def topic_json(
    topic: str,
    msg_type: str,
    payload: Mapping[str, object],
    *,
    sequence: int,
    stamp: float | None = None,
    frame_id: str = "camera",
) -> str:
    return json.dumps(
        topic_payload(
            topic,
            msg_type,
            payload,
            sequence=sequence,
            stamp=stamp,
            frame_id=frame_id,
        ),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _sequence(value: int) -> int:
    parsed = _non_negative_int(value, name="sequence")
    if parsed > MAX_SEQUENCE:
        raise ValueError(f"sequence must be between 0 and {MAX_SEQUENCE}")
    return parsed


def _topic_name(value: str) -> str:
    text = _non_empty_text(value, name="topic")
    if not text.startswith("/"):
        raise ValueError("topic must start with /")
    if "//" in text:
        raise ValueError("topic must not contain empty path segments")
    return text


def _non_empty_text(value: str, *, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError(f"{name} must not contain control characters")
    return text


def _finite_float(value: float, *, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def _non_negative_int(value: int, *, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be 0 or greater")
    return parsed


def _positive_int(value: int, *, name: str) -> int:
    parsed = _non_negative_int(value, name=name)
    if parsed == 0:
        raise ValueError(f"{name} must be greater than 0")
    return parsed
