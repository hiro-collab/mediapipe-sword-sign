from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import joblib
import numpy as np

from .model_loader import resolve_model_path


ROOM_LIGHT_SCHEMA_VERSION = 1
DEFAULT_ROOM_LIGHT_MODEL_FILENAME = "room_light_model.pkl"
DEFAULT_ROOM_LIGHT_SOURCE = "room_light_detector"

ROOM_LIGHT_ELECTRIC_ON = "electric_on"
ROOM_LIGHT_ELECTRIC_ON_DAYLIT = "electric_on_daylit"
ROOM_LIGHT_ELECTRIC_OFF_DAYLIT = "electric_off_daylit"
ROOM_LIGHT_ELECTRIC_OFF_DARK = "electric_off_dark"
ROOM_LIGHT_UNKNOWN = "unknown"

DEFAULT_ROOM_LIGHT_LABELS = (
    ROOM_LIGHT_ELECTRIC_ON,
    ROOM_LIGHT_ELECTRIC_ON_DAYLIT,
    ROOM_LIGHT_ELECTRIC_OFF_DAYLIT,
    ROOM_LIGHT_ELECTRIC_OFF_DARK,
    ROOM_LIGHT_UNKNOWN,
)
ELECTRIC_ON_LABELS = {ROOM_LIGHT_ELECTRIC_ON, ROOM_LIGHT_ELECTRIC_ON_DAYLIT}
ELECTRIC_OFF_LABELS = {ROOM_LIGHT_ELECTRIC_OFF_DAYLIT, ROOM_LIGHT_ELECTRIC_OFF_DARK}
DAYLIGHT_PRESENT_LABELS = {
    ROOM_LIGHT_ELECTRIC_ON_DAYLIT,
    ROOM_LIGHT_ELECTRIC_OFF_DAYLIT,
}
DAYLIGHT_ABSENT_LABELS = {ROOM_LIGHT_ELECTRIC_ON, ROOM_LIGHT_ELECTRIC_OFF_DARK}

SUMMARY_STATS = ("mean", "std", "min", "max", "p10", "p50", "p90")
CHANNEL_NAMES = (
    "gray",
    "hsv_value",
    "hsv_saturation",
    "red",
    "green",
    "blue",
    "chroma_red",
    "chroma_green",
    "chroma_blue",
)
SEQUENCE_STATS = ("mean", "std", "min", "max", "delta", "abs_delta")


@dataclass(frozen=True)
class FrameObservation:
    frame_id: int
    timestamp: float
    features: tuple[float, ...]


@dataclass(frozen=True)
class RoomLightPrediction:
    label: str
    confidence: float
    probabilities: Mapping[str, float]


@dataclass(frozen=True)
class RoomLightState:
    timestamp: float
    source: str
    label: str
    confidence: float
    probabilities: Mapping[str, float]
    frame_count: int
    required_frame_count: int
    first_frame_id: int | None
    last_frame_id: int | None
    duration_seconds: float
    model_loaded: bool
    metadata: Mapping[str, object] | None = None

    @property
    def electric_state(self) -> str:
        if self.label in ELECTRIC_ON_LABELS:
            return "on"
        if self.label in ELECTRIC_OFF_LABELS:
            return "off"
        return "unknown"

    @property
    def daylight_state(self) -> str:
        if self.label in DAYLIGHT_PRESENT_LABELS:
            return "present"
        if self.label in DAYLIGHT_ABSENT_LABELS:
            return "absent"
        return "unknown"

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": "room_light_state",
            "schema_version": ROOM_LIGHT_SCHEMA_VERSION,
            "timestamp": float(self.timestamp),
            "source": self.source,
            "label": self.label,
            "confidence": float(self.confidence),
            "probabilities": {
                str(label): float(probability)
                for label, probability in self.probabilities.items()
            },
            "electric_light": {
                "state": self.electric_state,
                "confidence": float(self.confidence),
            },
            "daylight": {
                "state": self.daylight_state,
                "confidence": float(self.confidence),
            },
            "sequence": {
                "frame_count": int(self.frame_count),
                "required_frame_count": int(self.required_frame_count),
                "first_frame_id": self.first_frame_id,
                "last_frame_id": self.last_frame_id,
                "duration_seconds": float(self.duration_seconds),
            },
            "model": {
                "loaded": bool(self.model_loaded),
            },
        }
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


