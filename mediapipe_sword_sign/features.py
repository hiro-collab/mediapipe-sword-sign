from __future__ import annotations

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
    base_x = float(base.x)
    base_y = float(base.y)
    base_z = float(base.z)

    features: list[float] = []
    for point in points:
        features.extend(
            [
                float(point.x) - base_x,
                float(point.y) - base_y,
                float(point.z) - base_z,
            ]
        )
    return features


def features_from_hand_landmarks(hand_landmarks: HandLandmarksLike) -> list[float]:
    return relative_landmark_features(hand_landmarks.landmark)


def validate_feature_vector(features: Iterable[float]) -> list[float]:
    vector = [float(value) for value in features]
    if len(vector) != FEATURE_DIMENSION:
        raise ValueError(f"expected {FEATURE_DIMENSION} features, got {len(vector)}")
    return vector
