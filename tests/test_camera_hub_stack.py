import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path


def load_stack_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "camera_hub_stack.py"
    spec = importlib.util.spec_from_file_location("camera_hub_stack", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


stack = load_stack_module()


class CameraHubStackTests(unittest.TestCase):
    def test_build_ffmpeg_args_uses_dshow_camera_name(self):
        args = stack.build_ffmpeg_args(
            ffmpeg="ffmpeg",
            camera_name="HD Pro Webcam C920",
            width=640,
            height=480,
            fps=30,
            bitrate="800k",
            gop=30,
            rtsp_url="rtsp://127.0.0.1:8554/cam0",
        )

        self.assertIn("video=HD Pro Webcam C920", args)
        self.assertIn("-rtsp_transport", args)
        self.assertIn("-g", args)
        self.assertIn("-keyint_min", args)
        self.assertEqual(args[-1], "rtsp://127.0.0.1:8554/cam0")

    def test_default_opencv_ffmpeg_options_are_tcp_only(self):
        self.assertIn("rtsp_transport;tcp", stack.DEFAULT_OPENCV_FFMPEG_OPTIONS)
        self.assertIn("fflags;nobuffer", stack.DEFAULT_OPENCV_FFMPEG_OPTIONS)
        self.assertIn("reorder_queue_size;0", stack.DEFAULT_OPENCV_FFMPEG_OPTIONS)

    def test_build_ffprobe_args_limits_live_rtsp_probe(self):
        args = stack.build_ffprobe_args(
            "ffprobe",
            "rtsp://127.0.0.1:8554/cam0",
        )

        self.assertIn("-analyzeduration", args)
        self.assertIn("-probesize", args)
        self.assertIn("-select_streams", args)
        self.assertEqual(args[-1], "rtsp://127.0.0.1:8554/cam0")

    def test_probe_rtsp_once_treats_timeout_with_video_output_as_ready(self):
        original_run = stack.subprocess.run

        def fake_run(*args, **kwargs):
            raise stack.subprocess.TimeoutExpired(
                cmd=["ffprobe"],
                timeout=5,
                output=b"codec_type=video\nwidth=640\nheight=480\n",
            )

        try:
            stack.subprocess.run = fake_run
            ready, detail = stack.probe_rtsp_once(["ffprobe"], timeout_seconds=5)
        finally:
            stack.subprocess.run = original_run

        self.assertTrue(ready)
        self.assertIn("codec_type=video", detail)

    def test_probe_rtsp_once_retries_plain_timeout(self):
        original_run = stack.subprocess.run

        def fake_run(*args, **kwargs):
            raise stack.subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=5)

        try:
            stack.subprocess.run = fake_run
            ready, detail = stack.probe_rtsp_once(["ffprobe"], timeout_seconds=5)
        finally:
            stack.subprocess.run = original_run

        self.assertFalse(ready)
        self.assertIn("timed out", detail)

    def test_build_hub_args_enables_landmarks_and_low_latency_options(self):
        args = stack.build_hub_args(
            uv="uv",
            host="127.0.0.1",
            port=8765,
            rtsp_url="rtsp://127.0.0.1:8554/cam0",
            frame_id="cam0",
            publish_jpeg_every=0.0,
            gesture_every=0.05,
            gesture_model_complexity=0,
            release_grace_seconds=0.03,
            camera_backend="ffmpeg-pipe",
            opencv_ffmpeg_capture_options="rtsp_transport;tcp",
            camera_open_timeout_ms=5000,
            camera_read_timeout_ms=3000,
            capture_interval=0.0,
            width=640,
            height=480,
            fps=30,
            ffmpeg_path="ffmpeg",
            max_clients=12,
        )

        self.assertIn("--publish-landmarks", args)
        self.assertIn("--interval", args)
        self.assertIn("--camera-backend", args)
        self.assertIn("ffmpeg-pipe", args)
        self.assertIn("--camera-width", args)
        self.assertIn("--camera-height", args)
        self.assertIn("--camera-fps", args)
        self.assertIn("--ffmpeg-path", args)
        self.assertIn("--max-clients", args)
        self.assertIn("12", args)
        self.assertIn("--opencv-ffmpeg-capture-options", args)
        self.assertIn("rtsp_transport;tcp", args)
        self.assertIn("5000", args)
        self.assertIn("3000", args)
        self.assertEqual(args[0:3], ["uv", "run", "python"])

    def test_parser_defaults_to_one_terminal_browser_debug_stack(self):
        args = stack.build_parser().parse_args([])

        self.assertEqual(args.camera_name, "HD Pro Webcam C920")
        self.assertEqual(args.frame_id, "cam0")
        self.assertEqual(args.publish_jpeg_every, 0.0)
        self.assertEqual(args.capture_interval, 0.0)
        self.assertEqual(args.gop, 30)
        self.assertEqual(args.hub_camera_backend, "ffmpeg-pipe")
        self.assertEqual(args.camera_open_timeout_ms, 5000)
        self.assertEqual(args.camera_read_timeout_ms, 3000)
        self.assertEqual(args.max_clients, 8)
        self.assertEqual(args.viewer_host, "127.0.0.1")
        self.assertEqual(args.viewer_port, 8770)
        self.assertFalse(args.no_viewer_server)
        self.assertFalse(args.force_stop_existing)
        self.assertFalse(args.no_browser)

    def test_connect_host_maps_wildcard_to_localhost(self):
        self.assertEqual(stack.connect_host("0.0.0.0"), "127.0.0.1")
        self.assertEqual(stack.connect_host("127.0.0.1"), "127.0.0.1")

    def test_mediamtx_webrtc_url_uses_rtsp_path_for_browser_video(self):
        url = stack.mediamtx_webrtc_url("rtsp://127.0.0.1:8554/cam0")

        self.assertEqual(
            url,
            "http://127.0.0.1:8889/cam0?controls=false&muted=true&autoplay=true",
        )

    def test_browser_monitor_url_passes_media_and_websocket_urls(self):
        url = stack.browser_monitor_url(
            "http://127.0.0.1:8770/browser_camera_hub_viewer.html",
            media_url="http://127.0.0.1:8889/cam0?controls=false",
            ws_url="ws://127.0.0.1:8765",
        )

        self.assertIn(
            "http://127.0.0.1:8770/browser_camera_hub_viewer.html?",
            url,
        )
        self.assertIn("mediaUrl=http%3A%2F%2F127.0.0.1%3A8889%2Fcam0", url)
        self.assertIn("wsUrl=ws%3A%2F%2F127.0.0.1%3A8765", url)

    def test_viewer_server_helpers_build_http_routes(self):
        self.assertEqual(
            stack.viewer_server_page_url("127.0.0.1", 8770),
            "http://127.0.0.1:8770/browser_camera_hub_viewer.html",
        )
        self.assertEqual(
            stack.viewer_server_health_url("0.0.0.0", 8770),
            "http://127.0.0.1:8770/healthz",
        )

    def test_build_viewer_server_args_uses_separate_static_server(self):
        args = stack.build_viewer_server_args(
            uv="uv",
            host="127.0.0.1",
            port=8770,
            viewer_path=Path("apps/browser_camera_hub_viewer.html"),
            allow_remote=False,
        )

        self.assertEqual(
            args[0:4],
            ["uv", "run", "python", "apps/serve_browser_monitor.py"],
        )
        self.assertIn("--viewer-path", args)
        self.assertNotIn("--allow-remote", args)

    def test_stack_ports_include_viewer_server_by_default(self):
        args = stack.build_parser().parse_args([])

        self.assertIn(8770, stack.stack_ports(args))
        args.no_viewer_server = True
        self.assertNotIn(8770, stack.stack_ports(args))

    def test_process_discovery_helper_is_ignored(self):
        process = {
            "pid": 123,
            "command": (
                "powershell -NoProfile -Command "
                "\"Get-CimInstance Win32_Process | Where-Object { "
                f"$_.CommandLine -match '{stack.STACK_PROCESS_PATTERN}' }}\""
            ),
        }

        self.assertTrue(stack.is_process_discovery_helper(process))

    def test_find_existing_stack_processes_ignores_current_family(self):
        original_family = stack.current_process_family_pids
        original_ports = stack.listen_port_owners
        original_matching = stack.list_matching_processes
        original_details = stack.process_details

        try:
            stack.current_process_family_pids = lambda current_pid: {10, 11, 12}
            stack.listen_port_owners = lambda ports: {11: {8765}, 99: {8554}}
            stack.list_matching_processes = lambda: [
                {"pid": 12, "name": "uv.exe", "command": "camera_hub_stack.py"},
                {
                    "pid": 55,
                    "name": "powershell.exe",
                    "command": (
                        "Get-CimInstance Win32_Process "
                        f"{stack.STACK_PROCESS_PATTERN}"
                    ),
                },
                {"pid": 99, "name": "mediamtx.exe", "command": "mediamtx config.yml"},
            ]
            stack.process_details = lambda pids: {}

            processes = stack.find_existing_stack_processes(
                ports=(8554, 8765),
                current_pid=10,
            )
        finally:
            stack.current_process_family_pids = original_family
            stack.listen_port_owners = original_ports
            stack.list_matching_processes = original_matching
            stack.process_details = original_details

        self.assertEqual([process["pid"] for process in processes], [99])

    def test_stack_process_pattern_does_not_match_generic_ffmpeg(self):
        self.assertNotIn("ffmpeg", stack.STACK_PROCESS_PATTERN)

    def test_log_helpers_redact_credentials(self):
        value = stack.quote_for_log("rtsp://user:secret@example.test:8554/cam0")

        self.assertNotIn("secret", value)
        self.assertIn("rtsp://<redacted>@example.test:8554/cam0", value)

    def test_parser_rejects_invalid_runtime_numbers(self):
        invalid_args = [
            ["--width", "0"],
            ["--hub-port", "70000"],
            ["--gesture-model-complexity", "2"],
            ["--release-grace-seconds", "nan"],
        ]

        for args in invalid_args:
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        stack.build_parser().parse_args(args)


if __name__ == "__main__":
    unittest.main()
