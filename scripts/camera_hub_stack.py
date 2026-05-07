from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import urlopen


DEFAULT_MEDIAMTX_PATH = Path(r"C:\Tools\mediamtx_v1.18.1_windows_amd64\mediamtx.exe")
DEFAULT_FFMPEG_PATH = Path(r"C:\Tools\ffmpeg\bin\ffmpeg.exe")
DEFAULT_FFPROBE_PATH = Path(r"C:\Tools\ffmpeg\bin\ffprobe.exe")
DEFAULT_OPENCV_FFMPEG_OPTIONS = (
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|reorder_queue_size;0"
)
MEDIAMTX_PORTS = (8554, 8888, 8889)
STACK_PROCESS_PATTERN = (
    "serve_camera_hub|serve_browser_monitor|camera_hub_stack|mediamtx"
)
CAMERA_STATUS_TOPIC = "/camera/status"
SWORD_SIGN_STATE_TOPIC = "/vision/sword_sign/state"
REQUIRED_READY_TOPICS = frozenset({CAMERA_STATUS_TOPIC, SWORD_SIGN_STATE_TOPIC})
EXTERNAL_PROCESS_DENYLIST = {
    "chrome",
    "msedge",
    "firefox",
    "brave",
    "brave-browser",
    "opera",
    "vivaldi",
    "updater",
    "googleupdate",
    "microsoftedgeupdate",
}
URL_CREDENTIAL_RE = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<userinfo>[^/\s\"'@]+@)"
)


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[str]
    log_file: Path
    started_at: str
    critical: bool = True

    def is_running(self) -> bool:
        return self.process.poll() is None


def process_manifest_path() -> Path | None:
    state_dir = os.environ.get("HOME_CONTROL_STACK_STATE_DIR")
    if not state_dir:
        return None
    return (
        Path(state_dir)
        / "modules"
        / "mediapipe_camera_hub_stack"
        / "processes.json"
    )


