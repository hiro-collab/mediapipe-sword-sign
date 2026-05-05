import argparse
import unittest

from apps.serve_camera_hub import (
    camera_status_payload,
    parse_interval,
    parse_jpeg_quality,
    parse_port,
    parse_threshold,
    parse_window_size,
    resolve_auth_token,
)


class ServeCameraHubTests(unittest.TestCase):
    def test_parse_port_and_threshold(self):
        self.assertEqual(parse_port("8765"), 8765)
        self.assertEqual(parse_threshold("0.6"), 0.6)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_port("0")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_threshold("nan")

    def test_parse_window_size_requires_two_or_more_frames(self):
        self.assertEqual(parse_window_size("2"), 2)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_window_size("1")

    def test_parse_optional_intervals_and_jpeg_quality(self):
        self.assertEqual(parse_interval("0"), 0.0)
        self.assertEqual(parse_jpeg_quality("80"), 80)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_interval("-1")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_jpeg_quality("101")

    def test_resolve_auth_token_trims_empty_values(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"CAMERA_HUB_WS_TOKEN": " secret "}, clear=True):
            self.assertEqual(resolve_auth_token("CAMERA_HUB_WS_TOKEN"), "secret")
        with mock.patch.dict(os.environ, {"CAMERA_HUB_WS_TOKEN": " "}, clear=True):
            self.assertIsNone(resolve_auth_token("CAMERA_HUB_WS_TOKEN"))

    def test_camera_status_payload_reports_processors(self):
        payload = camera_status_payload(
            camera_index=0,
            frame_number=12,
            fps=29.97,
            room_light_model_loaded=True,
        )

        self.assertEqual(payload["type"], "camera_status")
        self.assertEqual(payload["camera"]["selected_index"], 0)
        self.assertTrue(payload["processors"]["room_light"]["model_loaded"])


if __name__ == "__main__":
    unittest.main()
