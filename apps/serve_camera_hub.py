from __future__ import annotations

import argparse
import asyncio
import base64
import concurrent.futures
import contextlib
import functools
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mediapipe_sword_sign import (  # noqa: E402
    GESTURE_SWORD_SIGN,
    GestureHoldTracker,
    SwordSignDetector,
    UnsafeModelError,
    gesture_state_payload,
)
from mediapipe_sword_sign.adapters import WebSocketTopicBroadcaster  # noqa: E402
from mediapipe_sword_sign.topics import (  # noqa: E402
    CAMERA_IMAGE_COMPRESSED_TOPIC,
    CAMERA_STATUS_TOPIC,
    MSG_TYPE_CAMERA_STATUS,
    MSG_TYPE_COMPRESSED_IMAGE,
    MSG_TYPE_GESTURE_STATE,
    SWORD_SIGN_STATE_TOPIC,
    binary_topic_message,
    topic_json,
)

CAMERA_BACKEND_FLAGS = {
    "auto": 0,
    "dshow": cv2.CAP_DSHOW,
    "ffmpeg": cv2.CAP_FFMPEG,
    "ffmpeg-pipe": cv2.CAP_FFMPEG,
    "msmf": cv2.CAP_MSMF,
}

DEFAULT_OPENCV_FFMPEG_CAPTURE_OPTIONS = (
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|reorder_queue_size;0"
)