class StackSupervisor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.repo_root = Path(__file__).resolve().parents[1]
        self.log_dir = (self.repo_root / args.log_dir).resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.process_manifest_path = process_manifest_path()
        self.processes: list[ManagedProcess] = []
        self.ready = False
        self.ready_at = ""
        self.ready_detail = "starting"
        self._stopping = False
        self._lock = threading.Lock()

    def run(self) -> int:
        mediamtx = resolve_tool(
            "mediamtx",
            self.args.mediamtx_path,
            [DEFAULT_MEDIAMTX_PATH],
        )
        ffmpeg = resolve_tool("ffmpeg", self.args.ffmpeg_path, [DEFAULT_FFMPEG_PATH])
        ffprobe = resolve_tool("ffprobe", self.args.ffprobe_path, [DEFAULT_FFPROBE_PATH])
        uv = resolve_tool("uv", self.args.uv_path, [])

        config_path = (self.repo_root / self.args.mediamtx_config).resolve()
        if not config_path.exists():
            raise SystemExit(f"MediaMTX config not found: {config_path}")

        check_existing_stack(
            ports=stack_ports(self.args),
            force_stop=self.args.force_stop_existing,
            current_pid=os.getpid(),
        )

        self._start(
            "mediamtx",
            [mediamtx, str(config_path)],
        )
        self._sleep_and_check(1.5)

        self._start(
            "ffmpeg-cam0",
            build_ffmpeg_args(
                ffmpeg=ffmpeg,
                camera_name=self.args.camera_name,
                width=self.args.width,
                height=self.args.height,
                fps=self.args.fps,
                bitrate=self.args.bitrate,
                gop=self.args.gop,
                rtsp_url=self.args.rtsp_url,
            ),
            stdin=subprocess.PIPE,
        )

        if not self.args.skip_rtsp_wait:
            wait_for_rtsp(ffprobe, self.args.rtsp_url, self.args.wait_seconds)

        hub_args = build_hub_args(
            uv=uv,
            host=self.args.hub_host,
            port=self.args.hub_port,
            rtsp_url=self.args.rtsp_url,
            frame_id=self.args.frame_id,
            publish_jpeg_every=self.args.publish_jpeg_every,
            gesture_every=self.args.gesture_every,
            gesture_model_complexity=self.args.gesture_model_complexity,
            release_grace_seconds=self.args.release_grace_seconds,
            camera_backend=self.args.hub_camera_backend,
            opencv_ffmpeg_capture_options=self.args.opencv_ffmpeg_capture_options,
            camera_open_timeout_ms=self.args.camera_open_timeout_ms,
            camera_read_timeout_ms=self.args.camera_read_timeout_ms,
            capture_interval=self.args.capture_interval,
            width=self.args.width,
            height=self.args.height,
            fps=self.args.fps,
            ffmpeg_path=ffmpeg,
            max_clients=self.args.max_clients,
        )
        self._start("camera-hub", hub_args)
        self._sleep_and_check(1.0)
        observed_topics = wait_for_camera_hub_topics(
            self.args.hub_port,
            connect_host(self.args.hub_host),
            self.args.hub_wait_seconds,
            service_name="Camera Hub topics",
        )
        self.ready = True
        self.ready_at = datetime.now(timezone.utc).isoformat()
        self.ready_detail = "received " + ", ".join(sorted(observed_topics))
        self._write_process_manifest()

        if self.args.python_gui:
            self._start(
                "python-gui",
                [uv, "run", "python", "apps/camera_hub_gui.py"],
                critical=False,
            )

        media_url = mediamtx_webrtc_url(self.args.rtsp_url)
        ws_url = f"ws://{connect_host(self.args.hub_host)}:{self.args.hub_port}"
        if self.args.no_viewer_server:
            viewer_base_url = (
                self.repo_root / "apps" / "browser_camera_hub_viewer.html"
            ).resolve().as_uri()
            viewer_health_url = None
        else:
            viewer_args = build_viewer_server_args(
                uv=uv,
                host=self.args.viewer_host,
                port=self.args.viewer_port,
                viewer_path=self.repo_root / "apps" / "browser_camera_hub_viewer.html",
                allow_remote=self.args.viewer_allow_remote,
            )
            self._start("browser-monitor", viewer_args)
            self._sleep_and_check(0.5)
            viewer_health_url = viewer_server_health_url(
                self.args.viewer_host,
                self.args.viewer_port,
            )
            wait_for_http(
                viewer_health_url,
                self.args.viewer_wait_seconds,
                service_name="Browser Monitor HTTP",
            )
            viewer_base_url = viewer_server_page_url(
                self.args.viewer_host,
                self.args.viewer_port,
            )

        viewer_url = browser_monitor_url(
            viewer_base_url,
            media_url=media_url,
            ws_url=ws_url,
        )

        print()
        print("Camera Hub stack routes:")
        print(f"  Camera publish: FFmpeg -> MediaMTX RTSP {self.args.rtsp_url}")
        print(f"  Browser Monitor video: {media_url}")
        print(f"  Camera Hub input: {self.args.rtsp_url}")
        print(f"  Camera Hub topics: {ws_url}")
        if viewer_health_url is not None:
            print(f"  Browser Monitor HTTP: {viewer_base_url}")
        else:
            print(f"  Browser Monitor file fallback: {viewer_base_url}")
        print(f"  Browser Monitor GUI: {viewer_url}")
        if self.args.publish_jpeg_every <= 0:
            print("  Python JPEG image topic: disabled (normal MediaMTX mode)")
        else:
            print(
                "  Python JPEG image topic: enabled for debug "
                f"every {self.args.publish_jpeg_every:.3f}s"
            )

        if not self.args.no_browser:
            open_browser_viewer(viewer_url)

        print("Camera Hub stack is running.")
        print(f"Logs: {self.log_dir}")
        print("Stop: press Ctrl+C in this terminal.")
        print()
        return self._monitor()

    def stop(self) -> None:
        with self._lock:
            if self._stopping:
                return
            self._stopping = True

        print()
        print("Stopping Camera Hub stack...")
        self.ready = False
        self.ready_detail = "stopping"

        for managed in reversed(self.processes):
            if managed.is_running() and managed.name.startswith("ffmpeg"):
                send_ffmpeg_quit(managed.process)

        deadline = time.monotonic() + self.args.graceful_timeout
        self._wait_until(deadline)

        for managed in reversed(self.processes):
            if managed.is_running():
                send_interrupt(managed.process)

        deadline = time.monotonic() + self.args.graceful_timeout
        self._wait_until(deadline)

        for managed in reversed(self.processes):
            if managed.is_running():
                terminate_tree(managed.process)

        deadline = time.monotonic() + 3.0
        self._wait_until(deadline)

        for managed in reversed(self.processes):
            if managed.is_running():
                kill_tree(managed.process)

        self._write_process_manifest()
        print("Camera Hub stack stopped.")

    def _start(
        self,
        name: str,
        command: list[str],
        *,
        stdin: int | None = subprocess.DEVNULL,
        critical: bool = True,
    ) -> ManagedProcess:
        log_file = self.log_dir / f"{name}.log"
        log_handle = log_file.open("a", encoding="utf-8", errors="replace")
        log_handle.write(f"\n\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log_handle.write(
            "command: "
            + " ".join(quote_for_log(part) for part in command)
            + "\n"
        )
        log_handle.flush()

        creationflags = 0
        if os.name == "nt":
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP

        process = subprocess.Popen(
            command,
            cwd=self.repo_root,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        managed = ManagedProcess(
            name=name,
            process=process,
            log_file=log_file,
            started_at=datetime.now(timezone.utc).isoformat(),
            critical=critical,
        )
        self.processes.append(managed)
        self._write_process_manifest()
        thread = threading.Thread(
            target=stream_output,
            args=(managed, log_handle),
            name=f"{name}-log",
            daemon=True,
        )
        thread.start()
        print(f"started {name}: pid={process.pid}")
        return managed

    def _sleep_and_check(self, seconds: float) -> None:
        time.sleep(seconds)
        for managed in self.processes:
            if managed.critical and not managed.is_running():
                raise RuntimeError(
                    f"{managed.name} exited early with code {managed.process.returncode}. "
                    f"See log: {managed.log_file}"
                )

    def _monitor(self) -> int:
        while not self._stopping:
            for managed in self.processes:
                if managed.critical and not managed.is_running():
                    print(
                        f"{managed.name} exited with code {managed.process.returncode}. "
                        f"Stopping the rest of the stack."
                    )
                    self._write_process_manifest()
                    self.stop()
                    return int(managed.process.returncode or 1)
            time.sleep(0.5)
        return 0

    def _wait_until(self, deadline: float) -> None:
        while time.monotonic() < deadline:
            if all(not managed.is_running() for managed in self.processes):
                self._write_process_manifest()
                return
            time.sleep(0.1)
        self._write_process_manifest()

    def _write_process_manifest(self) -> None:
        if self.process_manifest_path is None:
            return
        processes = []
        for managed in self.processes:
            processes.append(
                {
                    "name": managed.name,
                    "pid": managed.process.pid,
                    "critical": managed.critical,
                    "running": managed.is_running(),
                    "returncode": managed.process.returncode,
                    "started_at": managed.started_at,
                    "log_file": str(managed.log_file),
                }
            )
        payload = {
            "schema_version": 1,
            "module": "mediapipe-sword-sign",
            "service": "mediapipe_camera_hub_stack",
            "owner_pid": os.getpid(),
            "ready": self.ready,
            "ready_at": self.ready_at or None,
            "ready_detail": self.ready_detail,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "processes": processes,
        }
        try:
            self.process_manifest_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.process_manifest_path.with_suffix(
                self.process_manifest_path.suffix + ".tmp"
            )
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self.process_manifest_path)
        except OSError as exc:
            print(
                f"warning: failed to write process manifest "
                f"{self.process_manifest_path}: {exc}"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start MediaMTX, FFmpeg camera publish, and Camera Hub in one terminal.",
    )
    parser.add_argument("--camera-name", default="HD Pro Webcam C920")
    parser.add_argument("--frame-id", default="cam0")
    parser.add_argument("--rtsp-url", default="rtsp://127.0.0.1:8554/cam0")
    parser.add_argument("--width", type=parse_positive_int, default=640)
    parser.add_argument("--height", type=parse_positive_int, default=480)
    parser.add_argument("--fps", type=parse_positive_int, default=30)
    parser.add_argument("--bitrate", default="800k")
    parser.add_argument("--gop", type=parse_positive_int, default=30)
    parser.add_argument("--hub-host", default="127.0.0.1")
    parser.add_argument("--hub-port", type=parse_port, default=8765)
    parser.add_argument("--publish-jpeg-every", type=parse_non_negative_float, default=0.0)
    parser.add_argument("--capture-interval", type=parse_non_negative_float, default=0.0)
    parser.add_argument("--gesture-every", type=parse_non_negative_float, default=0.05)
    parser.add_argument("--gesture-model-complexity", type=parse_model_complexity, default=0)
    parser.add_argument("--release-grace-seconds", type=parse_non_negative_float, default=0.03)
    parser.add_argument(
        "--hub-camera-backend",
        choices=["ffmpeg", "ffmpeg-pipe"],
        default="ffmpeg-pipe",
        help=(
            "Camera Hub RTSP reader backend. ffmpeg-pipe avoids OpenCV RTSP "
            "buffering by reading raw BGR frames from ffmpeg."
        ),
    )
    parser.add_argument(
        "--opencv-ffmpeg-capture-options",
        default=DEFAULT_OPENCV_FFMPEG_OPTIONS,
    )
    parser.add_argument("--camera-open-timeout-ms", type=int, default=5000)
    parser.add_argument("--camera-read-timeout-ms", type=int, default=3000)
    parser.add_argument(
        "--max-clients",
        type=parse_positive_int,
        default=8,
        help="Maximum Camera Hub WebSocket clients for gesture/status topics.",
    )
    parser.add_argument(
        "--mediamtx-config",
        default=r"configs\mediamtx\mediamtx.publisher.example.yml",
    )
    parser.add_argument("--mediamtx-path", default="")
    parser.add_argument("--ffmpeg-path", default="")
    parser.add_argument("--ffprobe-path", default="")
    parser.add_argument("--uv-path", default="")
    parser.add_argument("--wait-seconds", type=parse_positive_int, default=20)
    parser.add_argument("--hub-wait-seconds", type=parse_positive_int, default=20)
    parser.add_argument("--viewer-host", default="127.0.0.1")
    parser.add_argument("--viewer-port", type=parse_port, default=8770)
    parser.add_argument("--viewer-wait-seconds", type=parse_positive_int, default=10)
    parser.add_argument("--viewer-allow-remote", action="store_true")
    parser.add_argument("--no-viewer-server", action="store_true")
    parser.add_argument("--graceful-timeout", type=parse_positive_float, default=8.0)
    parser.add_argument("--skip-rtsp-wait", action="store_true")
    parser.add_argument("--force-stop-existing", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--python-gui", action="store_true")
    parser.add_argument(
        "--log-dir",
        default=r".runtime\camera-hub-stack\logs",
    )
    return parser


def parse_positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def parse_port(value: str) -> int:
    parsed = parse_positive_int(value)
    if parsed > 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return parsed


def parse_non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("value must be 0 or greater")
    return parsed


def parse_positive_float(value: str) -> float:
    parsed = parse_non_negative_float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def parse_model_complexity(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("model complexity must be 0 or 1") from exc
    if parsed not in {0, 1}:
        raise argparse.ArgumentTypeError("model complexity must be 0 or 1")
    return parsed


def build_ffmpeg_args(
    *,
    ffmpeg: str,
    camera_name: str,
    width: int,
    height: int,
    fps: int,
    bitrate: str,
    gop: int,
    rtsp_url: str,
) -> list[str]:
    return [
        ffmpeg,
        "-f",
        "dshow",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(fps),
        "-i",
        f"video={camera_name}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-b:v",
        bitrate,
        "-g",
        str(gop),
        "-keyint_min",
        str(gop),
        "-sc_threshold",
        "0",
        "-rtsp_transport",
        "tcp",
        "-f",
        "rtsp",
        rtsp_url,
    ]


def build_hub_args(
    *,
    uv: str,
    host: str,
    port: int,
    rtsp_url: str,
    frame_id: str,
    publish_jpeg_every: float,
    gesture_every: float,
    gesture_model_complexity: int,
    release_grace_seconds: float,
    camera_backend: str,
    opencv_ffmpeg_capture_options: str,
    camera_open_timeout_ms: int,
    camera_read_timeout_ms: int,
    capture_interval: float,
    width: int,
    height: int,
    fps: int,
    ffmpeg_path: str,
    max_clients: int,
) -> list[str]:
    return [
        uv,
        "run",
        "python",
        "apps/serve_camera_hub.py",
        "--host",
        host,
        "--port",
        str(port),
        "--interval",
        str(capture_interval),
        "--camera-source",
        rtsp_url,
        "--camera-backend",
        camera_backend,
        "--camera-width",
        str(width),
        "--camera-height",
        str(height),
        "--camera-fps",
        str(fps),
        "--ffmpeg-path",
        ffmpeg_path,
        "--opencv-ffmpeg-capture-options",
        opencv_ffmpeg_capture_options,
        "--camera-open-timeout-ms",
        str(camera_open_timeout_ms),
        "--camera-read-timeout-ms",
        str(camera_read_timeout_ms),
        "--frame-id",
        frame_id,
        "--publish-jpeg-every",
        str(publish_jpeg_every),
        "--gesture-every",
        str(gesture_every),
        "--gesture-model-complexity",
        str(gesture_model_complexity),
        "--release-grace-seconds",
        str(release_grace_seconds),
        "--max-clients",
        str(max_clients),
        "--publish-landmarks",
    ]


def build_viewer_server_args(
    *,
    uv: str,
    host: str,
    port: int,
    viewer_path: Path,
    allow_remote: bool,
) -> list[str]:
    args = [
        uv,
        "run",
        "python",
        "apps/serve_browser_monitor.py",
        "--host",
        host,
        "--port",
        str(port),
        "--viewer-path",
        str(viewer_path),
    ]
    if allow_remote:
        args.append("--allow-remote")
    return args


def stack_ports(args: argparse.Namespace) -> tuple[int, ...]:
    ports = set(MEDIAMTX_PORTS)
    ports.add(int(args.hub_port))
    if not args.no_viewer_server:
        ports.add(int(args.viewer_port))
    return tuple(sorted(ports))


def resolve_tool(name: str, explicit: str, fallbacks: list[Path]) -> str:
    if explicit:
        explicit_path = Path(explicit)
        if explicit_path.exists():
            return str(explicit_path)
        found = shutil.which(explicit)
        if found:
            return found
        raise SystemExit(f"{name} not found: {explicit}")

    found = shutil.which(name)
    if found:
        return found

    for fallback in fallbacks:
        if fallback.exists():
            return str(fallback)

    fallback_text = ", ".join(str(path) for path in fallbacks) or "(none)"
    raise SystemExit(f"{name} not found in PATH. Fallbacks checked: {fallback_text}")


def check_existing_stack(
    *,
    ports: tuple[int, ...],
    force_stop: bool,
    current_pid: int,
) -> None:
    existing = find_existing_stack_processes(ports=ports, current_pid=current_pid)
    if not existing:
        return

    print("Existing Camera Hub stack related processes were found:")
    for process in existing:
        command = redact_text_for_log(process.get("command") or "")
        if len(command) > 140:
            command = command[:137] + "..."
        port_text = ""
        if process.get("ports"):
            port_text = f" ports={','.join(str(port) for port in process['ports'])}"
        print(
            f"  pid={process['pid']} name={process['name']}{port_text} command={command}"
        )

    if not force_stop:
        ports_text = ", ".join(str(port) for port in ports)
        raise RuntimeError(
            "Existing stack processes or occupied ports were found. "
            f"Stop them first, or rerun with --force-stop-existing. Ports checked: {ports_text}"
        )

    denied = [
        process
        for process in existing
        if is_external_process_name(str(process.get("name") or ""))
    ]
    if denied:
        summary = ", ".join(
            f"pid={process['pid']} name={process.get('name') or ''}" for process in denied
        )
        raise RuntimeError(
            "Refusing to stop external browser/updater process while handling "
            f"Camera Hub port conflicts: {summary}"
        )

    print("Stopping existing stack processes before startup...")
    for process in existing:
        pid = int(process["pid"])
        if pid != current_pid:
            terminate_pid_tree(pid)
    time.sleep(2.0)

    remaining = find_existing_stack_processes(ports=ports, current_pid=current_pid)
    if remaining:
        for process in remaining:
            pid = int(process["pid"])
            if pid != current_pid:
                kill_pid_tree(pid)
        time.sleep(1.0)

    remaining = find_existing_stack_processes(ports=ports, current_pid=current_pid)
    if remaining:
        raise RuntimeError("Could not stop all existing stack processes.")


def find_existing_stack_processes(
    *,
    ports: tuple[int, ...],
    current_pid: int,
) -> list[dict[str, object]]:
    ignored_pids = current_process_family_pids(current_pid)
    port_owners = listen_port_owners(ports)
    candidates: dict[int, dict[str, object]] = {}

    for pid, owned_ports in port_owners.items():
        if pid in ignored_pids:
            continue
        candidates[pid] = {
            "pid": pid,
            "name": "",
            "command": "",
            "ports": sorted(owned_ports),
        }

    for process in list_matching_processes():
        pid = int(process["pid"])
        if pid in ignored_pids or is_process_discovery_helper(process):
            continue
        existing = candidates.setdefault(
            pid,
            {"pid": pid, "name": "", "command": "", "ports": []},
        )
        existing["name"] = process.get("name", "")
        existing["command"] = process.get("command", "")

    if candidates:
        details = process_details(list(candidates))
        for pid, detail in details.items():
            existing = candidates[pid]
            if not existing.get("name"):
                existing["name"] = detail.get("name", "")
            if not existing.get("command"):
                existing["command"] = detail.get("command", "")

    return sorted(candidates.values(), key=lambda item: int(item["pid"]))


def is_process_discovery_helper(process: dict[str, object]) -> bool:
    command = str(process.get("command") or "")
    return (
        "Get-CimInstance Win32_Process" in command
        and STACK_PROCESS_PATTERN in command
    )


def is_external_process_name(name: str) -> bool:
    normalized = name.strip().lower()
    if normalized.endswith(".exe"):
        normalized = normalized[:-4]
    return normalized in EXTERNAL_PROCESS_DENYLIST


def current_process_family_pids(current_pid: int) -> set[int]:
    if os.name == "nt":
        return current_process_family_pids_windows(current_pid)
    return {current_pid, os.getppid()}


def current_process_family_pids_windows(current_pid: int) -> set[int]:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress"
        ),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {current_pid, os.getppid()}

    import json

    parsed = json.loads(result.stdout)
    rows = parsed if isinstance(parsed, list) else [parsed]
    parents: dict[int, int] = {}
    for row in rows:
        try:
            pid = int(row["ProcessId"])
            parent = int(row["ParentProcessId"])
        except (KeyError, TypeError, ValueError):
            continue
        parents[pid] = parent

    family = {current_pid}
    pid = current_pid
    for _ in range(12):
        parent = parents.get(pid)
        if parent is None or parent <= 0 or parent in family:
            break
        family.add(parent)
        pid = parent
    return family


def listen_port_owners(ports: tuple[int, ...]) -> dict[int, set[int]]:
    if os.name == "nt":
        return listen_port_owners_windows(ports)
    return {}


def listen_port_owners_windows(ports: tuple[int, ...]) -> dict[int, set[int]]:
    port_list = ",".join(str(port) for port in ports)
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            f"Get-NetTCPConnection -LocalPort {port_list} -State Listen "
            "-ErrorAction SilentlyContinue | "
            "Select-Object LocalPort,OwningProcess | ConvertTo-Json -Compress"
        ),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {}

    import json

    parsed = json.loads(result.stdout)
    rows = parsed if isinstance(parsed, list) else [parsed]
    owners: dict[int, set[int]] = {}
    for row in rows:
        try:
            pid = int(row["OwningProcess"])
            port = int(row["LocalPort"])
        except (KeyError, TypeError, ValueError):
            continue
        owners.setdefault(pid, set()).add(port)
    return owners


