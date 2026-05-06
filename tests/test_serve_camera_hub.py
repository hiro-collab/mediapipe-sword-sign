import argparse
import unittest

from apps.serve_camera_hub import (
    build_parser,
    camera_status_payload,
    compressed_image_binary_payload,
    due,
    FfmpegPipeCapture,
    fourcc_to_text,
    normalized_landmarks_payload,
    parse_camera_backend,
    parse_camera_fps,
    parse_fourcc,
    parse_camera_source,
    parse_ffmpeg_capture_options,
    parse_image_transport,
    parse_interval,
    parse_jpeg_quality,
    parse_max_clients,
    parse_max_message_bytes,
    parse_max_queue,
    parse_model_complexity,
    parse_port,
    parse_threshold,
    parse_tool_path,
    parse_timeout_ms,
    redact_camera_source,
    resolve_auth_token,
    safe_runtime_error,
)


class ServeCameraHubTests(unittest.TestCase):
    def test_parse_port_and_threshold(self):
        self.assertEqual(parse_port("8765"), 8765)
        self.assertEqual(parse_threshold("0.6"), 0.6)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_port("0")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_threshold("nan")

    def test_parse_websocket_limits(self):
        self.assertEqual(parse_max_clients("8"), 8)
        self.assertEqual(parse_max_message_bytes("4096"), 4096)
        self.assertEqual(parse_max_queue("4"), 4)
        for parser in (parse_max_clients, parse_max_message_bytes, parse_max_queue):
            with self.subTest(parser=parser.__name__):
                with self.assertRaises(argparse.ArgumentTypeError):
                    parser("0")

    def test_parse_optional_intervals_and_jpeg_quality(self):
        self.assertEqual(parse_interval("0"), 0.0)
        self.assertEqual(parse_jpeg_quality("80"), 80)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_interval("-1")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_jpeg_quality("101")

    def test_parse_image_transport_accepts_binary_and_json(self):
        self.assertEqual(parse_image_transport("binary"), "binary")
        self.assertEqual(parse_image_transport(" JSON "), "json")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_image_transport("base64")

    def test_parse_camera_options(self):
        self.assertEqual(parse_camera_backend(" DSHOW "), "dshow")
        self.assertEqual(parse_camera_backend(" ffmpeg-pipe "), "ffmpeg-pipe")
        self.assertEqual(parse_camera_fps("30"), 30.0)
        self.assertEqual(parse_fourcc("mjpg"), "MJPG")
        self.assertEqual(parse_tool_path(" ffmpeg "), "ffmpeg")
        self.assertEqual(
            parse_camera_source(" rtsp://127.0.0.1:8554/cam0 "),
            "rtsp://127.0.0.1:8554/cam0",
        )
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_camera_backend("v4l2")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_camera_fps("0")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_fourcc("jpg")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_camera_source(" ")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_tool_path(" ")

    def test_parse_model_complexity_accepts_mediapipe_values(self):
        self.assertEqual(parse_model_complexity("0"), 0)
        self.assertEqual(parse_model_complexity("1"), 1)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_model_complexity("2")

    def test_parse_low_latency_capture_options(self):
        self.assertEqual(parse_timeout_ms("1000"), 1000)
        self.assertEqual(
            parse_ffmpeg_capture_options(" rtsp_transport;tcp "),
            "rtsp_transport;tcp",
        )
        self.assertEqual(parse_ffmpeg_capture_options("none"), "")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_timeout_ms("70000")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_ffmpeg_capture_options(" ")

    def test_build_parser_accepts_gesture_every(self):
        args = build_parser().parse_args(
            [
                "--gesture-every",
                "0.1",
                "--camera-width",
                "640",
                "--camera-height",
                "480",
                "--camera-fps",
                "30",
                "--camera-fourcc",
                "MJPG",
                "--camera-source",
                "rtsp://127.0.0.1:8554/cam0",
                "--interval",
                "0",
                "--camera-backend",
                "ffmpeg-pipe",
                "--ffmpeg-path",
                "C:\\Tools\\ffmpeg\\bin\\ffmpeg.exe",
                "--camera-open-timeout-ms",
                "2000",
                "--camera-read-timeout-ms",
                "500",
                "--opencv-ffmpeg-capture-options",
                "rtsp_transport;tcp",
                "--gesture-model-complexity",
                "0",
                "--publish-landmarks",
            ]
        )

        self.assertEqual(args.gesture_every, 0.1)
        self.assertEqual(args.camera_backend, "ffmpeg-pipe")
        self.assertEqual(args.ffmpeg_path, "C:\\Tools\\ffmpeg\\bin\\ffmpeg.exe")
        self.assertEqual(args.camera_width, 640)
        self.assertEqual(args.camera_height, 480)
        self.assertEqual(args.camera_fps, 30.0)
        self.assertEqual(args.camera_fourcc, "MJPG")
        self.assertEqual(args.camera_source, "rtsp://127.0.0.1:8554/cam0")
        self.assertEqual(args.interval, 0.0)
        self.assertEqual(args.camera_open_timeout_ms, 2000)
        self.assertEqual(args.camera_read_timeout_ms, 500)
        self.assertEqual(args.opencv_ffmpeg_capture_options, "rtsp_transport;tcp")
        self.assertEqual(args.gesture_model_complexity, 0)
        self.assertTrue(args.publish_landmarks)

    def test_build_parser_defaults_to_low_latency_rtsp_options(self):
        args = build_parser().parse_args([])

        self.assertIn("rtsp_transport;tcp", args.opencv_ffmpeg_capture_options)
        self.assertIn("fflags;nobuffer", args.opencv_ffmpeg_capture_options)
        self.assertIn("reorder_queue_size;0", args.opencv_ffmpeg_capture_options)
        self.assertEqual(args.camera_read_timeout_ms, 3000)
        self.assertEqual(args.ffmpeg_path, "ffmpeg")
        self.assertEqual(args.max_message_bytes, 4096)
        self.assertEqual(args.max_queue, 4)

    def test_ffmpeg_pipe_capture_requires_dimensions(self):
        with self.assertRaises(ValueError):
            FfmpegPipeCapture(
                "rtsp://127.0.0.1:8554/cam0",
                width=0,
                height=480,
                fps=30,
                ffmpeg_path="ffmpeg",
            )

    def test_compressed_image_binary_payload_describes_binary_jpeg(self):
        payload = compressed_image_binary_payload(byte_length=1234)

        self.assertEqual(payload["type"], "compressed_image")
        self.assertEqual(payload["format"], "jpeg")
        self.assertEqual(payload["transport"], "binary")
        self.assertEqual(payload["byte_length"], 1234)

    def test_normalized_landmarks_payload_serializes_points(self):
        class Landmark:
            def __init__(self, x, y, z):
                self.x = x
                self.y = y
                self.z = z

        class HandLandmarks:
            landmark = [Landmark(0.1234567, 0.5, -0.25)]

        payload = normalized_landmarks_payload(HandLandmarks())

        self.assertEqual(
            payload,
            [{"x": 0.123457, "y": 0.5, "z": -0.25}],
        )

    def test_resolve_auth_token_trims_empty_values(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"CAMERA_HUB_WS_TOKEN": " secret "}, clear=True):
            self.assertEqual(resolve_auth_token("CAMERA_HUB_WS_TOKEN"), "secret")
        with mock.patch.dict(os.environ, {"CAMERA_HUB_WS_TOKEN": " "}, clear=True):
            self.assertIsNone(resolve_auth_token("CAMERA_HUB_WS_TOKEN"))

    def test_camera_status_payload_reports_sword_sign_processor(self):
        payload = camera_status_payload(
            camera_index=0,
            frame_number=12,
            fps=29.97,
        )

        self.assertEqual(payload["type"], "camera_status")
        self.assertEqual(payload["camera"]["selected_index"], 0)
        self.assertTrue(payload["camera"]["frame_read_ok"])
        self.assertTrue(payload["processors"]["sword_sign"]["enabled"])
        self.assertNotIn("room_light", payload["processors"])

    def test_camera_status_payload_can_include_capture_properties(self):
        payload = camera_status_payload(
            camera_index=0,
            frame_number=12,
            fps=29.97,
            capture={"width": 640, "height": 480, "fourcc": "MJPG"},
            camera_source="rtsp://user:pass@example.test:8554/cam0",
        )

        self.assertEqual(payload["capture"]["width"], 640)
        self.assertEqual(payload["capture"]["fourcc"], "MJPG")
        self.assertEqual(
            payload["camera"]["source"],
            "rtsp://<redacted>@example.test:8554/cam0",
        )

    def test_camera_status_payload_can_report_frame_read_failure(self):
        payload = camera_status_payload(
            camera_index=0,
            frame_number=0,
            fps=0.0,
            frame_read_ok=False,
        )

        self.assertFalse(payload["camera"]["frame_read_ok"])

    def test_fourcc_to_text_decodes_printable_codes(self):
        import cv2

        self.assertEqual(fourcc_to_text(cv2.VideoWriter_fourcc(*"MJPG")), "MJPG")
        self.assertEqual(fourcc_to_text(0), "")

    def test_redact_camera_source_hides_credentials(self):
        self.assertEqual(
            redact_camera_source("rtsp://user:secret@127.0.0.1:8554/cam0"),
            "rtsp://<redacted>@127.0.0.1:8554/cam0",
        )
        self.assertEqual(
            redact_camera_source("rtsp://127.0.0.1:8554/cam0"),
            "rtsp://127.0.0.1:8554/cam0",
        )
        self.assertEqual(redact_camera_source("0"), "0")

    def test_safe_runtime_error_does_not_report_paths_or_model_details(self):
        self.assertEqual(
            safe_runtime_error(FileNotFoundError("executable not found: C:\\Secret\\ffmpeg.exe")),
            "executable_not_found",
        )
        self.assertEqual(
            safe_runtime_error(FileNotFoundError("C:\\Secret\\model.joblib")),
            "model_not_found",
        )

    def test_due_respects_disabled_and_elapsed_interval(self):
        self.assertFalse(due(None, 0.0, 10.0))
        self.assertTrue(due(None, 1.0, 10.0))
        self.assertFalse(due(9.5, 1.0, 10.0))
        self.assertTrue(due(8.9, 1.0, 10.0))


if __name__ == "__main__":
    unittest.main()
