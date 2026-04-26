import unittest

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


if __name__ == "__main__":
    unittest.main()
