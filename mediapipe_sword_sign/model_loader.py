from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Iterable

import joblib


DEFAULT_MODEL_FILENAME = "gesture_model.pkl"


class UnsafeModelError(RuntimeError):
    """Raised when a pickle/joblib model path crosses the trusted boundary."""


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_model_candidates() -> list[Path]:
    return [
        Path.cwd() / DEFAULT_MODEL_FILENAME,
        project_root() / DEFAULT_MODEL_FILENAME,
    ]


def default_trusted_roots() -> list[Path]:
    return [
        project_root(),
    ]


def resolve_model_path(
    model_path: str | Path | None = None,
    *,
    candidates: Iterable[Path] | None = None,
    expected_sha256: str | None = None,
    trusted_roots: Iterable[Path] | None = None,
    allow_untrusted: bool = False,
) -> Path:
    if model_path is not None:
        path = Path(model_path)
        if path.exists():
            return validate_model_path(
                path,
                expected_sha256=expected_sha256,
                trusted_roots=trusted_roots,
                allow_untrusted=allow_untrusted,
            )
        raise FileNotFoundError(f"model file not found: {path}")

    for candidate in candidates or default_model_candidates():
        if candidate.exists():
            return validate_model_path(
                candidate,
                expected_sha256=expected_sha256,
                trusted_roots=trusted_roots,
                allow_untrusted=allow_untrusted,
            )

    searched = ", ".join(str(path) for path in default_model_candidates())
    raise FileNotFoundError(f"model file not found. searched: {searched}")


def validate_model_path(
    model_path: str | Path,
    *,
    expected_sha256: str | None = None,
    trusted_roots: Iterable[Path] | None = None,
    allow_untrusted: bool = False,
) -> Path:
    path = Path(model_path).resolve()

    if expected_sha256 is not None:
        expected_sha256 = expected_sha256.strip().lower()
        if len(expected_sha256) != 64 or any(char not in "0123456789abcdef" for char in expected_sha256):
            raise UnsafeModelError("expected model SHA-256 must be a 64-character hex string")
        actual_sha256 = file_sha256(path)
        if not hmac.compare_digest(actual_sha256, expected_sha256):
            raise UnsafeModelError(
                f"model SHA-256 mismatch for {path}: expected {expected_sha256}, got {actual_sha256}"
            )
        return path

    if allow_untrusted:
        return path

    roots = [root.resolve() for root in trusted_roots or default_trusted_roots()]
    if any(_is_relative_to(path, root) for root in roots):
        return path

    roots_text = ", ".join(str(root) for root in roots)
    raise UnsafeModelError(
        "refusing to load joblib/pickle model outside trusted roots without SHA-256 "
        f"verification. model={path}; trusted_roots={roots_text}"
    )


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_gesture_model(
    model_path: str | Path | None = None,
    *,
    expected_sha256: str | None = None,
    trusted_roots: Iterable[Path] | None = None,
    allow_untrusted: bool = False,
):
    return joblib.load(
        resolve_model_path(
            model_path,
            expected_sha256=expected_sha256,
            trusted_roots=trusted_roots,
            allow_untrusted=allow_untrusted,
        )
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
