import unittest

import numpy as np

from mediapipe_sword_sign.room_light import (
    ROOM_LIGHT_ELECTRIC_OFF_DAYLIT,
    ROOM_LIGHT_ELECTRIC_ON,
    ROOM_LIGHT_UNKNOWN,
    RoomLightClassifier,
    RoomLightDetector,
    RoomLightFeatureExtractor,
)


class FakeEstimator:
    classes_ = np.asarray([ROOM_LIGHT_ELECTRIC_OFF_DAYLIT, ROOM_LIGHT_ELECTRIC_ON])

    def predict_proba(self, values):
        self.last_shape = values.shape
        return np.asarray([[0.2, 0.8]])


def frame(value: int):
    return np.full((24, 32, 3), value, dtype=np.uint8)


class RoomLightTests(unittest.TestCase):
    def test_feature_extractor_requires_two_frame_sequences(self):
        extractor = RoomLightFeatureExtractor()
        first = extractor.extract_frame_features(frame(64))

        with self.assertRaises(ValueError):
            extractor.extract_sequence_features([first])

    def test_detector_rejects_window_size_below_two(self):
        with self.assertRaises(ValueError):
            RoomLightDetector(window_size=1)

    def test_detector_reports_insufficient_then_model_missing(self):
        detector = RoomLightDetector(window_size=2)

        first = detector.observe(frame(32), frame_id=1, timestamp=10.0)
        second = detector.observe(frame(48), frame_id=2, timestamp=11.0)

        self.assertEqual(first.label, ROOM_LIGHT_UNKNOWN)
        self.assertEqual(first.to_dict()["metadata"]["reason"], "insufficient_frames")
        self.assertEqual(second.label, ROOM_LIGHT_UNKNOWN)
        self.assertEqual(second.to_dict()["metadata"]["reason"], "model_not_loaded")
        self.assertEqual(second.to_dict()["sequence"]["frame_count"], 2)
        self.assertEqual(second.to_dict()["sequence"]["first_frame_id"], 1)
        self.assertEqual(second.to_dict()["sequence"]["last_frame_id"], 2)
        self.assertEqual(second.to_dict()["electric_light"]["state"], "unknown")

    def test_detector_uses_classifier_after_two_frames(self):
        estimator = FakeEstimator()
        detector = RoomLightDetector(
            classifier=RoomLightClassifier(estimator),
            window_size=2,
            threshold=0.6,
        )

        detector.observe(frame(32), frame_id=1, timestamp=10.0)
        state = detector.observe(frame(96), frame_id=2, timestamp=11.0)

        self.assertEqual(state.label, ROOM_LIGHT_ELECTRIC_ON)
        self.assertEqual(state.electric_state, "on")
        self.assertEqual(state.daylight_state, "absent")
        self.assertAlmostEqual(state.confidence, 0.8)
        self.assertEqual(estimator.last_shape[0], 1)

    def test_detector_marks_low_confidence_prediction_unknown(self):
        class LowConfidenceEstimator:
            classes_ = np.asarray([ROOM_LIGHT_ELECTRIC_ON, ROOM_LIGHT_ELECTRIC_OFF_DAYLIT])

            def predict_proba(self, values):
                return np.asarray([[0.55, 0.45]])

        detector = RoomLightDetector(
            classifier=RoomLightClassifier(LowConfidenceEstimator()),
            window_size=2,
            threshold=0.7,
        )

        detector.observe(frame(32), frame_id=1, timestamp=10.0)
        state = detector.observe(frame(96), frame_id=2, timestamp=11.0)

        self.assertEqual(state.label, ROOM_LIGHT_UNKNOWN)
        self.assertEqual(state.to_dict()["metadata"]["reason"], "below_threshold")
        self.assertEqual(state.to_dict()["metadata"]["raw_label"], ROOM_LIGHT_ELECTRIC_ON)

    def test_classifier_validates_feature_names_when_artifact_provides_them(self):
        classifier = RoomLightClassifier(
            FakeEstimator(),
            feature_names=["sequence:mean:frame:gray:mean"],
        )

        with self.assertRaises(ValueError):
            classifier.predict([0.0], feature_names=["different"])


if __name__ == "__main__":
    unittest.main()
