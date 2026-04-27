import tempfile
import unittest
from pathlib import Path

from mediapipe_sword_sign.model_loader import (
    UnsafeModelError,
    file_sha256,
    validate_model_path,
)


class ModelLoaderSecurityTests(unittest.TestCase):
    def test_trusted_root_allows_model_path(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            model_path = Path(directory) / "gesture_model.pkl"
            model_path.write_bytes(b"local model")

            resolved = validate_model_path(model_path, trusted_roots=[Path(directory)])

            self.assertEqual(resolved, model_path.resolve())

    def test_untrusted_root_rejects_model_path_without_hash(self):
        with (
            tempfile.TemporaryDirectory(dir=Path.cwd()) as trusted,
            tempfile.TemporaryDirectory(dir=Path.cwd()) as untrusted,
        ):
            model_path = Path(untrusted) / "gesture_model.pkl"
            model_path.write_bytes(b"external model")

            with self.assertRaises(UnsafeModelError):
                validate_model_path(model_path, trusted_roots=[Path(trusted)])

    def test_expected_sha256_allows_model_outside_trusted_roots(self):
        with (
            tempfile.TemporaryDirectory(dir=Path.cwd()) as trusted,
            tempfile.TemporaryDirectory(dir=Path.cwd()) as untrusted,
        ):
            model_path = Path(untrusted) / "gesture_model.pkl"
            model_path.write_bytes(b"external model")

            resolved = validate_model_path(
                model_path,
                expected_sha256=file_sha256(model_path),
                trusted_roots=[Path(trusted)],
            )

            self.assertEqual(resolved, model_path.resolve())

    def test_wrong_sha256_rejects_model(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            model_path = Path(directory) / "gesture_model.pkl"
            model_path.write_bytes(b"local model")

            with self.assertRaises(UnsafeModelError):
                validate_model_path(
                    model_path,
                    expected_sha256="0" * 64,
                    trusted_roots=[Path(directory)],
                )


if __name__ == "__main__":
    unittest.main()