def list_matching_processes() -> list[dict[str, object]]:
    if os.name == "nt":
        return list_matching_processes_windows()
    return []


def list_matching_processes_windows() -> list[dict[str, object]]:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -match "
            f"'{STACK_PROCESS_PATTERN}' }} | "
            "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
        ),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    import json

    parsed = json.loads(result.stdout)
    rows = parsed if isinstance(parsed, list) else [parsed]
    processes: list[dict[str, object]] = []
    for row in rows:
        try:
            pid = int(row["ProcessId"])
        except (KeyError, TypeError, ValueError):
            continue
        processes.append(
            {
                "pid": pid,
                "name": str(row.get("Name") or ""),
                "command": str(row.get("CommandLine") or ""),
            }
        )
    return processes


def process_details(pids: list[int]) -> dict[int, dict[str, str]]:
    if os.name == "nt":
        return process_details_windows(pids)
    return {}


def process_details_windows(pids: list[int]) -> dict[int, dict[str, str]]:
    if not pids:
        return {}
    pid_list = ",".join(str(pid) for pid in pids)
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            f"Get-CimInstance Win32_Process | Where-Object {{ @({pid_list}) "
            "-contains $_.ProcessId }} | "
            "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
        ),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {}

    import json

    parsed = json.loads(result.stdout)
    rows = parsed if isinstance(parsed, list) else [parsed]
    details: dict[int, dict[str, str]] = {}
    for row in rows:
        try:
            pid = int(row["ProcessId"])
        except (KeyError, TypeError, ValueError):
            continue
        details[pid] = {
            "name": str(row.get("Name") or ""),
            "command": str(row.get("CommandLine") or ""),
        }
    return details


