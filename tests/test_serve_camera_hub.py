import argparse
import math
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from apps.serve_camera_hub import (
    build_parser,
    CameraFrameSnapshot,
    camera_status_payload,
    capture_status_properties,
    compressed_image_binary_payload,
    copy_processor_metrics,
    due,
    FfmpegPipeCapture,
    LatestFrameCamera,
    looks_like_local_file_source,
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
    parse_replay_video_path,
    parse_threshold,
    parse_tool_path,
    parse_timeout_ms,
    redact_camera_source,
    resolve_auth_token,
    safe_runtime_error,
    VideoReplayCapture,
)


class ServeCameraHubTests(unittest.TestCase):
    def test_retired_direct_websocket_entrypoint_is_absent(self):
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "apps" / "serve_websocket.py").exists())
        for relative_path in (
            "README.md",
            "docs/retired-paths.md",
            "docs/module-responsibilities.md",
        ):
            self.assertNotIn(
                "serve_websocket.py",
                (root / relative_path).read_text(encoding="utf-8"),
            )

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
        self.assertEqual(parse_camera_backend(" replay-video "), "replay-video")
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

    def test_parse_replay_video_path_requires_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "sample.mp4"
            video.write_bytes(b"local test video")

            self.assertEqual(parse_replay_video_path(str(video)), str(video.resolve()))
            with self.assertRaises(argparse.ArgumentTypeError):
                parse_replay_video_path(str(Path(directory) / "missing.mp4"))
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_replay_video_path(" ")

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

    def test_build_parser_accepts_replay_video_options(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "sample.mp4"
            video.write_bytes(b"local test video")

            args = build_parser().parse_args(
                [
                    "--replay-video",
                    str(video),
                    "--no-replay-loop",
                    "--replay-fps",
                    "12.5",
                    "--interval",
                    "0.08",
                ]
            )

        self.assertEqual(args.replay_video, str(video.resolve()))
        self.assertFalse(args.replay_loop)
        self.assertEqual(args.replay_fps, 12.5)
        self.assertEqual(args.interval, 0.08)

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

    def test_ffmpeg_pipe_capture_bounds_rtsp_read_wait(self):
        process = mock.Mock()
        process.poll.return_value = 0
        process.stdout = mock.Mock()
        with mock.patch(
            "apps.serve_camera_hub.resolve_executable",
            return_value="ffmpeg",
        ), mock.patch(
            "apps.serve_camera_hub.subprocess.Popen",
            return_value=process,
        ) as popen:
            capture = FfmpegPipeCapture(
                "rtsp://127.0.0.1:8554/cam0",
                width=640,
                height=480,
                fps=30,
                ffmpeg_path="ffmpeg",
                read_timeout_ms=750,
            )
            capture.release()

        command = popen.call_args.args[0]
        timeout_index = command.index("-rw_timeout")
        self.assertEqual(command[timeout_index + 1], "750000")
        self.assertLess(timeout_index, command.index("-i"))

    def test_latest_frame_camera_reopens_and_clears_stale_frame(self):
        class FakeCapture:
            def __init__(self, reads):
                self.reads = iter(reads)
                self.last = (False, None)
                self.released = False

            def isOpened(self):
                return not self.released

            def read(self):
                try:
                    self.last = next(self.reads)
                except StopIteration:
                    pass
                return self.last

            def get(self, _prop_id):
                return 0.0

            def set(self, _prop_id, _value):
                return True

            def release(self):
                self.released = True

        first = FakeCapture(
            [
                (True, "frame-before-disconnect"),
                (False, None),
                (False, None),
                (False, None),
            ]
        )
        second = FakeCapture([(True, "frame-after-reconnect")])
        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            side_effect=[first, second],
        ):
            camera = LatestFrameCamera(0, camera_index=0, interval=0.001)
            camera._reconnect_initial_delay = 0.0
            camera._reconnect_max_delay = 0.0
            camera.start()
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                snapshot = camera.snapshot(copy_frame=False)
                if snapshot.frame == "frame-after-reconnect":
                    break
                time.sleep(0.005)
            camera.stop()

        snapshot = camera.snapshot(copy_frame=False)
        self.assertEqual(snapshot.frame, "frame-after-reconnect")
        self.assertTrue(first.released)
        self.assertTrue(second.released)
        self.assertEqual(camera.actual_properties()["reconnect_attempts"], 1)

    def test_latest_frame_camera_starts_degraded_and_reopens_initial_capture(self):
        class FakeCapture:
            def __init__(self, *, opened, reads=()):
                self.opened = opened
                self.reads = iter(reads)
                self.released = False

            def isOpened(self):
                return self.opened and not self.released

            def read(self):
                try:
                    return next(self.reads)
                except StopIteration:
                    return False, None

            def get(self, _prop_id):
                return 0.0

            def set(self, _prop_id, _value):
                return True

            def release(self):
                self.released = True

        initial = FakeCapture(opened=False)
        recovered = FakeCapture(
            opened=True,
            reads=[(True, "recovered-frame")] * 5,
        )
        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            side_effect=[initial, recovered],
        ):
            camera = LatestFrameCamera(0, camera_index=0, interval=0.001)
            camera._reconnect_after_failures = 1
            camera._reconnect_initial_delay = 0.0
            camera._reconnect_max_delay = 0.0
            camera.start()
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if camera.snapshot(copy_frame=False).frame == "recovered-frame":
                    break
                time.sleep(0.005)
            camera.stop()

        self.assertEqual(
            camera.snapshot(copy_frame=False).frame,
            "recovered-frame",
        )
        self.assertTrue(initial.released)
        self.assertTrue(recovered.released)
        self.assertEqual(camera.actual_properties()["reconnect_attempts"], 1)

    def test_latest_frame_camera_active_success_without_frame_reconnects(self):
        class FakeCapture:
            def __init__(self, reads):
                self.reads = iter(reads)
                self.last = (False, None)
                self.release_calls = 0

            def isOpened(self):
                return self.release_calls == 0

            def read(self):
                try:
                    self.last = next(self.reads)
                except StopIteration:
                    pass
                return self.last

            def get(self, _property):
                return 0.0

            def set(self, _property, _value):
                return True

            def release(self):
                self.release_calls += 1

        initial = FakeCapture([(True, None)])
        recovered = FakeCapture([(True, "valid-frame")] * 3)
        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            side_effect=[initial, recovered],
        ):
            camera = LatestFrameCamera(0, camera_index=0, interval=0.001)
            camera._reconnect_after_failures = 1
            camera._reconnect_initial_delay = 0.0
            camera._reconnect_max_delay = 0.0
            camera.start()
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if camera.snapshot(copy_frame=False).frame == "valid-frame":
                    break
                time.sleep(0.005)
            camera.stop()

        self.assertEqual(camera.snapshot(copy_frame=False).frame, "valid-frame")
        self.assertEqual(initial.release_calls, 1)
        self.assertEqual(recovered.release_calls, 1)

    def test_latest_frame_camera_reconnect_requires_a_real_first_frame(self):
        class FakeCapture:
            def __init__(self, *, opened=True, reads=()):
                self.opened = opened
                self.reads = iter(reads)
                self.released = False

            def isOpened(self):
                return self.opened and not self.released

            def read(self):
                try:
                    return next(self.reads)
                except StopIteration:
                    return False, None

            def get(self, _prop_id):
                return 0.0

            def set(self, _prop_id, _value):
                return True

            def release(self):
                self.released = True

        initial = FakeCapture()
        opened_without_frame = FakeCapture(reads=[(False, None)])
        recovered = FakeCapture(reads=[(True, "first-recovered-frame")])
        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            side_effect=[initial, opened_without_frame, recovered],
        ):
            camera = LatestFrameCamera(0, camera_index=0, interval=0.001)
            camera._reconnect_initial_delay = 0.0
            camera._reconnect_max_delay = 0.0
            self.assertTrue(camera._reconnect_until_open())

        snapshot = camera.snapshot(copy_frame=False)
        self.assertEqual(snapshot.frame, "first-recovered-frame")
        self.assertTrue(snapshot.frame_read_ok)
        self.assertTrue(opened_without_frame.released)
        self.assertIs(camera.cap, recovered)
        self.assertEqual(camera.actual_properties()["reconnect_attempts"], 2)
        camera.stop()
        self.assertTrue(recovered.released)

    def test_latest_frame_camera_reconnect_rejects_success_without_a_frame(self):
        class FakeCapture:
            def __init__(self, reads=()):
                self.reads = iter(reads)
                self.released = False

            def isOpened(self):
                return not self.released

            def read(self):
                return next(self.reads, (False, None))

            def get(self, _property):
                return 0.0

            def set(self, _property, _value):
                return True

            def release(self):
                self.released = True

        initial = FakeCapture()
        missing_frame = FakeCapture(reads=[(True, None)])
        recovered = FakeCapture(reads=[(True, "valid-frame")])
        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            side_effect=[initial, missing_frame, recovered],
        ):
            camera = LatestFrameCamera(0, camera_index=0, interval=0.001)
            camera._reconnect_initial_delay = 0.0
            camera._reconnect_max_delay = 0.0
            self.assertTrue(camera._reconnect_until_open())

        snapshot = camera.snapshot(copy_frame=False)
        self.assertEqual(snapshot.frame, "valid-frame")
        self.assertTrue(snapshot.frame_read_ok)
        self.assertTrue(missing_frame.released)
        self.assertIs(camera.cap, recovered)
        camera.stop()

    def test_latest_frame_camera_reconnect_releases_configuration_failure(self):
        class FakeCapture:
            def __init__(self, reads=()):
                self.reads = iter(reads)
                self.released = False

            def isOpened(self):
                return not self.released

            def read(self):
                return next(self.reads, (False, None))

            def get(self, _property):
                return 0.0

            def set(self, _property, _value):
                return True

            def release(self):
                self.released = True

        initial = FakeCapture()
        configuration_failure = FakeCapture()
        recovered = FakeCapture(reads=[(True, "valid-frame")])
        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            side_effect=[initial, configuration_failure, recovered],
        ):
            camera = LatestFrameCamera(0, camera_index=0, interval=0.001)
            camera._reconnect_initial_delay = 0.0
            camera._reconnect_max_delay = 0.0
            with mock.patch.object(
                camera,
                "_configure_capture",
                side_effect=[RuntimeError("configuration failed"), None],
            ):
                self.assertTrue(camera._reconnect_until_open())

        self.assertTrue(configuration_failure.released)
        self.assertIs(camera.cap, recovered)
        camera.stop()

    def test_latest_frame_camera_initial_configuration_failure_releases_capture(self):
        capture = mock.Mock()
        capture.set.side_effect = RuntimeError("configuration failed")
        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            return_value=capture,
        ):
            with self.assertRaisesRegex(RuntimeError, "configuration failed"):
                LatestFrameCamera(
                    0,
                    camera_index=0,
                    interval=0.001,
                    width=640,
                )

        capture.release.assert_called_once_with()

    def test_latest_frame_camera_reconnect_releases_closed_and_raising_candidates(self):
        class FakeCapture:
            def __init__(self, *, opened=True, open_error=None, reads=()):
                self.opened = opened
                self.open_error = open_error
                self.reads = iter(reads)
                self.release_calls = 0

            def isOpened(self):
                if self.open_error is not None:
                    raise self.open_error
                return self.opened and self.release_calls == 0

            def read(self):
                return next(self.reads, (False, None))

            def get(self, _property):
                return 0.0

            def set(self, _property, _value):
                return True

            def release(self):
                self.release_calls += 1

        initial = FakeCapture()
        closed = FakeCapture(opened=False)
        raising = FakeCapture(open_error=RuntimeError("open state unavailable"))
        recovered = FakeCapture(reads=[(True, "valid-frame")])
        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            side_effect=[initial, closed, raising, recovered],
        ):
            camera = LatestFrameCamera(0, camera_index=0, interval=0.001)
            camera._reconnect_initial_delay = 0.0
            camera._reconnect_max_delay = 0.0
            self.assertTrue(camera._reconnect_until_open())

        self.assertEqual(initial.release_calls, 1)
        self.assertEqual(closed.release_calls, 1)
        self.assertEqual(raising.release_calls, 1)
        self.assertIs(camera.cap, recovered)
        camera.stop()
        self.assertEqual(recovered.release_calls, 1)

    def test_latest_frame_camera_stop_before_pending_claim_releases_returned_candidate(self):
        open_started = threading.Event()
        allow_open_return = threading.Event()

        class FakeCapture:
            def __init__(self):
                self.release_calls = 0

            def isOpened(self):
                return self.release_calls == 0

            def read(self):
                return False, None

            def get(self, _property):
                return 0.0

            def set(self, _property, _value):
                return True

            def release(self):
                self.release_calls += 1

        initial = FakeCapture()
        candidate = FakeCapture()
        open_count = 0

        def open_capture(*_args, **_kwargs):
            nonlocal open_count
            open_count += 1
            if open_count == 1:
                return initial
            open_started.set()
            allow_open_return.wait(timeout=1.0)
            return candidate

        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            side_effect=open_capture,
        ):
            camera = LatestFrameCamera(0, camera_index=0, interval=0.001)
            camera._reconnect_initial_delay = 0.0
            result = []
            worker = threading.Thread(
                target=lambda: result.append(camera._reconnect_until_open())
            )
            worker.start()
            self.assertTrue(open_started.wait(timeout=1.0))
            camera.stop()
            allow_open_return.set()
            worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [False])
        self.assertIsNone(camera.cap)
        self.assertEqual(initial.release_calls, 1)
        self.assertEqual(candidate.release_calls, 1)

    def test_latest_frame_camera_stop_during_configuration_releases_pending_once(self):
        configure_started = threading.Event()
        allow_configuration = threading.Event()

        class FakeCapture:
            def __init__(self):
                self.release_calls = 0

            def isOpened(self):
                return self.release_calls == 0

            def read(self):
                return False, None

            def get(self, _property):
                return 0.0

            def set(self, _property, _value):
                return True

            def release(self):
                self.release_calls += 1

        initial = FakeCapture()
        candidate = FakeCapture()
        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            side_effect=[initial, candidate],
        ):
            camera = LatestFrameCamera(0, camera_index=0, interval=0.001)
            camera._reconnect_initial_delay = 0.0

            def blocked_configuration(*_args, **_kwargs):
                configure_started.set()
                allow_configuration.wait(timeout=1.0)

            result = []
            with mock.patch.object(
                camera,
                "_configure_capture",
                side_effect=blocked_configuration,
            ):
                worker = threading.Thread(
                    target=lambda: result.append(camera._reconnect_until_open())
                )
                worker.start()
                self.assertTrue(configure_started.wait(timeout=1.0))
                camera.stop()
                allow_configuration.set()
                worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [False])
        self.assertIsNone(camera.cap)
        self.assertEqual(initial.release_calls, 1)
        self.assertEqual(candidate.release_calls, 1)

    def test_latest_frame_camera_promotes_pending_and_stop_is_idempotent(self):
        class FakeCapture:
            def __init__(self, reads=()):
                self.reads = iter(reads)
                self.release_calls = 0

            def isOpened(self):
                return self.release_calls == 0

            def read(self):
                return next(self.reads, (False, None))

            def get(self, _property):
                return 0.0

            def set(self, _property, _value):
                return True

            def release(self):
                self.release_calls += 1

        initial = FakeCapture()
        recovered = FakeCapture(reads=[(True, "valid-frame")])
        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            side_effect=[initial, recovered],
        ):
            camera = LatestFrameCamera(0, camera_index=0, interval=0.001)
            camera._reconnect_initial_delay = 0.0
            self.assertTrue(camera._reconnect_until_open())
            self.assertIs(camera.cap, recovered)
            self.assertIsNone(camera._pending_capture)
            camera.stop()
            camera.stop()
            with self.assertRaisesRegex(RuntimeError, "not startable"):
                camera.start()

        self.assertEqual(initial.release_calls, 1)
        self.assertEqual(recovered.release_calls, 1)

    def test_latest_frame_camera_stop_releases_blocked_first_read(self):
        first_read_started = threading.Event()
        first_read_unblocked = threading.Event()

        class FakeCapture:
            def __init__(self, *, block_read=False):
                self.block_read = block_read
                self.release_calls = 0

            def isOpened(self):
                return self.release_calls == 0

            def read(self):
                if self.block_read:
                    first_read_started.set()
                    first_read_unblocked.wait(timeout=1.0)
                return False, None

            def get(self, _property):
                return 0.0

            def set(self, _property, _value):
                return True

            def release(self):
                self.release_calls += 1
                if self.block_read:
                    first_read_unblocked.set()

        initial = FakeCapture()
        candidate = FakeCapture(block_read=True)
        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            side_effect=[initial, candidate],
        ):
            camera = LatestFrameCamera(0, camera_index=0, interval=0.001)
            camera._reconnect_initial_delay = 0.0
            camera._reconnect_max_delay = 0.0
            result = []
            worker = threading.Thread(
                target=lambda: result.append(camera._reconnect_until_open())
            )
            worker.start()
            self.assertTrue(first_read_started.wait(timeout=1.0))
            camera.stop()
            worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [False])
        self.assertEqual(initial.release_calls, 1)
        self.assertEqual(candidate.release_calls, 1)

    def test_latest_frame_camera_stop_discards_late_active_read(self):
        read_started = threading.Event()
        read_unblocked = threading.Event()

        class FakeCapture:
            def __init__(self):
                self.release_calls = 0

            def isOpened(self):
                return self.release_calls == 0

            def read(self):
                read_started.set()
                read_unblocked.wait(timeout=1.0)
                return True, "late-frame"

            def get(self, _property):
                return 0.0

            def set(self, _property, _value):
                return True

            def release(self):
                self.release_calls += 1
                read_unblocked.set()

        capture = FakeCapture()
        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            return_value=capture,
        ):
            camera = LatestFrameCamera(0, camera_index=0, interval=0.001)
            camera.start()
            self.assertTrue(read_started.wait(timeout=1.0))
            camera.stop()

        snapshot = camera.snapshot(copy_frame=False)
        self.assertFalse(camera.is_opened())
        self.assertFalse(snapshot.frame_read_ok)
        self.assertIsNone(snapshot.frame)
        self.assertEqual(capture.release_calls, 1)

    def test_latest_frame_camera_stop_releases_candidate_opened_during_reconnect(self):
        candidate_opened = threading.Event()
        allow_candidate_result = threading.Event()

        class FakeCapture:
            def __init__(self, *, block_on_open=False):
                self.block_on_open = block_on_open
                self.release_calls = 0

            def isOpened(self):
                if self.block_on_open:
                    candidate_opened.set()
                    allow_candidate_result.wait(timeout=1.0)
                return True

            def release(self):
                self.release_calls += 1

            def get(self, _property):
                return 0.0

            def set(self, _property, _value):
                return True

        initial = FakeCapture()
        candidate = FakeCapture(block_on_open=True)
        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            side_effect=[initial, candidate],
        ):
            camera = LatestFrameCamera(0, camera_index=0, interval=0.001)
            camera._reconnect_initial_delay = 0.0
            camera._reconnect_max_delay = 0.0
            result = []
            worker = threading.Thread(
                target=lambda: result.append(camera._reconnect_until_open())
            )
            worker.start()
            self.assertTrue(candidate_opened.wait(timeout=1.0))
            camera.stop()
            allow_candidate_result.set()
            worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [False])
        self.assertIsNone(camera.cap)
        self.assertEqual(initial.release_calls, 1)
        self.assertEqual(candidate.release_calls, 1)

    def test_latest_frame_camera_closed_replay_source_fails_closed(self):
        capture = mock.Mock()
        capture.isOpened.return_value = False
        capture.get.return_value = 0.0
        capture.set.return_value = True
        capture.metadata = None
        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            return_value=capture,
        ):
            camera = LatestFrameCamera(
                "fixture.mp4",
                camera_index=0,
                interval=0.0,
                backend="replay-video",
                replay_loop=False,
            )
            with self.assertRaisesRegex(RuntimeError, "camera not available"):
                camera.start()

        self.assertIsNone(camera._thread)

    def test_non_looping_replay_eof_does_not_become_implicit_reconnect_loop(self):
        capture = mock.Mock()
        capture.isOpened.return_value = True
        capture.read.return_value = (False, None)
        capture.get.return_value = 0.0
        capture.set.return_value = True
        capture.metadata = None
        with mock.patch(
            "apps.serve_camera_hub.open_video_capture",
            return_value=capture,
        ) as opener:
            camera = LatestFrameCamera(
                "fixture.mp4",
                camera_index=0,
                interval=0.0,
                backend="replay-video",
                replay_loop=False,
            )
            camera.start()
            time.sleep(0.12)
            camera.stop()

        opener.assert_called_once()
        self.assertEqual(camera.actual_properties()["reconnect_attempts"], 0)

    def test_video_replay_capture_loops_after_eof(self):
        class FakeCapture:
            def __init__(self):
                self.frames = ["frame-0", "frame-1"]
                self.index = 0
                self.released = False

            def isOpened(self):
                return True

            def read(self):
                if self.index >= len(self.frames):
                    return False, None
                frame = self.frames[self.index]
                self.index += 1
                return True, frame

            def get(self, _prop_id):
                return 0.0

            def set(self, _prop_id, value):
                self.index = int(value)
                return True

            def release(self):
                self.released = True

        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "sample.mp4"
            video.write_bytes(b"local test video")
            fake_capture = FakeCapture()
            with mock.patch(
                "apps.serve_camera_hub.cv2.VideoCapture",
                return_value=fake_capture,
            ):
                replay = VideoReplayCapture(str(video), loop=True)

                self.assertEqual(replay.read(), (True, "frame-0"))
                self.assertEqual(replay.read(), (True, "frame-1"))
                self.assertEqual(replay.read(), (True, "frame-0"))
                self.assertEqual(replay.loop_count, 1)
                self.assertEqual(replay.metadata()["mode"], "replay-video")
                self.assertEqual(replay.metadata()["source"], "local-file:sample.mp4")
                replay.release()

            self.assertTrue(fake_capture.released)

    def test_compressed_image_binary_payload_describes_binary_jpeg(self):
        payload = compressed_image_binary_payload(byte_length=1234)

        self.assertEqual(payload["type"], "compressed_image")
        self.assertEqual(payload["format"], "jpeg")
        self.assertEqual(payload["transport"], "binary")
        self.assertEqual(payload["byte_length"], 1234)

    def test_camera_status_payload_accepts_processor_metrics(self):
        payload = camera_status_payload(
            camera_index=0,
            frame_number=42,
            fps=29.97,
            processors={
                "sword_sign": {
                    "enabled": True,
                    "last_frame_id": 42,
                    "inference_ms": 12.345,
                    "publish_age_ms": 23.456,
                }
            },
        )

        sword = payload["processors"]["sword_sign"]
        self.assertEqual(sword["last_frame_id"], 42)
        self.assertAlmostEqual(sword["inference_ms"], 12.345)
        self.assertAlmostEqual(sword["publish_age_ms"], 23.456)

    def test_capture_status_properties_adds_latency_diagnostics(self):
        class Camera:
            def actual_properties(self):
                return {"backend": "ffmpeg-pipe", "width": 640, "height": 480}

        snapshot = CameraFrameSnapshot(
            frame=None,
            frame_number=10,
            stamp=100.0,
            fps=30.0,
            frame_read_ok=True,
            read_latency_ms=4.25,
            read_failures=2,
        )

        capture = capture_status_properties(Camera(), snapshot, now=100.125)

        self.assertEqual(capture["backend"], "ffmpeg-pipe")
        self.assertEqual(capture["frame_age_ms"], 125.0)
        self.assertEqual(capture["read_latency_ms"], 4.25)
        self.assertEqual(capture["read_failures"], 2)
        self.assertEqual(capture["read_fps"], 30.0)

    def test_capture_status_properties_sanitizes_non_finite_diagnostics(self):
        class Camera:
            def actual_properties(self):
                return {"backend": "ffmpeg-pipe"}

        snapshot = CameraFrameSnapshot(
            frame=None,
            frame_number=10,
            stamp=math.nan,
            fps=math.inf,
            frame_read_ok=True,
            read_latency_ms=math.nan,
            read_failures=math.inf,
        )

        capture = capture_status_properties(Camera(), snapshot, now=100.0)

        self.assertEqual(capture["frame_age_ms"], 0.0)
        self.assertEqual(capture["read_latency_ms"], 0.0)
        self.assertEqual(capture["read_failures"], 0)
        self.assertEqual(capture["read_fps"], 0.0)

    def test_copy_processor_metrics_returns_independent_dicts(self):
        metrics = {"sword_sign": {"enabled": True, "inference_ms": 1.0}}

        copied = copy_processor_metrics(metrics)
        copied["sword_sign"]["inference_ms"] = 2.0

        self.assertEqual(metrics["sword_sign"]["inference_ms"], 1.0)

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

    def test_camera_status_payload_reports_reconnecting_without_stopping_hub(self):
        payload = camera_status_payload(
            camera_index=0,
            frame_number=12,
            fps=0.0,
            frame_read_ok=False,
            camera_opened=False,
        )

        self.assertFalse(payload["camera"]["opened"])
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
        self.assertTrue(looks_like_local_file_source("hand_movie.mp4"))
        self.assertEqual(
            redact_camera_source(r"C:\Users\kawai\works\hand_movie.mp4"),
            "local-file:hand_movie.mp4",
        )

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
