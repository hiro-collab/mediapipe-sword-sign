from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mediapipe_sword_sign.room_light import (  # noqa: E402
    DEFAULT_ROOM_LIGHT_MODEL_FILENAME,
    ROOM_LIGHT_SCHEMA_VERSION,
)


METADATA_COLUMNS = {
    "label",
    "timestamp",
    "frame_count",
    "first_frame_id",
    "last_frame_id",
    "duration_seconds",
}


def parse_positive_int(value: str, *, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"{name} must be greater than 0")
    return parsed


def parse_test_size(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--test-size must be a number") from exc
    if not 0 <= parsed < 1:
        raise argparse.ArgumentTypeError("--test-size must be between 0 and 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train a room-light ON/OFF classifier from collected sequence features.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input", default="room_light_data.csv")
    parser.add_argument("--output", default=DEFAULT_ROOM_LIGHT_MODEL_FILENAME)
    parser.add_argument(
        "--estimator",
        choices=("logistic-regression", "random-forest"),
        default="logistic-regression",
    )
    parser.add_argument("--test-size", type=parse_test_size, default=0.2)
    parser.add_argument("--random-state", type=lambda value: parse_positive_int(value, name="--random-state"), default=42)
    parser.add_argument("--max-iter", type=lambda value: parse_positive_int(value, name="--max-iter"), default=1000)
    parser.add_argument("--n-estimators", type=lambda value: parse_positive_int(value, name="--n-estimators"), default=300)
    return parser


def build_estimator(args: argparse.Namespace):
    if args.estimator == "random-forest":
        return RandomForestClassifier(
            n_estimators=args.n_estimators,
            class_weight="balanced",
            random_state=args.random_state,
        )
    return Pipeline(
        steps=[
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    max_iter=args.max_iter,
                    class_weight="balanced",
                    random_state=args.random_state,
                ),
            ),
        ]
    )


def can_stratify(labels: pd.Series) -> bool:
    counts = labels.value_counts()
    return len(counts) >= 2 and int(counts.min()) >= 2


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    data = pd.read_csv(args.input)
    if "label" not in data.columns:
        parser.error("input CSV must contain a label column")
    feature_columns = [
        column
        for column in data.columns
        if column not in METADATA_COLUMNS
    ]
    if not feature_columns:
        parser.error("input CSV does not contain feature columns")

    labels = data["label"].astype(str)
    if labels.nunique() < 2:
        parser.error("training requires at least two labels")

    features = data[feature_columns].astype(float)
    estimator = build_estimator(args)
    report: dict[str, object] = {}

    if args.test_size > 0 and len(data) >= 5 and can_stratify(labels):
        x_train, x_test, y_train, y_test = train_test_split(
            features,
            labels,
            test_size=args.test_size,
            random_state=args.random_state,
            stratify=labels,
        )
        estimator.fit(x_train, y_train)
        predictions = estimator.predict(x_test)
        report = {
            "accuracy": float(accuracy_score(y_test, predictions)),
            "classification_report": classification_report(
                y_test,
                predictions,
                output_dict=True,
                zero_division=0,
            ),
        }
    else:
        estimator.fit(features, labels)
        report = {
            "accuracy": None,
            "note": "trained without holdout evaluation; add more samples per class for a test split",
        }

    if args.test_size > 0 and report.get("accuracy") is not None:
        estimator.fit(features, labels)

    min_frames = 2
    if "frame_count" in data.columns:
        min_frames = max(2, int(data["frame_count"].max()))

    artifact = {
        "schema_version": ROOM_LIGHT_SCHEMA_VERSION,
        "model_version": "room_light_sklearn_v1",
        "created_at": time.time(),
        "estimator": estimator,
        "feature_names": feature_columns,
        "labels": sorted(labels.unique().tolist()),
        "min_frames": min_frames,
        "estimator_type": args.estimator,
    }
    joblib.dump(artifact, args.output)

    print(
        json.dumps(
            {
                "output": args.output,
                "samples": int(len(data)),
                "labels": data["label"].value_counts().to_dict(),
                "feature_count": len(feature_columns),
                "min_frames": min_frames,
                "evaluation": report,
            },
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
