import argparse
import unittest
from unittest import mock

from apps.serve_websocket import (
    build_parser,
    parse_interval,
    parse_max_clients,
    parse_max_message_bytes,
    parse_max_queue,
    parse_port,
    parse_threshold,
    resolve_ws_auth_token,
    safe_runtime_error,
)
from mediapipe_sword_sign import UnsafeModelError


class ServeWebSocketTests(unittest.TestCase):
    def test_parse_port_threshold_and_interval_reject_invalid_values(self):
        self.assertEqual(parse_port("8765"), 8765)
        self.assertEqual(parse_threshold("0.9"), 0.9)
        self.assertEqual(parse_interval("0.1"), 0.1)

        for parser, value in (
            (parse_port, "0"),
            (parse_threshold, "nan"),
            (parse_interval, "nan"),
            (parse_interval, "0"),
        ):
            with self.subTest(parser=parser.__name__, value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    parser(value)

    def test_parse_websocket_limits(self):
        self.assertEqual(parse_max_clients("8"), 8)
        self.assertEqual(parse_max_message_bytes("4096"), 4096)
        self.assertEqual(parse_max_queue("4"), 4)

        for parser in (parse_max_clients, parse_max_message_bytes, parse_max_queue):
            with self.subTest(parser=parser.__name__):
                with self.assertRaises(argparse.ArgumentTypeError):
                    parser("0")

    def test_build_parser_accepts_origin_and_limits(self):
        args = build_parser().parse_args(
            [
                "--allowed-origin",
                "http://localhost:3000",
                "--max-clients",
                "2",
                "--max-message-bytes",
                "1024",
                "--max-queue",
                "1",
            ]
        )

        self.assertEqual(args.allowed_origins, ["http://localhost:3000"])
        self.assertEqual(args.max_clients, 2)
        self.assertEqual(args.max_message_bytes, 1024)
        self.assertEqual(args.max_queue, 1)

    def test_resolve_ws_auth_token_trims_empty_values(self):
        with mock.patch.dict("os.environ", {"GESTURE_WS_TOKEN": " secret "}, clear=True):
            self.assertEqual(resolve_ws_auth_token("GESTURE_WS_TOKEN"), "secret")
        with mock.patch.dict("os.environ", {"GESTURE_WS_TOKEN": " "}, clear=True):
            self.assertIsNone(resolve_ws_auth_token("GESTURE_WS_TOKEN"))

    def test_safe_runtime_error_does_not_leak_paths_or_traces(self):
        self.assertEqual(safe_runtime_error(FileNotFoundError("C:\\Secret\\model.joblib")), "model_not_found")
        self.assertEqual(safe_runtime_error(UnsafeModelError("bad hash")), "unsafe_model")
        self.assertEqual(safe_runtime_error(RuntimeError("unexpected stack")), "runtime_failed")


if __name__ == "__main__":
    unittest.main()
