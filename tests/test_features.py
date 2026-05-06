import unittest
from dataclasses import dataclass
from math import inf, nan

from mediapipe_sword_sign import (
    FEATURE_DIMENSION,
    mirror_feature_vector,
    relative_landmark_features,
)
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

    def test_relative_landmark_features_rejects_non_finite_coordinates(self):
        landmarks = [Landmark(0.0, 0.0, 0.0) for _ in range(21)]
        landmarks[3] = Landmark(inf, 0.0, 0.0)

        with self.assertRaises(ValueError):
            relative_landmark_features(landmarks)

    def test_mirror_feature_vector_flips_only_wrist_relative_x(self):
        features = []
        for index in range(21):
            features.extend([float(index + 1), float(index + 100), float(-index)])

        mirrored = mirror_feature_vector(features)

        self.assertEqual(len(mirrored), FEATURE_DIMENSION)
        for index in range(0, FEATURE_DIMENSION, 3):
            self.assertEqual(mirrored[index], -features[index])
            self.assertEqual(mirrored[index + 1], features[index + 1])
            self.assertEqual(mirrored[index + 2], features[index + 2])
        self.assertEqual(features[0], 1.0)

    def test_validate_feature_vector_rejects_non_finite_values(self):
        with self.assertRaises(ValueError):
            validate_feature_vector([0.0] * (FEATURE_DIMENSION - 1) + [nan])


if __name__ == "__main__":
    unittest.main()