def wait_for_rtsp(ffprobe: str, rtsp_url: str, wait_seconds: int) -> None:
    print(f"waiting for RTSP stream: {redact_text_for_log(rtsp_url)}")
    deadline = time.monotonic() + wait_seconds
    last_error = ""
    command = build_ffprobe_args(ffprobe, rtsp_url)
    while time.monotonic() < deadline:
        remaining = max(1.0, min(5.0, deadline - time.monotonic()))
        ready, detail = probe_rtsp_once(command, timeout_seconds=remaining)
        if ready:
            print("RTSP stream is ready.")
            return
        last_error = redact_text_for_log(detail)
        time.sleep(1.0)
    raise RuntimeError(f"RTSP stream did not become ready. Last error: {last_error}")


def wait_for_websocket(
    port: int,
    host: str,
    wait_seconds: int,
    *,
    service_name: str,
) -> None:
    url = f"ws://{host}:{port}"
    print(f"waiting for {service_name}: {url}")
    deadline = time.monotonic() + wait_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            asyncio.run(probe_websocket_once(url))
            print(f"{service_name} is ready.")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(0.5)
    raise RuntimeError(
        f"{service_name} did not become ready on {url}. Last error: {last_error}"
    )


def wait_for_http(url: str, wait_seconds: int, *, service_name: str) -> None:
    print(f"waiting for {service_name}: {url}")
    deadline = time.monotonic() + wait_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:
                if 200 <= response.status < 300:
                    print(f"{service_name} is ready.")
                    return
                last_error = f"HTTP {response.status}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(0.25)
    raise RuntimeError(
        f"{service_name} did not become ready on {url}. Last error: {last_error}"
    )