class RoomLightFeatureExtractor:
    def __init__(
        self,
        *,
        resize_width: int = 64,
        resize_height: int = 36,
        histogram_bins: int = 16,
        roi_grid: tuple[int, int] = (3, 3),
    ) -> None:
        self.resize_width = _positive_int(resize_width, name="resize_width")
        self.resize_height = _positive_int(resize_height, name="resize_height")
        self.histogram_bins = _positive_int(histogram_bins, name="histogram_bins")
        rows, cols = roi_grid
        self.roi_grid = (
            _positive_int(rows, name="roi_grid rows"),
            _positive_int(cols, name="roi_grid cols"),
        )

    @property
    def frame_feature_names(self) -> list[str]:
        names: list[str] = []
        for channel in CHANNEL_NAMES:
            for stat in SUMMARY_STATS:
                names.append(f"frame:{channel}:{stat}")
        names.extend(
            f"frame:gray_hist:{index:02d}"
            for index in range(self.histogram_bins)
        )
        rows, cols = self.roi_grid
        for row in range(rows):
            for col in range(cols):
                names.append(f"frame:roi_gray:r{row}c{col}:mean")
        return names

    @property
    def sequence_feature_names(self) -> list[str]:
        frame_names = self.frame_feature_names
        return [
            f"sequence:{stat}:{name}"
            for stat in SEQUENCE_STATS
            for name in frame_names
        ]

    def extract_frame_features(self, frame_bgr: object) -> tuple[float, ...]:
        cv2 = _import_cv2()
        bgr_uint8 = _as_uint8_bgr(frame_bgr)
        resized = cv2.resize(
            bgr_uint8,
            (self.resize_width, self.resize_height),
            interpolation=cv2.INTER_AREA,
        )
        bgr = resized.astype(np.float32) / 255.0
        blue = bgr[:, :, 0]
        green = bgr[:, :, 1]
        red = bgr[:, :, 2]
        channel_sum = red + green + blue + 1e-6
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV).astype(np.float32)
        saturation = hsv[:, :, 1] / 255.0
        value = hsv[:, :, 2] / 255.0

        channels = (
            gray,
            value,
            saturation,
            red,
            green,
            blue,
            red / channel_sum,
            green / channel_sum,
            blue / channel_sum,
        )

        features: list[float] = []
        for channel in channels:
            features.extend(_summary(channel))

        histogram, _ = np.histogram(
            gray,
            bins=self.histogram_bins,
            range=(0.0, 1.0),
        )
        histogram = histogram.astype(np.float64)
        histogram_sum = float(histogram.sum()) or 1.0
        features.extend((histogram / histogram_sum).tolist())

        rows, cols = self.roi_grid
        for row in range(rows):
            y0 = round(row * gray.shape[0] / rows)
            y1 = round((row + 1) * gray.shape[0] / rows)
            for col in range(cols):
                x0 = round(col * gray.shape[1] / cols)
                x1 = round((col + 1) * gray.shape[1] / cols)
                roi = gray[y0:y1, x0:x1]
                features.append(float(roi.mean()) if roi.size else 0.0)

        return tuple(float(value) for value in features)

    def extract_sequence_features(
        self,
        frame_features: Sequence[Sequence[float]],
    ) -> tuple[float, ...]:
        if len(frame_features) < 2:
            raise ValueError("room light sequence features require at least 2 frames")

        matrix = np.asarray(frame_features, dtype=np.float64)
        if matrix.ndim != 2:
            raise ValueError("frame_features must be a 2D sequence")
        expected_width = len(self.frame_feature_names)
        if matrix.shape[1] != expected_width:
            raise ValueError(
                f"frame feature length mismatch: expected {expected_width}, got {matrix.shape[1]}"
            )

        delta = matrix[-1] - matrix[0]
        sequence = np.concatenate(
            (
                matrix.mean(axis=0),
                matrix.std(axis=0),
                matrix.min(axis=0),
                matrix.max(axis=0),
                delta,
                np.abs(delta),
            )
        )
        return tuple(float(value) for value in sequence)


