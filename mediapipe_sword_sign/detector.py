from __future__ import annotations

import time
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Mapping

from .features import (
    features_from_hand_landmarks,
    mirror_feature_vector,
    validate_feature_vector,
)
from .model_loader import load_gesture_model
from .types import DEFAULT_LABELS, GESTURE_NONE, GesturePrediction, GestureState


DEFAULT_SOURCE = "mediapipe_sword_sign"
DEFAULT_THRESHOLD = 0.9


@dataclass(frozen=True)
class DetectionResult:
    state: GestureState
    hand_landmarks: object | None = None
    raw_results: object | None = None


class SwordSignDetector:
    """Frame/landmark to GestureState detector.

    The class owns MediaPipe only when frame detection is used. Tests and other
    adapters can call predict_features() without importing camera or UI code.
    """

    def __init__(
        self,
        *,
        model=None,
        model_path: str | Path | None = None,
        expected_model_sha256: str | None = None,
        allow_untrusted_model: bool = False,
        threshold: float = DEFAULT_THRESHOLD,
        labels: Mapping[int, str] | None = None,
        source: str = DEFAULT_SOURCE,
        model_complexity: int = 1,
        use_mirrored_features: bool = True,
    ) -> None:
        self.model = model if model is not None else load_gesture_model(
            model_path,
            expected_sha256=expected_model_sha256,
            allow_untrusted=allow_untrusted_model,
        )
        self._threshold = DEFAULT_THRESHOLD
        self.threshold = threshold
        self.labels = dict(labels or DEFAULT_LABELS)
        self.source = source
        self.model_complexity = validate_model_complexity(model_complexity)
        self.use_mirrored_features = bool(use_mirrored_features)
        self._mp_hands = None
        self._hands = None

    def __enter__(self) -> "SwordSignDetector":
        self._ensure_hands()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        if self._hands is not None:
            self._hands.close()
            self._hands = None

    @property
    def threshold(self) -> float:
        return self._threshold

    @threshold.setter
    def threshold(self, value: float) -> None:
        self._threshold = validate_probability(value, name="threshold")

    def detect(self, frame_bgr, *, flip: bool = False, timestamp: float | None = None) -> GestureState:
        return self.detect_frame(frame_bgr, flip=flip, timestamp=timestamp).state

    def detect_frame(
        self,
        frame_bgr,
        *,
        flip: bool = False,
        timestamp: float | None = None,
    ) -> DetectionResult:
        cv2 = self._import_cv2()
        image = cv2.flip(frame_bgr, 1) if flip else frame_bgr
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        results = self._ensure_hands().process(image_rgb)

        if not results.multi_hand_landmarks:
            return DetectionResult(
                state=GestureState.no_hand(
                    source=self.source,
                    timestamp=timestamp,
                    labels=self.labels,
                ),
                raw_results=results,
            )

        hand_landmarks = results.multi_hand_landmarks[0]
        return DetectionResult(
            state=self.detect_landmarks(hand_landmarks, timestamp=timestamp),
            hand_landmarks=hand_landmarks,
            raw_results=results,
        )

    def detect_landmarks(self, hand_landmarks, *, timestamp: float | None = None) -> GestureState:
        return self.predict_features(
            features_from_hand_landmarks(hand_landmarks),
            timestamp=timestamp,
        )

    def predict_features(self, features, *, timestamp: float | None = None) -> GestureState:
        vector = validate_feature_vector(features)
        feature_variants = [("original", vector)]
        if self.use_mirrored_features:
            mirrored = mirror_feature_vector(vector)
            if mirrored != vector:
                feature_variants.append(("mirrored", mirrored))

        probability_rows = list(
            self.model.predict_proba([variant for _, variant in feature_variants])
        )
        if len(probability_rows) != len(feature_variants):
            raise ValueError("model returned a probability row count that does not match inputs")
        first_probability_row = list(probability_rows[0])
        classes = list(getattr(self.model, "classes_", range(len(first_probability_row))))
        candidates = []
        for probabilities in probability_rows:
            scores = {
                int(label): float(probability)
                for label, probability in zip(classes, probabilities)
            }
            prediction_label = max(scores, key=scores.get)
            prediction_name = self.labels.get(prediction_label, f"label_{prediction_label}")
            candidates.append(
                {
                    "scores": scores,
                    "prediction_label": prediction_label,
                    "prediction_name": prediction_name,
                    "prediction_confidence": scores[prediction_label],
                }
            )

        active_candidates = [
            candidate for candidate in candidates
            if (
                candidate["prediction_name"] != GESTURE_NONE
                and candidate["prediction_confidence"] >= self.threshold
            )
        ]
        if active_candidates:
            selected = max(active_candidates, key=lambda candidate: candidate["prediction_confidence"])
            prediction_label = int(selected["prediction_label"])
            scores = selected["scores"]
        else:
            selected = max(candidates, key=lambda candidate: candidate["prediction_confidence"])
            prediction_label = int(selected["prediction_label"])
            scores = selected["scores"]

        prediction_name = self.labels.get(prediction_label, f"label_{prediction_label}")
        prediction_confidence = scores[prediction_label]
        prediction_active = (
            prediction_confidence >= self.threshold
            and prediction_name != GESTURE_NONE
        )

        gestures: dict[str, GesturePrediction] = {}
        for label, name in self.labels.items():
            confidence = scores.get(label, 0.0)
            gestures[name] = GesturePrediction(
                name=name,
                label=label,
                confidence=confidence,
                active=bool(prediction_active and label == prediction_label),
            )

        return GestureState(
            timestamp=time.time() if timestamp is None else timestamp,
            source=self.source,
            hand_detected=True,
            primary=prediction_name if prediction_active else None,
            gestures=gestures,
        )

    def _ensure_hands(self):
        if self._hands is None:
            mp = self._import_mediapipe()
            self._mp_hands = mp.solutions.hands
            self._hands = self._mp_hands.Hands(model_complexity=self.model_complexity)
        return self._hands

    @staticmethod
    def _import_cv2():
        import cv2

        return cv2

    @staticmethod
    def _import_mediapipe():
        import mediapipe as mp

        return mp


def validate_probability(value: float, *, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise ValueError(f"{name} must be a finite number between 0 and 1")
    return parsed


def validate_model_complexity(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("model_complexity must be 0 or 1") from exc
    if parsed not in {0, 1}:
        raise ValueError("model_complexity must be 0 or 1")
    return parsed