async def probe_websocket_once(url: str) -> None:
    from websockets.asyncio.client import connect

    async with connect(url):
        return


def wait_for_camera_hub_topics(
    port: int,
    host: str,
    wait_seconds: int,
    *,
    service_name: str,
) -> set[str]:
    url = f"ws://{host}:{port}"
    print(f"waiting for {service_name}: {url}")
    deadline = time.monotonic() + wait_seconds
    last_error = ""
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            observed = asyncio.run(probe_camera_hub_topics_once(url, remaining))
            print(f"{service_name} is ready: {', '.join(sorted(observed))}")
            return observed
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(0.5)
    raise RuntimeError(
        f"{service_name} did not become ready on {url}. Last error: {last_error}"
    )


async def probe_camera_hub_topics_once(url: str, wait_seconds: float) -> set[str]:
    from websockets.asyncio.client import connect

    observed: set[str] = set()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_seconds
    async with connect(url) as websocket:
        while loop.time() < deadline:
            timeout = max(0.1, min(1.0, deadline - loop.time()))
            message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
            envelope = parse_topic_envelope(message)
            topic = ready_topic_from_envelope(envelope)
            if topic is not None:
                observed.add(topic)
            if REQUIRED_READY_TOPICS.issubset(observed):
                return observed
    missing = ", ".join(sorted(REQUIRED_READY_TOPICS.difference(observed)))
    raise RuntimeError(f"missing Camera Hub ready topics: {missing}")


