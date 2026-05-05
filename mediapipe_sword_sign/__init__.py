"""Reusable gesture recognition module for sword-sign detection."""

from .detector import DetectionResult, SwordSignDetector
from .features import FEATURE_DIMENSION, features_from_hand_landmarks, relative_landmark_features
from .model_loader import UnsafeModelError, file_sha256
from .payloads import GESTURE_STATE_SCHEMA_VERSION, gesture_state_json, gesture_state_payload
from .ports import AsyncGesturePublisher, GesturePublisher
from .room_light import (
    DEFAULT_ROOM_LIGHT_LABELS,
    ROOM_LIGHT_SCHEMA_VERSION,
    RoomLightDetector,
    RoomLightFeatureExtractor,
    RoomLightState,
)
from .temporal import GestureHoldState, GestureHoldTracker
from .topics import (
    ROOM_LIGHT_STATE_TOPIC,
    SWORD_SIGN_STATE_TOPIC,
    TOPIC_ENVELOPE_SCHEMA_VERSION,
    TopicEnvelope,
    TopicHeader,
    topic_json,
    topic_payload,
)
from .types import (
    GESTURE_NONE,
    GESTURE_SWORD_SIGN,
    GESTURE_VICTORY,
    GesturePrediction,
    GestureState,
)

__all__ = [
    "DetectionResult",
    "FEATURE_DIMENSION",
    "GESTURE_NONE",
    "GESTURE_SWORD_SIGN",
    "GESTURE_VICTORY",
    "DEFAULT_ROOM_LIGHT_LABELS",
    "GesturePrediction",
    "GesturePublisher",
    "GestureState",
    "GestureHoldState",
    "GestureHoldTracker",
    "GESTURE_STATE_SCHEMA_VERSION",
    "ROOM_LIGHT_SCHEMA_VERSION",
    "ROOM_LIGHT_STATE_TOPIC",
    "SWORD_SIGN_STATE_TOPIC",
    "SwordSignDetector",
    "RoomLightDetector",
    "RoomLightFeatureExtractor",
    "RoomLightState",
    "TOPIC_ENVELOPE_SCHEMA_VERSION",
    "TopicEnvelope",
    "TopicHeader",
    "UnsafeModelError",
    "AsyncGesturePublisher",
    "file_sha256",
    "features_from_hand_landmarks",
    "gesture_state_json",
    "gesture_state_payload",
    "relative_landmark_features",
    "topic_json",
    "topic_payload",
]
