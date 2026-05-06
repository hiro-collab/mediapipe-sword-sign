"""Reusable gesture recognition module for sword-sign detection."""

from .detector import DetectionResult, SwordSignDetector
from .features import FEATURE_DIMENSION, features_from_hand_landmarks, relative_landmark_features
from .model_loader import UnsafeModelError, file_sha256
from .payloads import GESTURE_STATE_SCHEMA_VERSION, gesture_state_json, gesture_state_payload
from .ports import AsyncGesturePublisher, GesturePublisher
from .temporal import GestureHoldState, GestureHoldTracker
from .topics import (
    SWORD_SIGN_STATE_TOPIC,
    TOPIC_ENVELOPE_SCHEMA_VERSION,
    TopicEnvelope,
    TopicHeader,
    binary_topic_message,
    parse_binary_topic_message,
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
    "GesturePrediction",
    "GesturePublisher",
    "GestureState",
    "GestureHoldState",
    "GestureHoldTracker",
    "GESTURE_STATE_SCHEMA_VERSION",
    "SWORD_SIGN_STATE_TOPIC",
    "SwordSignDetector",
    "TOPIC_ENVELOPE_SCHEMA_VERSION",
    "TopicEnvelope",
    "TopicHeader",
    "binary_topic_message",
    "UnsafeModelError",
    "AsyncGesturePublisher",
    "file_sha256",
    "features_from_hand_landmarks",
    "gesture_state_json",
    "gesture_state_payload",
    "parse_binary_topic_message",
    "relative_landmark_features",
    "topic_json",
    "topic_payload",
]
