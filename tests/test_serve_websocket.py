import argparse
import unittest
from unittest import mock

from apps.serve_websocket import (
    parse_hold_seconds,
    parse_interval,
    parse_max_clients,
    parse_port,
    parse_release_grace_seconds,
    parse_threshold,
    resolve_ws_auth_token,
    safe_runtime_error,
)
from mediapipe_sword_sign.model_loader import UnsafeModelError


class ServeWebSocketSecurityTests(unittest.TestCase):
    def test_parse_port_rejects_out_of_range_values(self):
        self.assertEqual(parse_port("8765"), 8765)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_port("0")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_port("65536")

    def test_parse_threshold_requires_probability(self):
        self.assertEqual(parse_threshold("0.9"), 0.9)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_threshold("-0.1")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_threshold("nan")

    def test_parse_interval_and_limits_must_be_positive(self):
        self.assertEqual(parse_interval("0.1"), 0.1)
        self.assertEqual(parse_max_clients("1"), 1)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_interval("0")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_interval("nan")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_max_clients("0")

    def test_parse_stable_timing_accepts_non_negative_values(self):
        self.assertEqual(parse_hold_seconds("0"), 0.0)
        self.assertEqual(parse_hold_seconds("0.5"), 0.5)
        self.assertEqual(parse_release_grace_seconds("0.1"), 0.1)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_hold_seconds("-0.1")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_release_grace_seconds("nan")

    def test_resolve_ws_auth_token_trims_env_value(self):
        with mock.patch.dict("os.environ", {"GESTURE_WS_TOKEN": " secret "}, clear=True):
            self.assertEqual(resolve_ws_auth_token("GESTURE_WS_TOKEN"), "secret")

    def test_resolve_ws_auth_token_treats_blank_env_as_missing(self):
        with mock.patch.dict("os.environ", {"GESTURE_WS_TOKEN": " "}, clear=True):
            self.assertIsNone(resolve_ws_auth_token("GESTURE_WS_TOKEN"))

    def test_safe_runtime_error_does_not_expose_model_paths(self):
        self.assertEqual(
            safe_runtime_error(FileNotFoundError("model file not found: C:/secret/model.pkl")),
            "model_not_found",
        )
        self.assertEqual(
            safe_runtime_error(UnsafeModelError("refusing C:/secret/model.pkl")),
            "unsafe_model",
        )


if __name__ == "__main__":
    unittest.main()
