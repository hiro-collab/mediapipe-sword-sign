import unittest
from math import inf, nan

from mediapipe_sword_sign import FEATURE_DIMENSION, SwordSignDetector


class FakeModel:
    classes_ = [0, 1, 2]

    def __init__(self, probabilities):
        self.probabilities = probabilities

    def predict_proba(self, rows):
        return [self.probabilities for _ in rows]


class SwordSignDetectorTests(unittest.TestCase):
    def test_predict_features_marks_sword_sign_active_above_threshold(self):
        detector = SwordSignDetector(
            model=FakeModel([0.91, 0.04, 0.05]),
            threshold=0.9,
        )

        state = detector.predict_features([0.0] * FEATURE_DIMENSION, timestamp=123.0)

        self.assertTrue(state.hand_detected)
        self.assertEqual(state.primary, "sword_sign")
        self.assertTrue(state.sword_sign.active)
        self.assertAlmostEqual(state.sword_sign.confidence, 0.91)

    def test_predict_features_suppresses_low_confidence_prediction(self):
        detector = SwordSignDetector(
            model=FakeModel([0.89, 0.08, 0.03]),
            threshold=0.9,
        )

        state = detector.predict_features([0.0] * FEATURE_DIMENSION, timestamp=123.0)

        self.assertIsNone(state.primary)
        self.assertFalse(state.sword_sign.active)

    def test_predict_features_suppresses_none_prediction(self):
        detector = SwordSignDetector(
            model=FakeModel([0.01, 0.02, 0.97]),
            threshold=0.9,
        )

        state = detector.predict_features([0.0] * FEATURE_DIMENSION, timestamp=123.0)

        self.assertIsNone(state.primary)
        self.assertFalse(state.gesture("none").active)

    def test_threshold_must_be_finite_probability(self):
        for value in (-0.1, 1.1, nan, inf):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    SwordSignDetector(model=FakeModel([1.0]), threshold=value)

    def test_threshold_setter_rejects_invalid_values(self):
        detector = SwordSignDetector(model=FakeModel([1.0]), threshold=0.5)

        with self.assertRaises(ValueError):
            detector.threshold = inf

    def test_model_complexity_must_match_mediapipe_values(self):
        for value in (-1, 2, "nan"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    SwordSignDetector(model=FakeModel([1.0]), model_complexity=value)


if __name__ == "__main__":
    unittest.main()
