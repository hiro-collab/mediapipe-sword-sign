from __future__ import annotations

from pathlib import Path
from typing import Iterable

import joblib


DEFAULT_MODEL_FILENAME = "gesture_model.pkl"


def default_model_candidates() -> list[Path]:
    package_root = Path(__file__).resolve().parent.parent
    return [
        Path.cwd() / DEFAULT_MODEL_FILENAME,
        package_root / DEFAULT_MODEL_FILENAME,
    ]


def resolve_model_path(
    model_path: str | Path | None = None,
    *,
    candidates: Iterable[Path] | None = None,
) -> Path:
    if model_path is not None:
        path = Path(model_path)
        if path.exists():
            return path
        raise FileNotFoundError(f"model file not found: {path}")

    for candidate in candidates or default_model_candidates():
        if candidate.exists():
            return candidate

    searched = ", ".join(str(path) for path in default_model_candidates())
    raise FileNotFoundError(f"model file not found. searched: {searched}")


def load_gesture_model(model_path: str | Path | None = None):
    return joblib.load(resolve_model_path(model_path))
