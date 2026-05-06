from __future__ import annotations

import math
from typing import Iterable, Protocol


LANDMARK_COUNT = 21
COORDS_PER_LANDMARK = 3
FEATURE_DIMENSION = LANDMARK_COUNT * COORDS_PER_LANDMARK


class LandmarkLike(Protocol):
    x: float
    y: float
    z: float


class HandLandmarksLike(Protocol):
    landmark: Iterable[LandmarkLike]


def relative_landmark_features(landmarks: Iterable[LandmarkLike]) -> list[float]:
    """Convert MediaPipe hand landmarks to wrist-relative xyz features."""
    points = list(landmarks)
    if len(points) != LANDMARK_COUNT:
        raise ValueError(f"expected {LANDMARK_COUNT} landmarks, got {len(points)}")

    base = points[0]
    base_x = _finite_float(base.x)
    base_y = _finite_float(base.y)
    base_z = _finite_float(base.z)

    features: list[float] = []
    for point in points:
        point_x = _finite_float(point.x)
        point_y = _finite_float(point.y)
        point_z = _finite_float(point.z)
        features.extend(
            [
                point_x - base_x,
                point_y - base_y,
                point_z - base_z,
            ]
        )
    return features


def features_from_hand_landmarks(hand_landmarks: HandLandmarksLike) -> list[float]:
    return relative_landmark_features(hand_landmarks.landmark)


def validate_feature_vector(features: Iterable[float]) -> list[float]:
    vector = [_finite_float(value) for value in features]
    if len(vector) != FEATURE_DIMENSION:
        raise ValueError(f"expected {FEATURE_DIMENSION} features, got {len(vector)}")
    return vector


def _finite_float(value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("feature values must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError("feature values must be finite numbers")
    return parsed