def parse_topic_envelope(message: object) -> dict[str, object]:
    if isinstance(message, bytes):
        message = message.decode("utf-8", errors="replace")
    if not isinstance(message, str):
        raise ValueError("Camera Hub message is not text JSON")
    parsed = json.loads(message)
    if not isinstance(parsed, dict):
        raise ValueError("Camera Hub topic envelope must be an object")
    return parsed


def ready_topic_from_envelope(envelope: dict[str, object]) -> str | None:
    topic = envelope.get("topic")
    if topic == CAMERA_STATUS_TOPIC and is_ready_camera_status(envelope):
        return CAMERA_STATUS_TOPIC
    if topic == SWORD_SIGN_STATE_TOPIC and is_ready_gesture_state(envelope):
        return SWORD_SIGN_STATE_TOPIC
    return None


def is_ready_camera_status(envelope: dict[str, object]) -> bool:
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return False
    if payload.get("type") != "camera_status":
        return False
    camera = payload.get("camera")
    if not isinstance(camera, dict):
        return False
    return camera.get("opened") is True and camera.get("frame_read_ok") is True


def is_ready_gesture_state(envelope: dict[str, object]) -> bool:
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return False
    if payload.get("type") != "gesture_state":
        return False
    gestures = payload.get("gestures")
    return isinstance(gestures, dict) and "sword_sign" in gestures