def parse_port(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--port must be an integer") from exc
    if not 1 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("--port must be between 1 and 65535")
    return parsed


def parse_non_negative_int(value: str, *, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"{name} must be 0 or greater")
    return parsed


def parse_positive_int(value: str, *, name: str) -> int:
    parsed = parse_non_negative_int(value, name=name)
    if parsed == 0:
        raise argparse.ArgumentTypeError(f"{name} must be greater than 0")
    return parsed


def parse_camera_index(value: str) -> int:
    return parse_non_negative_int(value, name="--camera-index")


def parse_camera_source(value: str) -> str:
    parsed = value.strip()
    if not parsed:
        raise argparse.ArgumentTypeError("--camera-source must not be empty")
    return parsed


def parse_camera_backend(value: str) -> str:
    parsed = value.strip().lower()
    if parsed not in CAMERA_BACKEND_FLAGS:
        choices = ", ".join(sorted(CAMERA_BACKEND_FLAGS))
        raise argparse.ArgumentTypeError(
            f"--camera-backend must be one of: {choices}"
        )
    return parsed


def parse_tool_path(value: str) -> str:
    parsed = value.strip()
    if not parsed:
        raise argparse.ArgumentTypeError("tool path must not be empty")
    return parsed


def parse_timeout_ms(value: str) -> int:
    parsed = parse_non_negative_int(value, name="timeout")
    if parsed > 60000:
        raise argparse.ArgumentTypeError("timeout must be 60000ms or less")
    return parsed


def parse_ffmpeg_capture_options(value: str) -> str:
    parsed = value.strip()
    if parsed.lower() in {"none", "off", "disable", "disabled"}:
        return ""
    if not parsed:
        raise argparse.ArgumentTypeError(
            "--opencv-ffmpeg-capture-options must not be empty; use 'none' to disable"
        )
    return parsed


def parse_camera_dimension(value: str) -> int:
    return parse_positive_int(value, name="camera dimension")


def parse_camera_fps(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--camera-fps must be a number") from exc
    if not isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("--camera-fps must be greater than 0")
    return parsed


def parse_fourcc(value: str) -> str:
    parsed = value.strip().upper()
    if len(parsed) != 4 or any(ord(char) < 32 or ord(char) > 126 for char in parsed):
        raise argparse.ArgumentTypeError("--camera-fourcc must be 4 ASCII characters")
    return parsed


def parse_model_complexity(value: str) -> int:
    parsed = parse_non_negative_int(value, name="--gesture-model-complexity")
    if parsed not in {0, 1}:
        raise argparse.ArgumentTypeError("--gesture-model-complexity must be 0 or 1")
    return parsed


def parse_threshold(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("threshold must be a number") from exc
    if not isfinite(parsed) or not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("threshold must be between 0 and 1")
    return parsed


def parse_interval(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("interval must be a number") from exc
    if not isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("interval must be 0 or greater")
    return parsed


def parse_positive_interval(value: str) -> float:
    parsed = parse_interval(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("interval must be greater than 0")
    return parsed


def parse_jpeg_quality(value: str) -> int:
    parsed = parse_positive_int(value, name="--jpeg-quality")
    if parsed > 100:
        raise argparse.ArgumentTypeError("--jpeg-quality must be between 1 and 100")
    return parsed


def parse_image_transport(value: str) -> str:
    parsed = value.strip().lower()
    if parsed not in {"binary", "json"}:
        raise argparse.ArgumentTypeError("--image-transport must be binary or json")
    return parsed


def parse_max_clients(value: str) -> int:
    return parse_positive_int(value, name="--max-clients")


def parse_max_message_bytes(value: str) -> int:
    return parse_positive_int(value, name="--max-message-bytes")


def parse_max_queue(value: str) -> int:
    return parse_positive_int(value, name="--max-queue")


def resolve_auth_token(auth_token_env: str | None) -> str | None:
    if not auth_token_env:
        return None
    env_name = auth_token_env.strip()
    if not env_name:
        return None
    token = os.environ.get(env_name)
    if token is None:
        return None
    return token.strip() or None


def safe_runtime_error(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        if "executable not found" in str(exc):
            return "executable_not_found"
        return "model_not_found"
    if isinstance(exc, UnsafeModelError):
        return "unsafe_model"
    if isinstance(exc, RuntimeError) and "camera not available" in str(exc):
        return str(exc)
    if isinstance(exc, ValueError):
        return str(exc)
    return "runtime_failed"


def camera_status_payload(
    *,
    camera_index: int,
    frame_number: int,
    fps: float,
    frame_read_ok: bool = True,
    capture: dict[str, object] | None = None,
    camera_source: str | None = None,
    processors: dict[str, object] | None = None,
) -> dict[str, object]:
    camera = {
        "selected_index": camera_index,
        "opened": True,
        "frame_read_ok": bool(frame_read_ok),
    }
    if camera_source is not None:
        camera["source"] = redact_camera_source(camera_source)

    payload = {
        "type": "camera_status",
        "timestamp": time.time(),
        "camera": camera,
        "frame_id": frame_number,
        "fps": round(fps, 3),
        "processors": processors if processors is not None else {
            "sword_sign": {"enabled": True},
        },
    }
    if capture is not None:
        payload["capture"] = capture
    return payload


def encode_jpeg(frame_bgr, *, quality: int) -> bytes:
    success, encoded = cv2.imencode(
        ".jpg",
        frame_bgr,
        [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)],
    )
    if not success:
        raise RuntimeError("failed to encode camera frame")
    return encoded.tobytes()


def compressed_image_payload(frame_bgr, *, quality: int) -> dict[str, object]:
    encoded = encode_jpeg(frame_bgr, quality=quality)
    return {
        "type": "compressed_image",
        "format": "jpeg",
        "encoding": "bgr8",
        "transport": "json_base64",
        "byte_length": len(encoded),
        "data_base64": base64.b64encode(encoded).decode("ascii"),
    }


def compressed_image_binary_payload(*, byte_length: int) -> dict[str, object]:
    return {
        "type": "compressed_image",
        "format": "jpeg",
        "encoding": "bgr8",
        "transport": "binary",
        "byte_length": int(byte_length),
    }


def normalized_landmarks_payload(hand_landmarks) -> list[dict[str, float]]:
    if hand_landmarks is None:
        return []
    points = []
    for landmark in getattr(hand_landmarks, "landmark", []):
        points.append(
            {
                "x": round(float(getattr(landmark, "x", 0.0)), 6),
                "y": round(float(getattr(landmark, "y", 0.0)), 6),
                "z": round(float(getattr(landmark, "z", 0.0)), 6),
            }
        )
    return points


def fourcc_to_text(value: int) -> str:
    chars = [chr((int(value) >> (8 * index)) & 0xFF) for index in range(4)]
    if any(ord(char) < 32 or ord(char) > 126 for char in chars):
        return ""
    return "".join(chars).strip()


def redact_camera_source(source: str) -> str:
    parsed = urlsplit(source)
    if not parsed.scheme or not parsed.netloc:
        return source
    if parsed.username is None and parsed.password is None:
        return source

    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    netloc = f"<redacted>@{host}"
    return urlunsplit(
        (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
    )


class FfmpegPipeCapture:
    def __init__(
        self,
        source: str,
        *,
        width: int,
        height: int,
        fps: float | None,
        ffmpeg_path: str,
    ) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("ffmpeg-pipe backend requires camera width and height")
        self.source = source
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps or 0.0)
        self.ffmpeg_path = resolve_executable(ffmpeg_path)
        self.frame_size = self.width * self.height * 3
        self.process: subprocess.Popen[bytes] | None = None
        self._open()

    def isOpened(self) -> bool:
        process = self.process
        return process is not None and process.poll() is None

    def read(self):
        process = self.process
        if process is None or process.stdout is None or process.poll() is not None:
            return False, None
        data = read_exact(process.stdout, self.frame_size)
        if data is None:
            return False, None
        import numpy as np

        frame = np.frombuffer(data, dtype=np.uint8).reshape(
            (self.height, self.width, 3)
        )
        return True, frame.copy()

    def get(self, prop_id: int) -> float:
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self.width)
        if prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self.height)
        if prop_id == cv2.CAP_PROP_FPS:
            return float(self.fps)
        if prop_id == cv2.CAP_PROP_FOURCC:
            return float(cv2.VideoWriter_fourcc(*"BGR3"))
        return 0.0

    def set(self, prop_id: int, value: float) -> bool:
        if prop_id == cv2.CAP_PROP_FPS:
            self.fps = float(value)
        return True

    def release(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        with contextlib.suppress(Exception):
            if process.stdout is not None:
                process.stdout.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def _open(self) -> None:
        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-rtsp_transport",
            "tcp",
            "-i",
            self.source,
            "-an",
            "-sn",
            "-dn",
            "-vf",
            f"scale={self.width}:{self.height}",
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )


def read_exact(stream, size: int) -> bytes | None:
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def resolve_executable(value: str) -> str:
    path = Path(value)
    if path.exists():
        return str(path)
    found = shutil.which(value)
    if found:
        return found
    raise FileNotFoundError(f"executable not found: {value}")


def open_video_capture(
    source: int | str,
    *,
    backend: str,
    open_timeout_ms: int,
    read_timeout_ms: int,
    ffmpeg_capture_options: str | None,
    width: int | None,
    height: int | None,
    fps: float | None,
    ffmpeg_path: str,
):
    if backend == "ffmpeg-pipe":
        if not isinstance(source, str):
            raise ValueError("ffmpeg-pipe backend requires --camera-source")
        return FfmpegPipeCapture(
            source,
            width=int(width or 640),
            height=int(height or 480),
            fps=fps,
            ffmpeg_path=ffmpeg_path,
        )

    backend_flag = CAMERA_BACKEND_FLAGS[backend]
    is_url_source = isinstance(source, str) and bool(urlsplit(source).scheme)
    use_ffmpeg = backend == "ffmpeg" or (is_url_source and backend == "auto")

    if use_ffmpeg:
        if ffmpeg_capture_options:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = ffmpeg_capture_options
        else:
            os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)

    params: list[int] = []
    if use_ffmpeg:
        params.extend(
            [
                int(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC),
                int(open_timeout_ms),
                int(cv2.CAP_PROP_READ_TIMEOUT_MSEC),
                int(read_timeout_ms),
            ]
        )

    if use_ffmpeg:
        cap = cv2.VideoCapture()
        if params:
            cap.open(source, cv2.CAP_FFMPEG, params)
        else:
            cap.open(source, cv2.CAP_FFMPEG)
        return cap

    if isinstance(source, str) or backend_flag == 0:
        return cv2.VideoCapture(source)
    return cv2.VideoCapture(source, backend_flag)


@dataclass(frozen=True)
class CameraFrameSnapshot:
    frame: Any | None
    frame_number: int
    stamp: float
    fps: float
    frame_read_ok: bool
    read_latency_ms: float
    read_failures: int


class FpsTracker:
    def __init__(self) -> None:
        self.last_frame_at: float | None = None
        self.fps = 0.0

    def update(self, now: float) -> float:
        if self.last_frame_at is not None:
            elapsed = now - self.last_frame_at
            if elapsed > 0:
                self.fps = 1.0 / elapsed
        self.last_frame_at = now
        return self.fps


class LatestFrameCamera:
    def __init__(
        self,
        source: int | str,
        *,
        camera_index: int,
        interval: float,
        backend: str = "auto",
        width: int | None = None,
        height: int | None = None,
        fps: float | None = None,
        fourcc: str | None = None,
        open_timeout_ms: int = 3000,
        read_timeout_ms: int = 1000,
        ffmpeg_capture_options: str | None = None,
        ffmpeg_path: str = "ffmpeg",
    ) -> None:
        self.source = source
        self.camera_index = camera_index
        self.interval = interval
        self.backend = backend
        self.ffmpeg_capture_options = ffmpeg_capture_options
        self.cap = open_video_capture(
            source,
            backend=backend,
            open_timeout_ms=open_timeout_ms,
            read_timeout_ms=read_timeout_ms,
            ffmpeg_capture_options=ffmpeg_capture_options,
            width=width,
            height=height,
            fps=fps,
            ffmpeg_path=ffmpeg_path,
        )
        self._configure_capture(width=width, height=height, fps=fps, fourcc=fourcc)
        self._fps_tracker = FpsTracker()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame = None
        self._frame_number = 0
        self._stamp = time.time()
        self._fps = 0.0
        self._frame_read_ok = False
        self._read_latency_ms = 0.0
        self._read_failures = 0

    def actual_properties(self) -> dict[str, object]:
        return {
            "source": redact_camera_source(str(self.source)),
            "backend": self.backend,
            "ffmpeg_capture_options": self.ffmpeg_capture_options or "",
            "width": int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
            "height": int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
            "fps": round(float(self.cap.get(cv2.CAP_PROP_FPS) or 0.0), 3),
            "fourcc": fourcc_to_text(int(self.cap.get(cv2.CAP_PROP_FOURCC) or 0)),
        }

    def start(self) -> None:
        if not self.cap.isOpened():
            source = redact_camera_source(str(self.source))
            raise RuntimeError(f"camera not available: {source}")
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="camera-hub-capture",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        self.cap.release()

    def snapshot(self, *, copy_frame: bool = True) -> CameraFrameSnapshot:
        with self._lock:
            frame = self._frame
            if copy_frame and frame is not None:
                frame = frame.copy()
            return CameraFrameSnapshot(
                frame=frame,
                frame_number=self._frame_number,
                stamp=self._stamp,
                fps=self._fps,
                frame_read_ok=self._frame_read_ok,
                read_latency_ms=self._read_latency_ms,
                read_failures=self._read_failures,
            )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            read_started = time.monotonic()
            success, frame = self.cap.read()
            read_latency_ms = (time.monotonic() - read_started) * 1000.0
            stamp = time.time()
            now = time.monotonic()
            with self._lock:
                self._frame_read_ok = bool(success)
                self._read_latency_ms = read_latency_ms
                if success:
                    self._stamp = stamp
                    self._frame_number += 1
                    self._frame = frame
                    self._fps = self._fps_tracker.update(now)
                else:
                    self._read_failures += 1

            if success:
                if self.interval > 0:
                    time.sleep(self.interval)
            else:
                time.sleep(0.05)

    def _configure_capture(
        self,
        *,
        width: int | None,
        height: int | None,
        fps: float | None,
        fourcc: str | None,
    ) -> None:
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if fourcc:
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        if width is not None:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        if height is not None:
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if fps is not None:
            self.cap.set(cv2.CAP_PROP_FPS, fps)


class SwordSignInferenceRunner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.detector: SwordSignDetector | None = None
        self.hold_tracker = GestureHoldTracker(
            target=GESTURE_SWORD_SIGN,
            hold_seconds=args.hold_seconds,
            release_grace_seconds=args.release_grace_seconds,
        )

    def detect(self, frame_bgr, *, stamp: float):
        detector = self._detector()
        result = detector.detect_frame(frame_bgr, flip=True, timestamp=stamp)
        stable = self.hold_tracker.update(result.state)
        landmarks = (
            normalized_landmarks_payload(result.hand_landmarks)
            if self.args.publish_landmarks
            else None
        )
        return result.state, stable, landmarks

    def close(self) -> None:
        if self.detector is not None:
            self.detector.__exit__(None, None, None)
            self.detector = None

    def _detector(self) -> SwordSignDetector:
        if self.detector is None:
            self.detector = SwordSignDetector(
                model_path=self.args.gesture_model_path,
                expected_model_sha256=self.args.gesture_model_sha256,
                allow_untrusted_model=self.args.allow_untrusted_gesture_model,
                threshold=self.args.gesture_threshold,
                model_complexity=self.args.gesture_model_complexity,
            )
            self.detector.__enter__()
        return self.detector


def due(last_published_at: float | None, interval: float, now: float) -> bool:
    return interval > 0 and (
        last_published_at is None or now - last_published_at >= interval
    )


def capture_status_properties(
    camera: LatestFrameCamera,
    snapshot: CameraFrameSnapshot,
    *,
    now: float | None = None,
) -> dict[str, object]:
    timestamp = finite_non_negative_float(time.time() if now is None else now)
    stamp = finite_non_negative_float(snapshot.stamp, default=timestamp)
    capture = camera.actual_properties()
    capture.update(
        {
            "frame_age_ms": round(max(0.0, timestamp - stamp) * 1000.0, 3),
            "read_latency_ms": round(
                finite_non_negative_float(snapshot.read_latency_ms),
                3,
            ),
            "read_failures": finite_non_negative_int(snapshot.read_failures),
            "read_fps": round(finite_non_negative_float(snapshot.fps), 3),
        }
    )
    return capture


def finite_non_negative_float(value: object, *, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not isfinite(parsed) or parsed < 0:
        return default
    return parsed


def finite_non_negative_int(value: object, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if parsed < 0:
        return default
    return parsed


def copy_processor_metrics(
    metrics: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    return {name: dict(values) for name, values in metrics.items()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a local camera hub and publish vision topics over WebSocket.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=parse_port, default=8765)
    parser.add_argument("--camera-index", type=parse_camera_index, default=0)
    parser.add_argument(
        "--camera-source",
        type=parse_camera_source,
        help=(
            "OpenCV VideoCapture source URL/path, for example "
            "rtsp://127.0.0.1:8554/cam0. When set, this overrides --camera-index "
            "for capture and is intended for MediaMTX/go2rtc integration."
        ),
    )
    parser.add_argument(
        "--camera-backend",
        type=parse_camera_backend,
        default="auto",
        help=(
            "Camera backend. Use ffmpeg-pipe for low-latency RTSP sources, "
            "or ffmpeg to use OpenCV's FFmpeg VideoCapture."
        ),
    )
    parser.add_argument(
        "--opencv-ffmpeg-capture-options",
        type=parse_ffmpeg_capture_options,
        default=DEFAULT_OPENCV_FFMPEG_CAPTURE_OPTIONS,
        help=(
            "Options passed through OPENCV_FFMPEG_CAPTURE_OPTIONS before opening "
            "FFmpeg-backed sources. Use 'none' if the local OpenCV build rejects "
            "an option."
        ),
    )
    parser.add_argument(
        "--ffmpeg-path",
        type=parse_tool_path,
        default="ffmpeg",
        help="ffmpeg executable used by --camera-backend ffmpeg-pipe.",
    )
    parser.add_argument(
        "--camera-open-timeout-ms",
        type=parse_timeout_ms,
        default=3000,
        help="Open timeout for FFmpeg/GStreamer VideoCapture sources.",
    )
    parser.add_argument(
        "--camera-read-timeout-ms",
        type=parse_timeout_ms,
        default=3000,
        help="Read timeout for FFmpeg/GStreamer VideoCapture sources.",
    )
    parser.add_argument("--camera-width", type=parse_camera_dimension)
    parser.add_argument("--camera-height", type=parse_camera_dimension)
    parser.add_argument("--camera-fps", type=parse_camera_fps)
    parser.add_argument(
        "--camera-fourcc",
        type=parse_fourcc,
        help="Requested camera input FOURCC, for example MJPG.",
    )
    parser.add_argument("--frame-id", default="camera")
    parser.add_argument(
        "--interval",
        type=parse_interval,
        default=1 / 30,
        help=(
            "Camera capture interval. Use 0 for no extra capture-loop sleep, "
            "0.05 for about 20fps input, 0.033 for 30fps."
        ),
    )
    parser.add_argument("--status-every", type=parse_interval, default=2.0)
    parser.add_argument("--publish-jpeg-every", type=parse_interval, default=0.0)
    parser.add_argument("--jpeg-quality", type=parse_jpeg_quality, default=70)
    parser.add_argument(
        "--image-transport",
        type=parse_image_transport,
        default="binary",
        help="Image topic transport: binary WebSocket frame or JSON/base64 fallback.",
    )
    parser.add_argument("--gesture-threshold", type=parse_threshold, default=0.9)
    parser.add_argument(
        "--gesture-model-complexity",
        type=parse_model_complexity,
        default=1,
        help="MediaPipe Hands model_complexity. Use 0 to reduce CPU at some accuracy cost.",
    )
    parser.add_argument("--gesture-model-path")
    parser.add_argument("--gesture-model-sha256")
    parser.add_argument("--allow-untrusted-gesture-model", action="store_true")
    parser.add_argument(
        "--publish-landmarks",
        action="store_true",
        help=(
            "Include normalized MediaPipe hand landmarks in gesture topic payloads "
            "for browser debug overlays."
        ),
    )
    parser.add_argument(
        "--gesture-every",
        type=parse_interval,
        default=0.0,
        help="Minimum seconds between gesture inference runs. 0 means infer each newest frame as fast as the detector allows.",
    )
    parser.add_argument("--hold-seconds", type=parse_interval, default=0.5)
    parser.add_argument("--release-grace-seconds", type=parse_interval, default=0.1)
    parser.add_argument("--auth-token-env", default="CAMERA_HUB_WS_TOKEN")
    parser.add_argument("--allowed-origin", action="append", dest="allowed_origins")
    parser.add_argument(
        "--max-clients",
        type=parse_max_clients,
        default=8,
    )
    parser.add_argument("--max-message-bytes", type=parse_max_message_bytes, default=4096)
    parser.add_argument("--max-queue", type=parse_max_queue, default=4)
    parser.add_argument("--allow-remote-unauthenticated", action="store_true")
    return parser


async def publish_status_loop(
    *,
    camera: LatestFrameCamera,
    broadcaster: WebSocketTopicBroadcaster,
    args: argparse.Namespace,
    processor_metrics: dict[str, dict[str, object]],
    processor_metrics_lock: asyncio.Lock,
) -> None:
    while True:
        snapshot = camera.snapshot(copy_frame=False)
        async with processor_metrics_lock:
            processors = copy_processor_metrics(processor_metrics)
        await broadcaster.publish_message(
            topic_json(
                CAMERA_STATUS_TOPIC,
                MSG_TYPE_CAMERA_STATUS,
                camera_status_payload(
                    camera_index=args.camera_index,
                    frame_number=snapshot.frame_number,
                    fps=snapshot.fps,
                    frame_read_ok=snapshot.frame_read_ok,
                    capture=capture_status_properties(camera, snapshot),
                    camera_source=(
                        args.camera_source if args.camera_source is not None else None
                    ),
                    processors=processors,
                ),
                sequence=snapshot.frame_number,
                stamp=snapshot.stamp,
                frame_id=args.frame_id,
            )
        )
        await asyncio.sleep(args.status_every)


async def publish_image_loop(
    *,
    camera: LatestFrameCamera,
    broadcaster: WebSocketTopicBroadcaster,
    args: argparse.Namespace,
) -> None:
    last_published_frame = 0
    next_publish_at = time.monotonic()
    while True:
        now = time.monotonic()
        if now < next_publish_at:
            await asyncio.sleep(next_publish_at - now)
        next_publish_at = max(next_publish_at + args.publish_jpeg_every, time.monotonic())

        snapshot = camera.snapshot(copy_frame=True)
        if (
            snapshot.frame is None
            or not snapshot.frame_read_ok
            or snapshot.frame_number == last_published_frame
        ):
            await asyncio.sleep(0.005)
            continue

        if args.image_transport == "json":
            image_message = await asyncio.to_thread(
                lambda: topic_json(
                    CAMERA_IMAGE_COMPRESSED_TOPIC,
                    MSG_TYPE_COMPRESSED_IMAGE,
                    compressed_image_payload(snapshot.frame, quality=args.jpeg_quality),
                    sequence=snapshot.frame_number,
                    stamp=snapshot.stamp,
                    frame_id=args.frame_id,
                )
            )
        else:
            jpeg = await asyncio.to_thread(
                encode_jpeg,
                snapshot.frame,
                quality=args.jpeg_quality,
            )
            image_message = binary_topic_message(
                CAMERA_IMAGE_COMPRESSED_TOPIC,
                MSG_TYPE_COMPRESSED_IMAGE,
                compressed_image_binary_payload(byte_length=len(jpeg)),
                jpeg,
                sequence=snapshot.frame_number,
                stamp=snapshot.stamp,
                frame_id=args.frame_id,
            )

        await broadcaster.publish_message(image_message)
        last_published_frame = snapshot.frame_number


async def publish_gesture_loop(
    *,
    camera: LatestFrameCamera,
    broadcaster: WebSocketTopicBroadcaster,
    args: argparse.Namespace,
    processor_metrics: dict[str, dict[str, object]],
    processor_metrics_lock: asyncio.Lock,
) -> None:
    loop = asyncio.get_running_loop()
    runner = SwordSignInferenceRunner(args)
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="camera-hub-gesture",
    )
    last_inferred_frame = 0
    last_inferred_at: float | None = None
    try:
        while True:
            if last_inferred_at is not None and args.gesture_every > 0:
                remaining = args.gesture_every - (time.monotonic() - last_inferred_at)
                if remaining > 0:
                    await asyncio.sleep(min(remaining, 0.02))
                    continue

            snapshot = camera.snapshot(copy_frame=True)
            if (
                snapshot.frame is None
                or not snapshot.frame_read_ok
                or snapshot.frame_number == last_inferred_frame
            ):
                await asyncio.sleep(0.005)
                continue

            inference_started = time.monotonic()
            gesture_state, stable, landmarks = await loop.run_in_executor(
                executor,
                functools.partial(
                    runner.detect,
                    snapshot.frame,
                    stamp=snapshot.stamp,
                ),
            )
            inference_ms = (time.monotonic() - inference_started) * 1000.0
            payload = gesture_state_payload(
                gesture_state,
                sequence=snapshot.frame_number,
                stable=stable,
            )
            if landmarks is not None:
                payload["landmarks"] = {
                    "type": "mediapipe_hand_landmarks",
                    "coordinate_space": "normalized_input_frame_mirrored",
                    "points": landmarks,
                }
            await broadcaster.publish_message(
                topic_json(
                    SWORD_SIGN_STATE_TOPIC,
                    MSG_TYPE_GESTURE_STATE,
                    payload,
                    sequence=snapshot.frame_number,
                    stamp=snapshot.stamp,
                    frame_id=args.frame_id,
                )
            )
            publish_age_ms = max(0.0, time.time() - snapshot.stamp) * 1000.0
            async with processor_metrics_lock:
                processor_metrics["sword_sign"] = {
                    "enabled": True,
                    "last_frame_id": snapshot.frame_number,
                    "inference_ms": round(inference_ms, 3),
                    "publish_age_ms": round(publish_age_ms, 3),
                    "last_published_at": round(time.time(), 6),
                }
            last_inferred_frame = snapshot.frame_number
            last_inferred_at = time.monotonic()
    finally:
        await loop.run_in_executor(executor, runner.close)
        executor.shutdown(wait=True)


async def run(args: argparse.Namespace) -> None:
    auth_token = resolve_auth_token(args.auth_token_env)
    broadcaster = WebSocketTopicBroadcaster(
        args.host,
        args.port,
        auth_token=auth_token,
        allowed_origins=args.allowed_origins,
        max_clients=args.max_clients,
        max_message_bytes=args.max_message_bytes,
        max_queue=args.max_queue,
        allow_remote_unauthenticated=args.allow_remote_unauthenticated,
    )
    camera_source: int | str = (
        args.camera_source if args.camera_source is not None else args.camera_index
    )
    camera = LatestFrameCamera(
        camera_source,
        camera_index=args.camera_index,
        interval=args.interval,
        backend=args.camera_backend,
        width=args.camera_width,
        height=args.camera_height,
        fps=args.camera_fps,
        fourcc=args.camera_fourcc,
        open_timeout_ms=args.camera_open_timeout_ms,
        read_timeout_ms=args.camera_read_timeout_ms,
        ffmpeg_capture_options=args.opencv_ffmpeg_capture_options,
        ffmpeg_path=args.ffmpeg_path,
    )
    tasks: list[asyncio.Task[None]] = []
    processor_metrics: dict[str, dict[str, object]] = {
        "sword_sign": {"enabled": True},
    }
    processor_metrics_lock = asyncio.Lock()

    try:
        camera.start()
        async with broadcaster:
            print(f"Camera hub listening on ws://{args.host}:{args.port}")
            print(f"topics: {SWORD_SIGN_STATE_TOPIC}, {CAMERA_STATUS_TOPIC}")
            print(f"camera: {camera.actual_properties()}")
            if args.publish_jpeg_every > 0:
                print(
                    f"image topic: {CAMERA_IMAGE_COMPRESSED_TOPIC} "
                    f"({args.image_transport})"
                )
            print(
                "pipeline: capture thread, gesture inference worker, "
                "independent status/image publishers"
            )

            tasks.append(
                asyncio.create_task(
                    publish_gesture_loop(
                        camera=camera,
                        broadcaster=broadcaster,
                        args=args,
                        processor_metrics=processor_metrics,
                        processor_metrics_lock=processor_metrics_lock,
                    )
                )
            )
            if args.status_every > 0:
                tasks.append(
                    asyncio.create_task(
                        publish_status_loop(
                            camera=camera,
                            broadcaster=broadcaster,
                            args=args,
                            processor_metrics=processor_metrics,
                            processor_metrics_lock=processor_metrics_lock,
                        )
                    )
                )
            if args.publish_jpeg_every > 0:
                tasks.append(
                    asyncio.create_task(
                        publish_image_loop(
                            camera=camera,
                            broadcaster=broadcaster,
                            args=args,
                        )
                    )
                )

            await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        camera.stop()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        parser.exit(1, f"error: {safe_runtime_error(exc)}\n")


if __name__ == "__main__":
    main()
