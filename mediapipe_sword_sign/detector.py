from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .features import features_from_hand_landmarks, validate_feature_vector
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
        threshold: float = DEFAULT_THRESHOLD,
        labels: Mapping[int, str] | None = None,
        source: str = DEFAULT_SOURCE,
        model_complexity: int = 1,
    ) -> None:
        self.model = model if model is not None else load_gesture_model(model_path)
        self.threshold = float(threshold)
        self.labels = dict(labels or DEFAULT_LABELS)
        self.source = source
        self.model_complexity = model_complexity
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
        probabilities = list(self.model.predict_proba([vector])[0])
        classes = list(getattr(self.model, "classes_", range(len(probabilities))))
        scores = {
            int(label): float(probability)
            for label, probability in zip(classes, probabilities)
        }

        prediction_label = max(scores, key=scores.get)
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
