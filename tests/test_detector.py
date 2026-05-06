import unittest
from math import inf, nan

from mediapipe_sword_sign import FEATURE_DIMENSION, SwordSignDetector


class FakeModel:
    classes_ = [0, 1, 2]

    def __init__(self, probabilities):
        self.probabilities = probabilities

    def predict_proba(self, rows):
        return [self.probabilities for _ in rows]


class MirrorSensitiveModel:
    classes_ = [0, 1, 2]

    def __init__(self):
        self.rows = []

    def predict_proba(self, rows):
        self.rows = [list(row) for row in rows]
        probabilities = []
        for row in self.rows:
            if row[3] < 0.0:
                probabilities.append([0.95, 0.03, 0.02])
            else:
                probabilities.append([0.01, 0.02, 0.97])
        return probabilities


class NoneDominantModel:
    classes_ = [0, 1, 2]

    def predict_proba(self, rows):
        return [[0.91, 0.02, 0.98] for _ in rows]


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

    def test_predict_features_uses_mirrored_variant_for_opposite_hand(self):
        model = MirrorSensitiveModel()
        detector = SwordSignDetector(model=model, threshold=0.9)
        features = [0.0] * FEATURE_DIMENSION
        features[3] = 0.25

        state = detector.predict_features(features, timestamp=123.0)

        self.assertEqual(state.primary, "sword_sign")
        self.assertTrue(state.sword_sign.active)
        self.assertAlmostEqual(state.sword_sign.confidence, 0.95)
        self.assertEqual(len(model.rows), 2)
        self.assertEqual(model.rows[1][3], -0.25)

    def test_predict_features_can_disable_mirrored_variant(self):
        model = MirrorSensitiveModel()
        detector = SwordSignDetector(
            model=model,
            threshold=0.9,
            use_mirrored_features=False,
        )
        features = [0.0] * FEATURE_DIMENSION
        features[3] = 0.25

        state = detector.predict_features(features, timestamp=123.0)

        self.assertIsNone(state.primary)
        self.assertFalse(state.sword_sign.active)
        self.assertEqual(len(model.rows), 1)

    def test_predict_features_keeps_none_when_it_is_top_prediction(self):
        detector = SwordSignDetector(model=NoneDominantModel(), threshold=0.9)
        features = [0.0] * FEATURE_DIMENSION
        features[3] = 0.25

        state = detector.predict_features(features, timestamp=123.0)

        self.assertIsNone(state.primary)
        self.assertFalse(state.sword_sign.active)
        self.assertEqual(state.best_gesture().name, "none")

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
