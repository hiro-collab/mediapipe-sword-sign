"""Reusable gesture recognition module for sword-sign detection."""

from .detector import DetectionResult, SwordSignDetector
from .features import FEATURE_DIMENSION, features_from_hand_landmarks, relative_landmark_features
from .ports import AsyncGesturePublisher, GesturePublisher
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
    "SwordSignDetector",
    "AsyncGesturePublisher",
    "features_from_hand_landmarks",
    "relative_landmark_features",
]
