import importlib.util
import io
import os
import sys
import unittest
from unittest import mock
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
        rtsp_url = stack.mediamtx_rtsp_url()
        args = stack.build_ffmpeg_args(
            ffmpeg="ffmpeg",
            ffmpeg_video_source="dshow",
            ffmpeg_input_codec="auto",
            camera_name="HD Pro Webcam C920",
            width=640,
            height=480,
            fps=30,
            bitrate="800k",
            gop=30,
            rtsp_url=rtsp_url,
        )

        self.assertIn("video=HD Pro Webcam C920", args)
        self.assertIn("-rtsp_transport", args)
        self.assertIn("-g", args)
        self.assertIn("-keyint_min", args)
        self.assertNotIn("-vcodec", args)
        self.assertEqual(args[-1], rtsp_url)

    def test_build_ffmpeg_args_can_select_advertised_mjpeg_input(self):
        args = stack.build_ffmpeg_args(
            ffmpeg="ffmpeg",
            ffmpeg_video_source="dshow",
            ffmpeg_input_codec="mjpeg",
            camera_name="Logitech StreamCam",
            width=1920,
            height=1080,
            fps=30,
            bitrate="800k",
            gop=30,
            rtsp_url=stack.mediamtx_rtsp_url(),
        )

        self.assertEqual(args[1:5], ["-f", "dshow", "-vcodec", "mjpeg"])
        self.assertLess(args.index("-vcodec"), args.index("-i"))

    def test_build_ffmpeg_args_keeps_testsrc_independent_of_dshow_codec(self):
        args = stack.build_ffmpeg_args(
            ffmpeg="ffmpeg",
            ffmpeg_video_source="testsrc",
            ffmpeg_input_codec="mjpeg",
            camera_name="unused",
            width=640,
            height=480,
            fps=30,
            bitrate="800k",
            gop=30,
            rtsp_url=stack.mediamtx_rtsp_url(),
        )

        self.assertIn("lavfi", args)
        self.assertTrue(any(value.startswith("testsrc=") for value in args))
        self.assertNotIn("-vcodec", args)
        self.assertNotIn("mjpeg", args)

    def test_default_opencv_ffmpeg_options_are_tcp_only(self):
        self.assertIn("rtsp_transport;tcp", stack.DEFAULT_OPENCV_FFMPEG_OPTIONS)
        self.assertIn("fflags;nobuffer", stack.DEFAULT_OPENCV_FFMPEG_OPTIONS)
        self.assertIn("reorder_queue_size;0", stack.DEFAULT_OPENCV_FFMPEG_OPTIONS)

    def test_build_ffprobe_args_limits_live_rtsp_probe(self):
        rtsp_url = stack.mediamtx_rtsp_url()
        args = stack.build_ffprobe_args(
            "ffprobe",
            rtsp_url,
        )

        self.assertIn("-analyzeduration", args)
        self.assertIn("-probesize", args)
        self.assertIn("-select_streams", args)
        self.assertEqual(args[-1], rtsp_url)

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
            rtsp_url=stack.mediamtx_rtsp_url(),
            frame_id=stack.DEFAULT_MEDIAMTX_PATH,
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

        self.assertEqual(args.camera_name, "Logitech StreamCam")
        self.assertEqual(args.width, 1920)
        self.assertEqual(args.height, 1080)
        self.assertEqual(args.fps, 30)
        self.assertEqual(args.frame_id, stack.DEFAULT_MEDIAMTX_PATH)
        self.assertEqual(args.rtsp_url, stack.mediamtx_rtsp_url())
        self.assertEqual(args.publish_jpeg_every, 0.0)
        self.assertEqual(args.capture_interval, 0.0)
        self.assertEqual(args.gop, 30)
        self.assertEqual(args.ffmpeg_input_codec, "mjpeg")
        self.assertEqual(args.hub_camera_backend, "ffmpeg")
        self.assertEqual(args.camera_open_timeout_ms, 5000)
        self.assertEqual(args.camera_read_timeout_ms, 3000)
        self.assertEqual(args.max_clients, 8)
        self.assertEqual(args.viewer_host, "127.0.0.1")
        self.assertEqual(args.viewer_port, 8770)
        self.assertFalse(args.no_viewer_server)
        self.assertFalse(args.force_stop_existing)
        self.assertFalse(args.no_browser)
        self.assertEqual(args.camera_restart_initial_delay, 0.5)
        self.assertEqual(args.camera_restart_max_delay, 5.0)

    def test_default_capture_request_flows_into_ffmpeg_argv(self):
        defaults = stack.build_parser().parse_args([])

        args = stack.build_ffmpeg_args(
            ffmpeg="ffmpeg",
            ffmpeg_video_source=defaults.ffmpeg_video_source,
            ffmpeg_input_codec=defaults.ffmpeg_input_codec,
            camera_name=defaults.camera_name,
            width=defaults.width,
            height=defaults.height,
            fps=defaults.fps,
            bitrate=defaults.bitrate,
            gop=defaults.gop,
            rtsp_url=defaults.rtsp_url,
        )

        self.assertEqual(args[args.index("-video_size") + 1], "1920x1080")
        self.assertEqual(args[args.index("-framerate") + 1], "30")
        self.assertEqual(args[args.index("-vcodec") + 1], "mjpeg")
        self.assertIn("video=Logitech StreamCam", args)

    def test_camera_publisher_exit_restarts_without_stopping_camera_stack(self):
        failed_process = mock.Mock()
        failed_process.poll.return_value = 1
        failed_process.returncode = 1
        managed = stack.ManagedProcess(
            name="ffmpeg-cam0",
            process=failed_process,
            log_file=Path("failed.log"),
            started_at="2026-07-14T00:00:00+00:00",
            restartable=True,
            command=("ffmpeg", "-f", "dshow"),
            stdin=stack.subprocess.PIPE,
        )
        supervisor = stack.StackSupervisor.__new__(stack.StackSupervisor)
        supervisor.processes = [managed]
        supervisor._stopping = False
        supervisor.ready = True
        supervisor.stop = mock.Mock()

        def restart_once(item):
            self.assertIs(item, managed)
            supervisor._stopping = True
            return True

        supervisor._restart_process = mock.Mock(side_effect=restart_once)
        supervisor._refresh_camera_recovery = mock.Mock()
        with mock.patch.object(stack.time, "sleep"):
            result = supervisor._monitor()

        self.assertEqual(result, 0)
        supervisor._restart_process.assert_called_once_with(managed)
        supervisor.stop.assert_not_called()

    def test_restart_replaces_only_camera_publisher_and_marks_degraded(self):
        failed_process = mock.Mock()
        failed_process.returncode = 1
        managed = stack.ManagedProcess(
            name="ffmpeg-cam0",
            process=failed_process,
            log_file=Path("failed.log"),
            started_at="2026-07-14T00:00:00+00:00",
            restartable=True,
            command=("ffmpeg", "-f", "dshow"),
            stdin=stack.subprocess.PIPE,
        )
        replacement = mock.Mock()
        supervisor = stack.StackSupervisor.__new__(stack.StackSupervisor)
        supervisor.args = mock.Mock(
            camera_restart_initial_delay=0.5,
            camera_restart_max_delay=5.0,
        )
        supervisor.processes = [managed]
        supervisor._stopping = False
        supervisor._restart_attempts = {}
        supervisor.ready = True
        supervisor.ready_detail = "ready"
        supervisor._write_process_manifest = mock.Mock()
        supervisor._start = mock.Mock(return_value=replacement)

        with mock.patch.object(stack.time, "sleep") as sleep:
            restarted = supervisor._restart_process(managed)

        self.assertTrue(restarted)
        self.assertIs(supervisor.processes[0], replacement)
        self.assertFalse(supervisor.ready)
        self.assertIn("awaiting fresh frame", supervisor.ready_detail)
        sleep.assert_called_once_with(0.5)
        supervisor._start.assert_called_once_with(
            "ffmpeg-cam0",
            ["ffmpeg", "-f", "dshow"],
            stdin=stack.subprocess.PIPE,
            critical=True,
            restartable=True,
            register=False,
        )

    def test_recovery_requires_fresh_camera_and_gesture_topics(self):
        process = mock.Mock()
        process.poll.return_value = None
        publisher = stack.ManagedProcess(
            name="ffmpeg-cam0",
            process=process,
            log_file=Path("publisher.log"),
            started_at="2026-07-14T00:00:00+00:00",
            restartable=True,
        )
        supervisor = stack.StackSupervisor.__new__(stack.StackSupervisor)
        supervisor.args = mock.Mock(hub_host="127.0.0.1", hub_port=8765)
        supervisor.processes = [publisher]
        supervisor.ready = False
        supervisor.ready_at = ""
        supervisor.ready_detail = "reconnecting"
        supervisor._restart_attempts = {"ffmpeg-cam0": 2}
        supervisor._write_process_manifest = mock.Mock()

        async def fresh_topics(_url, _wait_seconds):
            return set(stack.REQUIRED_READY_TOPICS)

        with mock.patch.object(
            stack,
            "probe_camera_hub_topics_once",
            side_effect=fresh_topics,
        ):
            recovered = supervisor._refresh_camera_recovery()

        self.assertTrue(recovered)
        self.assertTrue(supervisor.ready)
        self.assertIn("fresh frame", supervisor.ready_detail)
        self.assertNotIn("ffmpeg-cam0", supervisor._restart_attempts)
        supervisor._write_process_manifest.assert_called_once()

    def test_process_manifest_path_uses_home_control_state_dir(self):
        original = os.environ.get("HOME_CONTROL_STACK_STATE_DIR")
        try:
            os.environ["HOME_CONTROL_STACK_STATE_DIR"] = "runtime-state"

            path = stack.process_manifest_path()
        finally:
            if original is None:
                os.environ.pop("HOME_CONTROL_STACK_STATE_DIR", None)
            else:
                os.environ["HOME_CONTROL_STACK_STATE_DIR"] = original

        self.assertEqual(
            path,
            Path("runtime-state")
            / "modules"
            / "mediapipe_camera_hub_stack"
            / "processes.json",
        )

    def test_ready_topic_requires_live_camera_status_and_gesture_state(self):
        camera_envelope = {
            "topic": stack.CAMERA_STATUS_TOPIC,
            "payload": {
                "type": "camera_status",
                "camera": {"opened": True, "frame_read_ok": True},
            },
        }
        gesture_envelope = {
            "topic": stack.SWORD_SIGN_STATE_TOPIC,
            "payload": {
                "type": "gesture_state",
                "gestures": {
                    "sword_sign": {"active": False, "confidence": 0.0},
                },
            },
        }

        self.assertEqual(
            stack.ready_topic_from_envelope(camera_envelope),
            stack.CAMERA_STATUS_TOPIC,
        )
        self.assertEqual(
            stack.ready_topic_from_envelope(gesture_envelope),
            stack.SWORD_SIGN_STATE_TOPIC,
        )

    def test_ready_topic_rejects_port_only_or_incomplete_payloads(self):
        unread_camera_envelope = {
            "topic": stack.CAMERA_STATUS_TOPIC,
            "payload": {
                "type": "camera_status",
                "camera": {"opened": True, "frame_read_ok": False},
            },
        }
        missing_sword_envelope = {
            "topic": stack.SWORD_SIGN_STATE_TOPIC,
            "payload": {
                "type": "gesture_state",
                "gestures": {"victory": {"active": True, "confidence": 1.0}},
            },
        }

        self.assertIsNone(stack.ready_topic_from_envelope(unread_camera_envelope))
        self.assertIsNone(stack.ready_topic_from_envelope(missing_sword_envelope))
        self.assertIsNone(stack.ready_topic_from_envelope({"topic": "/unrelated"}))

    def test_connect_host_maps_wildcard_to_localhost(self):
        self.assertEqual(stack.connect_host("0.0.0.0"), "127.0.0.1")
        self.assertEqual(stack.connect_host("127.0.0.1"), "127.0.0.1")

    def test_mediamtx_webrtc_url_uses_rtsp_path_for_browser_video(self):
        rtsp_url = stack.mediamtx_rtsp_url()
        url = stack.mediamtx_webrtc_url(rtsp_url)

        self.assertEqual(
            url,
            (
                "http://127.0.0.1:8889/"
                f"{stack.DEFAULT_MEDIAMTX_PATH}"
                "?controls=false&muted=true&autoplay=true"
            ),
        )

    def test_mediamtx_default_url_helpers_centralize_ports_and_path(self):
        self.assertEqual(
            stack.MEDIAMTX_PORTS,
            (
                stack.DEFAULT_MEDIAMTX_RTSP_PORT,
                stack.DEFAULT_MEDIAMTX_HLS_PORT,
                stack.DEFAULT_MEDIAMTX_WEBRTC_PORT,
            ),
        )
        self.assertEqual(
            stack.mediamtx_rtsp_url(path="/cam2"),
            "rtsp://127.0.0.1:8554/cam2",
        )
        self.assertEqual(
            stack.mediamtx_webrtc_url(
                stack.mediamtx_rtsp_url(path="/cam2"),
                webrtc_port=18889,
            ),
            "http://127.0.0.1:18889/cam2?controls=false&muted=true&autoplay=true",
        )

    def test_browser_monitor_url_passes_media_and_websocket_urls(self):
        media_url = stack.mediamtx_webrtc_url(stack.mediamtx_rtsp_url()).replace(
            "&muted=true&autoplay=true",
            "",
        )
        url = stack.browser_monitor_url(
            "http://127.0.0.1:8770/browser_camera_hub_viewer.html",
            media_url=media_url,
            ws_url="ws://127.0.0.1:8765",
        )

        self.assertIn(
            "http://127.0.0.1:8770/browser_camera_hub_viewer.html?",
            url,
        )
        self.assertIn(
            f"mediaUrl={stack.quote(media_url, safe='')}",
            url,
        )
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
        media_url = stack.mediamtx_webrtc_url(stack.mediamtx_rtsp_url()).replace(
            "&muted=true&autoplay=true",
            "",
        )
        args = stack.build_viewer_server_args(
            uv="uv",
            host="127.0.0.1",
            port=8770,
            viewer_path=Path("apps/browser_camera_hub_viewer.html"),
            media_url=media_url,
            ws_url="ws://127.0.0.1:18865",
            target="sword_sign",
            allow_remote=False,
        )

        self.assertEqual(
            args[0:4],
            ["uv", "run", "python", "apps/serve_browser_monitor.py"],
        )
        self.assertIn("--viewer-path", args)
        self.assertIn("--media-url", args)
        self.assertIn(media_url, args)
        self.assertIn("--ws-url", args)
        self.assertIn("ws://127.0.0.1:18865", args)
        self.assertIn("--target", args)
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
        original_parent_map = stack.process_parent_map
        original_ports = stack.listen_port_owners
        original_matching = stack.list_matching_processes
        original_details = stack.process_details

        try:
            stack.current_process_family_pids = (
                lambda current_pid, parent_map=None: {10, 11, 12}
            )
            stack.process_parent_map = lambda: {99: 1}
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
            stack.process_parent_map = original_parent_map
            stack.listen_port_owners = original_ports
            stack.list_matching_processes = original_matching
            stack.process_details = original_details

        self.assertEqual([process["pid"] for process in processes], [99])

    def test_metadata_echo_without_selected_port_is_ignored(self):
        original_family = stack.current_process_family_pids
        original_parent_map = stack.process_parent_map
        original_ports = stack.listen_port_owners
        original_matching = stack.list_matching_processes
        original_details = stack.process_details

        try:
            stack.current_process_family_pids = lambda current_pid, parent_map=None: {10}
            stack.process_parent_map = lambda: {55: 1, 77: 1}
            stack.listen_port_owners = lambda ports: {}
            stack.list_matching_processes = lambda: [
                {
                    "pid": 55,
                    "name": "codex.exe",
                    "command": "codex prompt mentions camera_hub_stack.py",
                },
                {
                    "pid": 77,
                    "name": "python.exe",
                    "command": "python scripts/camera_hub_stack.py",
                },
            ]
            stack.process_details = lambda pids: {}

            processes = stack.find_existing_stack_processes(
                ports=(8554, 8765),
                current_pid=10,
            )
        finally:
            stack.current_process_family_pids = original_family
            stack.process_parent_map = original_parent_map
            stack.listen_port_owners = original_ports
            stack.list_matching_processes = original_matching
            stack.process_details = original_details

        self.assertEqual(processes, [])

    def test_selected_port_owner_is_retained(self):
        original_family = stack.current_process_family_pids
        original_parent_map = stack.process_parent_map
        original_ports = stack.listen_port_owners
        original_matching = stack.list_matching_processes
        original_details = stack.process_details

        try:
            stack.current_process_family_pids = lambda current_pid, parent_map=None: {10}
            stack.process_parent_map = lambda: {99: 1}
            stack.listen_port_owners = lambda ports: {99: {8554}}
            stack.list_matching_processes = lambda: []
            stack.process_details = lambda pids: {
                99: {"name": "mediamtx.exe", "command": "mediamtx config.yml"}
            }

            processes = stack.find_existing_stack_processes(
                ports=(8554, 8765),
                current_pid=10,
            )
        finally:
            stack.current_process_family_pids = original_family
            stack.process_parent_map = original_parent_map
            stack.listen_port_owners = original_ports
            stack.list_matching_processes = original_matching
            stack.process_details = original_details

        self.assertEqual([process["pid"] for process in processes], [99])
        self.assertEqual(processes[0]["ports"], [8554])

    def test_validated_owner_lineage_is_retained_without_sibling_expansion(self):
        original_family = stack.current_process_family_pids
        original_parent_map = stack.process_parent_map
        original_ports = stack.listen_port_owners
        original_matching = stack.list_matching_processes
        original_details = stack.process_details

        supervisor = {
            "pid": 55,
            "name": "uv.exe",
            "command": "uv run python scripts/camera_hub_stack.py",
        }
        direct_child = {
            "pid": 88,
            "name": "python.exe",
            "command": "python apps/serve_browser_monitor.py",
        }
        sibling = {
            "pid": 77,
            "name": "python.exe",
            "command": "python apps/serve_camera_hub.py",
        }

        try:
            stack.current_process_family_pids = lambda current_pid, parent_map=None: {10}
            stack.process_parent_map = lambda: {55: 1, 77: 55, 88: 99, 99: 55}
            stack.listen_port_owners = lambda ports: {99: {8765}}
            stack.process_details = lambda pids: {
                99: {
                    "name": "python.exe",
                    "command": "python apps/serve_camera_hub.py",
                }
            }

            results = []
            for matching in (
                [supervisor, direct_child, sibling],
                [sibling, direct_child, supervisor],
            ):
                stack.list_matching_processes = lambda rows=matching: rows
                processes = stack.find_existing_stack_processes(
                    ports=(8554, 8765),
                    current_pid=10,
                )
                results.append([process["pid"] for process in processes])
        finally:
            stack.current_process_family_pids = original_family
            stack.process_parent_map = original_parent_map
            stack.listen_port_owners = original_ports
            stack.list_matching_processes = original_matching
            stack.process_details = original_details

        self.assertEqual(results, [[55, 88, 99], [55, 88, 99]])

    def test_force_stop_refuses_external_browser_and_updater_processes(self):
        original_find = stack.find_existing_stack_processes

        try:
            for name in ("chrome.exe", "updater.exe"):
                with self.subTest(name=name):
                    stack.find_existing_stack_processes = (
                        lambda ports, current_pid, process_name=name: [
                            {
                                "pid": 77,
                                "name": process_name,
                                "command": f"{process_name} --background",
                                "ports": [8889],
                            }
                        ]
                    )

                    with self.assertRaisesRegex(
                        RuntimeError, "Refusing to stop external"
                    ):
                        stack.check_existing_stack(
                            ports=(8889,),
                            force_stop=True,
                            current_pid=10,
                        )
        finally:
            stack.find_existing_stack_processes = original_find

    def test_stack_process_pattern_does_not_match_generic_ffmpeg(self):
        self.assertNotIn("ffmpeg", stack.STACK_PROCESS_PATTERN)

    def test_log_helpers_redact_credentials(self):
        value = stack.quote_for_log("rtsp://user:secret@example.test:8554/cam0")

        self.assertNotIn("secret", value)
        self.assertIn("rtsp://<redacted>@example.test:8554/cam0", value)

    def test_parser_rejects_invalid_runtime_numbers(self):
        invalid_args = [
            ["--width", "0"],
            ["--width", str(stack.MAX_CAMERA_WIDTH + 1)],
            ["--height", str(stack.MIN_CAMERA_HEIGHT - 1)],
            ["--height", str(stack.MAX_CAMERA_HEIGHT + 1)],
            ["--fps", str(stack.MAX_CAMERA_FPS + 1)],
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