class RoomLightClassifier:
    def __init__(
        self,
        estimator: object,
        *,
        feature_names: Sequence[str] | None = None,
        min_frames: int | None = None,
        model_version: str | None = None,
    ) -> None:
        self.estimator = estimator
        self.feature_names = list(feature_names) if feature_names is not None else None
        self.min_frames = None if min_frames is None else _minimum_frame_count(min_frames)
        self.model_version = model_version

    @classmethod
    def load(
        cls,
        model_path: str | Path | None = None,
        *,
        expected_sha256: str | None = None,
        allow_untrusted: bool = False,
    ) -> "RoomLightClassifier":
        artifact = joblib.load(
            resolve_model_path(
                model_path,
                candidates=default_room_light_model_candidates(),
                expected_sha256=expected_sha256,
                allow_untrusted=allow_untrusted,
            )
        )
        if isinstance(artifact, Mapping):
            estimator = artifact.get("estimator") or artifact.get("model")
            if estimator is None:
                raise ValueError("room light model artifact must contain estimator or model")
            feature_names = artifact.get("feature_names")
            min_frames = artifact.get("min_frames")
            model_version = artifact.get("model_version")
            return cls(
                estimator,
                feature_names=feature_names if feature_names is not None else None,
                min_frames=int(min_frames) if min_frames is not None else None,
                model_version=str(model_version) if model_version is not None else None,
            )
        return cls(artifact)

    def predict(
        self,
        sequence_features: Sequence[float],
        *,
        feature_names: Sequence[str] | None = None,
    ) -> RoomLightPrediction:
        if self.feature_names is not None:
            if feature_names is None:
                raise ValueError("room light model expects named features")
            if list(feature_names) != self.feature_names:
                raise ValueError("room light feature names do not match the loaded model")

        values = np.asarray([sequence_features], dtype=np.float64)
        predict_proba = getattr(self.estimator, "predict_proba", None)
        if callable(predict_proba):
            probabilities_raw = list(predict_proba(values)[0])
            classes = getattr(self.estimator, "classes_", range(len(probabilities_raw)))
            probabilities = {
                str(label): float(probability)
                for label, probability in zip(classes, probabilities_raw)
            }
            label = max(probabilities, key=probabilities.get)
            return RoomLightPrediction(
                label=label,
                confidence=probabilities[label],
                probabilities=probabilities,
            )

        predict = getattr(self.estimator, "predict", None)
        if not callable(predict):
            raise ValueError("room light estimator must expose predict or predict_proba")
        label = str(predict(values)[0])
        return RoomLightPrediction(
            label=label,
            confidence=1.0,
            probabilities={label: 1.0},
        )


class RoomLightDetector:
    def __init__(
        self,
        *,
        classifier: RoomLightClassifier | object | None = None,
        model_path: str | Path | None = None,
        expected_model_sha256: str | None = None,
        allow_untrusted_model: bool = False,
        feature_extractor: RoomLightFeatureExtractor | None = None,
        window_size: int = 2,
        threshold: float = 0.6,
        source: str = DEFAULT_ROOM_LIGHT_SOURCE,
    ) -> None:
        self.feature_extractor = feature_extractor or RoomLightFeatureExtractor()
        self.window_size = _minimum_frame_count(window_size)
        self.threshold = _probability(threshold, name="threshold")
        self.source = _non_empty_text(source, name="source")
        self.history: deque[FrameObservation] = deque(maxlen=self.window_size)
        self.classifier = self._resolve_classifier(
            classifier=classifier,
            model_path=model_path,
            expected_model_sha256=expected_model_sha256,
            allow_untrusted_model=allow_untrusted_model,
        )
        if self.classifier is not None and self.classifier.min_frames is not None:
            self.window_size = max(self.window_size, self.classifier.min_frames)
            self.history = deque(self.history, maxlen=self.window_size)

    @property
    def model_loaded(self) -> bool:
        return self.classifier is not None

    @property
    def required_frame_count(self) -> int:
        if self.classifier is not None and self.classifier.min_frames is not None:
            return max(2, self.classifier.min_frames)
        return self.window_size

    def clear(self) -> None:
        self.history.clear()

    def observe(
        self,
        frame_bgr: object,
        *,
        frame_id: int,
        timestamp: float | None = None,
    ) -> RoomLightState:
        stamp = time.time() if timestamp is None else float(timestamp)
        observation = FrameObservation(
            frame_id=int(frame_id),
            timestamp=stamp,
            features=self.feature_extractor.extract_frame_features(frame_bgr),
        )
        self.history.append(observation)
        return self.current_state(timestamp=stamp)

    def current_state(self, *, timestamp: float | None = None) -> RoomLightState:
        stamp = time.time() if timestamp is None else float(timestamp)
        observations = list(self.history)
        duration = _duration_seconds(observations)
        first_frame_id = observations[0].frame_id if observations else None
        last_frame_id = observations[-1].frame_id if observations else None

        if len(observations) < self.required_frame_count:
            return self._unknown_state(
                timestamp=stamp,
                observations=observations,
                duration_seconds=duration,
                reason="insufficient_frames",
            )

        if self.classifier is None:
            return self._unknown_state(
                timestamp=stamp,
                observations=observations,
                duration_seconds=duration,
                reason="model_not_loaded",
            )

        sequence_features = self.feature_extractor.extract_sequence_features(
            [observation.features for observation in observations]
        )
        prediction = self.classifier.predict(
            sequence_features,
            feature_names=self.feature_extractor.sequence_feature_names,
        )
        label = prediction.label
        metadata: dict[str, object] = {}
        if prediction.confidence < self.threshold:
            metadata["raw_label"] = label
            metadata["reason"] = "below_threshold"
            label = ROOM_LIGHT_UNKNOWN

        return RoomLightState(
            timestamp=stamp,
            source=self.source,
            label=label,
            confidence=prediction.confidence,
            probabilities=prediction.probabilities,
            frame_count=len(observations),
            required_frame_count=self.required_frame_count,
            first_frame_id=first_frame_id,
            last_frame_id=last_frame_id,
            duration_seconds=duration,
            model_loaded=True,
            metadata=metadata or None,
        )

    def _unknown_state(
        self,
        *,
        timestamp: float,
        observations: Sequence[FrameObservation],
        duration_seconds: float,
        reason: str,
    ) -> RoomLightState:
        return RoomLightState(
            timestamp=timestamp,
            source=self.source,
            label=ROOM_LIGHT_UNKNOWN,
            confidence=0.0,
            probabilities={ROOM_LIGHT_UNKNOWN: 1.0},
            frame_count=len(observations),
            required_frame_count=self.required_frame_count,
            first_frame_id=observations[0].frame_id if observations else None,
            last_frame_id=observations[-1].frame_id if observations else None,
            duration_seconds=duration_seconds,
            model_loaded=self.model_loaded,
            metadata={"reason": reason},
        )

    @staticmethod
    def _resolve_classifier(
        *,
        classifier: RoomLightClassifier | object | None,
        model_path: str | Path | None,
        expected_model_sha256: str | None,
        allow_untrusted_model: bool,
    ) -> RoomLightClassifier | None:
        if isinstance(classifier, RoomLightClassifier):
            return classifier
        if classifier is not None:
            return RoomLightClassifier(classifier)
        if model_path is not None or expected_model_sha256 is not None:
            return RoomLightClassifier.load(
                model_path,
                expected_sha256=expected_model_sha256,
                allow_untrusted=allow_untrusted_model,
            )
        if any(path.exists() for path in default_room_light_model_candidates()):
            return RoomLightClassifier.load(allow_untrusted=allow_untrusted_model)
        return None


