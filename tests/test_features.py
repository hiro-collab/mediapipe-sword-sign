import math
import unittest
from dataclasses import dataclass

from mediapipe_sword_sign import FEATURE_DIMENSION, relative_landmark_features
from mediapipe_sword_sign.features import validate_feature_vector


@dataclass
class Landmark:
    x: float
    y: float
    z: float


class FeatureTests(unittest.TestCase):
    def test_relative_landmark_features_uses_wrist_as_origin(self):
        landmarks = [Landmark(1.0, 2.0, 3.0)]
        landmarks.extend(Landmark(1.0 + i, 2.0 + i * 2, 3.0 + i * 3) for i in range(1, 21))

        features = relative_landmark_features(landmarks)

        self.assertEqual(len(features), FEATURE_DIMENSION)
        self.assertEqual(features[:3], [0.0, 0.0, 0.0])
        self.assertEqual(features[3:6], [1.0, 2.0, 3.0])

    def test_relative_landmark_features_rejects_wrong_landmark_count(self):
        with self.assertRaises(ValueError):
            relative_landmark_features([Landmark(0.0, 0.0, 0.0)])

    def test_relative_landmark_features_rejects_non_finite_values(self):
        landmarks = [Landmark(0.0, 0.0, 0.0) for _ in range(21)]
        landmarks[3] = Landmark(math.inf, 0.0, 0.0)

        with self.assertRaises(ValueError):
            relative_landmark_features(landmarks)

    def test_validate_feature_vector_rejects_non_finite_values(self):
        features = [0.0] * FEATURE_DIMENSION
        features[-1] = math.nan

        with self.assertRaises(ValueError):
            validate_feature_vector(features)


if __name__ == "__main__":
    unittest.main()
