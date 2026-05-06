from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MEDIAMTX_PATH = Path(r"C:\Tools\mediamtx_v1.18.1_windows_amd64\mediamtx.exe")
DEFAULT_FFMPEG_PATH = Path(r"C:\Tools\ffmpeg\bin\ffmpeg.exe")
DEFAULT_FFPROBE_PATH = Path(r"C:\Tools\ffmpeg\bin\ffprobe.exe")
DEFAULT_OPENCV_FFMPEG_OPTIONS = (
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|reorder_queue_size;0"
)
STACK_PORTS = (8554, 8888, 8889, 8765)


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[str]
    log_file: Path
    critical: bool = True

    def is_running(self) -> bool:
        return self.process.poll() is None


class StackSupervisor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.repo_root = Path(__file__).resolve().parents[1]
        self.log_dir = (self.repo_root / args.log_dir).resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.processes: list[ManagedProcess] = []
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
            ports=STACK_PORTS,
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
        wait_for_websocket(
            self.args.hub_port,
            connect_host(self.args.hub_host),
            self.args.hub_wait_seconds,
            service_name="Camera Hub WebSocket",
        )

        if self.args.python_gui:
            self._start(
                "python-gui",
                [uv, "run", "python", "apps/camera_hub_gui.py"],
                critical=False,
            )

        if not self.args.no_browser:
            open_browser_viewer(self.repo_root / "apps" / "browser_camera_hub_viewer.html")

        print()
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
        log_handle.write("command: " + " ".join(quote_for_log(part) for part in command) + "\n")
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
            critical=critical,
        )
        self.processes.append(managed)
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
                    self.stop()
                    return int(managed.process.returncode or 1)
            time.sleep(0.5)
        return 0

    def _wait_until(self, deadline: float) -> None:
        while time.monotonic() < deadline:
            if all(not managed.is_running() for managed in self.processes):
                return
            time.sleep(0.1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start MediaMTX, FFmpeg camera publish, and Camera Hub in one terminal.",
    )
    parser.add_argument("--camera-name", default="HD Pro Webcam C920")
    parser.add_argument("--frame-id", default="cam0")
    parser.add_argument("--rtsp-url", default="rtsp://127.0.0.1:8554/cam0")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--bitrate", default="800k")
    parser.add_argument("--gop", type=int, default=30)
    parser.add_argument("--hub-host", default="127.0.0.1")
    parser.add_argument("--hub-port", type=int, default=8765)
    parser.add_argument("--publish-jpeg-every", type=float, default=0.0)
    parser.add_argument("--capture-interval", type=float, default=0.0)
    parser.add_argument("--gesture-every", type=float, default=0.05)
    parser.add_argument("--gesture-model-complexity", type=int, default=0)
    parser.add_argument("--release-grace-seconds", type=float, default=0.03)
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
        type=int,
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
    parser.add_argument("--wait-seconds", type=int, default=20)
    parser.add_argument("--hub-wait-seconds", type=int, default=20)
    parser.add_argument("--graceful-timeout", type=float, default=8.0)
    parser.add_argument("--skip-rtsp-wait", action="store_true")
    parser.add_argument("--force-stop-existing", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--python-gui", action="store_true")
    parser.add_argument(
        "--log-dir",
        default=r".runtime\camera-hub-stack\logs",
    )
    return parser


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
        command = process.get("command") or ""
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
        and "serve_camera_hub|camera_hub_stack|mediamtx|ffmpeg" in command
    )


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
            "'serve_camera_hub|camera_hub_stack|mediamtx|ffmpeg' } | "
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
    print(f"waiting for RTSP stream: {rtsp_url}")
    deadline = time.monotonic() + wait_seconds
    last_error = ""
    command = build_ffprobe_args(ffprobe, rtsp_url)
    while time.monotonic() < deadline:
        remaining = max(1.0, min(5.0, deadline - time.monotonic()))
        ready, detail = probe_rtsp_once(command, timeout_seconds=remaining)
        if ready:
            print("RTSP stream is ready.")
            return
        last_error = detail
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


async def probe_websocket_once(url: str) -> None:
    from websockets.asyncio.client import connect

    async with connect(url):
        return


def connect_host(bind_host: str) -> str:
    if bind_host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return bind_host


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


def open_browser_viewer(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(path)])


def quote_for_log(value: str) -> str:
    if any(char.isspace() for char in value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


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
