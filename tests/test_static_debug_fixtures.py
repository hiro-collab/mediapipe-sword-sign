import unittest
from pathlib import Path

from mediapipe_sword_sign import GESTURE_SWORD_SIGN, SwordSignDetector


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = PROJECT_ROOT / "tests" / "pict_for_debug"
MODEL_PATH = PROJECT_ROOT / "gesture_model.pkl"


REQUIRED_FIXTURES = {
    "positive": FIXTURE_DIR / "sword_sign_in_b.png",
    "no_hand": FIXTURE_DIR / "hand_out.png",
    "gesture_negative": FIXTURE_DIR / "hand_in.png",
    "sword_like_negative": FIXTURE_DIR / "sword_sign_in.png",
}


def require_local_debug_assets() -> None:
    missing = [
        path
        for path in [MODEL_PATH, *REQUIRED_FIXTURES.values()]
        if not path.exists()
    ]
    if missing:
        names = ", ".join(path.name for path in missing)
        raise unittest.SkipTest(f"local debug assets are not present: {names}")


class StaticDebugFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_local_debug_assets()
        try:
            import cv2
        except ImportError as exc:
            raise unittest.SkipTest("opencv-python is not installed") from exc

        cls.cv2 = cv2
        cls.detector = SwordSignDetector(
            model_path=MODEL_PATH,
            threshold=0.9,
            model_complexity=0,
        )

    @classmethod
    def tearDownClass(cls):
        detector = getattr(cls, "detector", None)
        if detector is not None:
            detector.close()

    def read_fixture(self, name: str):
        path = REQUIRED_FIXTURES[name]
        image = self.cv2.imread(str(path))
        self.assertIsNotNone(image, f"failed to read fixture: {path}")
        return image

    def detect_fixture(self, name: str):
        return self.detector.detect(self.read_fixture(name), timestamp=123.0)

    def test_sword_sign_fixture_detects_active_sword_sign(self):
        state = self.detect_fixture("positive")

        self.assertTrue(state.hand_detected)
        self.assertEqual(state.primary, GESTURE_SWORD_SIGN)
        self.assertTrue(state.sword_sign.active)
        self.assertGreaterEqual(state.sword_sign.confidence, 0.95)

    def test_no_hand_fixture_stays_inactive(self):
        state = self.detect_fixture("no_hand")

        self.assertFalse(state.hand_detected)
        self.assertIsNone(state.primary)
        for prediction in state.gestures.values():
            self.assertFalse(prediction.active)

    def test_non_sword_hand_fixtures_do_not_trigger_sword_sign(self):
        for name in ("gesture_negative", "sword_like_negative"):
            with self.subTest(name=name):
                state = self.detect_fixture(name)

                self.assertTrue(state.hand_detected)
                self.assertNotEqual(state.primary, GESTURE_SWORD_SIGN)
                self.assertFalse(state.sword_sign.active)


if __name__ == "__main__":
    unittest.main()