def connect_host(bind_host: str) -> str:
    if bind_host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return bind_host


def http_host(bind_host: str) -> str:
    host = connect_host(bind_host)
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def mediamtx_webrtc_url(rtsp_url: str) -> str:
    parsed = urlsplit(rtsp_url)
    host = parsed.hostname or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    path = parsed.path.lstrip("/") or "cam0"
    return (
        f"http://{host}:8889/{quote(path, safe='/')}"
        "?controls=false&muted=true&autoplay=true"
    )


def viewer_server_page_url(host: str, port: int) -> str:
    return f"http://{http_host(host)}:{port}/browser_camera_hub_viewer.html"


def viewer_server_health_url(host: str, port: int) -> str:
    return f"http://{http_host(host)}:{port}/healthz"


def browser_monitor_url(viewer_base_url: str, *, media_url: str, ws_url: str) -> str:
    query = urlencode(
        {
            "mediaUrl": media_url,
            "wsUrl": ws_url,
            "target": "sword_sign",
        }
    )
    separator = "&" if "?" in viewer_base_url else "?"
    return f"{viewer_base_url}{separator}{query}"


def build_ffprobe_args(ffprobe: str, rtsp_url: str) -> list[str]:
    return [
        ffprobe,
        "-rtsp_transport",
        "tcp",
        "-analyzeduration",
        "1000000",
        "-probesize",
        "32768",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_type,width,height,avg_frame_rate",
        "-of",
        "default=noprint_wrappers=1",
        rtsp_url,
    ]