def default_room_light_model_candidates() -> list[Path]:
    return [
        Path.cwd() / DEFAULT_ROOM_LIGHT_MODEL_FILENAME,
        Path(__file__).resolve().parent.parent / DEFAULT_ROOM_LIGHT_MODEL_FILENAME,
    ]


def room_light_training_feature_names(
    feature_extractor: RoomLightFeatureExtractor | None = None,
) -> list[str]:
    extractor = feature_extractor or RoomLightFeatureExtractor()
    return extractor.sequence_feature_names


def _summary(channel: np.ndarray) -> list[float]:
    return [
        float(channel.mean()),
        float(channel.std()),
        float(channel.min()),
        float(channel.max()),
        float(np.percentile(channel, 10)),
        float(np.percentile(channel, 50)),
        float(np.percentile(channel, 90)),
    ]


def _as_uint8_bgr(frame_bgr: object) -> np.ndarray:
    frame = np.asarray(frame_bgr)
    if frame.ndim != 3 or frame.shape[2] < 3:
        raise ValueError("frame_bgr must be an HxWx3 BGR image")
    frame = frame[:, :, :3]
    if frame.dtype == np.uint8:
        return frame
    frame_float = frame.astype(np.float32)
    if frame_float.size and float(np.nanmax(frame_float)) <= 1.0:
        frame_float = frame_float * 255.0
    frame_float = np.nan_to_num(frame_float, nan=0.0, posinf=255.0, neginf=0.0)
    return np.clip(frame_float, 0, 255).astype(np.uint8)


def _duration_seconds(observations: Sequence[FrameObservation]) -> float:
    if len(observations) < 2:
        return 0.0
    return max(0.0, observations[-1].timestamp - observations[0].timestamp)


def _positive_int(value: int, *, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return parsed


def _minimum_frame_count(value: int) -> int:
    parsed = _positive_int(value, name="window_size")
    if parsed < 2:
        raise ValueError("room light detection requires at least 2 frames")
    return parsed


def _probability(value: float, *, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not 0.0 <= parsed <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return parsed


def _non_empty_text(value: str, *, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _import_cv2():
    import cv2

    return cv2