def probe_rtsp_once(command: list[str], *, timeout_seconds: float) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        output = "\n".join(part for part in [result.stdout, result.stderr] if part)
        if "codec_type=video" in output:
            return True, output.strip()
        detail = output.strip() or f"ffprobe exited with code {result.returncode}"
        detail = redact_text_for_log(detail)
        return False, detail
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(
            part
            for part in [
                text_from_subprocess_output(getattr(exc, "output", None)),
                text_from_subprocess_output(getattr(exc, "stderr", None)),
            ]
            if part
        )
        if "codec_type=video" in output:
            return True, output.strip()
        detail = output.strip() or f"ffprobe timed out after {timeout_seconds:.1f}s"
        detail = redact_text_for_log(detail)
        return False, detail


def text_from_subprocess_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def stream_output(managed: ManagedProcess, log_handle) -> None:
    try:
        assert managed.process.stdout is not None
        for line in managed.process.stdout:
            text = line.rstrip()
            if text:
                print(f"[{managed.name}] {text}", flush=True)
            log_handle.write(line)
            log_handle.flush()
    finally:
        log_handle.write(f"--- exit code {managed.process.wait()} ---\n")
        log_handle.close()


def send_ffmpeg_quit(process: subprocess.Popen[str]) -> None:
    if process.stdin is None:
        return
    try:
        process.stdin.write("q\n")
        process.stdin.flush()
    except OSError:
        pass


def send_interrupt(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            os.kill(process.pid, signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
    except OSError:
        pass


def terminate_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    terminate_pid_tree(process.pid)


def kill_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    kill_pid_tree(process.pid)


def terminate_pid_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def kill_pid_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def open_browser_viewer(url: str) -> None:
    if os.name == "nt":
        os.startfile(url)  # type: ignore[attr-defined]
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, url])


def quote_for_log(value: str) -> str:
    value = redact_text_for_log(value)
    if any(char.isspace() for char in value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def redact_text_for_log(value: object) -> str:
    return URL_CREDENTIAL_RE.sub(r"\g<scheme><redacted>@", str(value))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    supervisor = StackSupervisor(args)

    def request_stop(signum, frame) -> None:  # noqa: ANN001
        supervisor.stop()
        raise SystemExit(128 + int(signum))

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if os.name == "nt" and hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_stop)

    try:
        return supervisor.run()
    except KeyboardInterrupt:
        supervisor.stop()
        return 130
    except Exception as exc:
        supervisor.stop()
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
