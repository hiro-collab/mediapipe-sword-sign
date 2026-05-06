from __future__ import annotations

import asyncio
import base64
import copy
import json
import queue
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mediapipe_sword_sign.topics import (  # noqa: E402
    CAMERA_IMAGE_COMPRESSED_TOPIC,
    CAMERA_STATUS_TOPIC,
    SWORD_SIGN_STATE_TOPIC,
    parse_binary_topic_message,
)
from mediapipe_sword_sign.types import DISPLAY_NAMES, GESTURE_SWORD_SIGN  # noqa: E402


PREVIEW_WINDOW = "Camera Hub Preview"
EVENT_QUEUE_MAXSIZE = 256
MAX_EVENTS_PER_TICK = 50
TICK_INTERVAL_MS = 30
PREVIEW_MAX_FPS = 120.0
JSON_VIEW_MAX_FPS = 5.0


@dataclass(frozen=True)
class GestureSummary:
    primary: str
    best_name: str
    best_confidence: float
    target_active: bool
    target_confidence: float
    stable_active: bool | None
    stable_held_for: float | None
    stable_activated: bool
    stable_released: bool


@dataclass(frozen=True)
class CameraStatusSummary:
    camera_index: int | None
    opened: bool | None
    frame_read_ok: bool | None
    fps: float | None
    frame_id: int | None
    frame_age_ms: float | None
    read_latency_ms: float | None
    read_failures: int | None
    gesture_inference_ms: float | None
    gesture_publish_age_ms: float | None


class HubTopicRuntime:
    def __init__(self) -> None:
        self.events: queue.Queue[tuple[str, object]] = queue.Queue(
            maxsize=EVENT_QUEUE_MAXSIZE,
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._latest_frame_envelope: dict[str, Any] | None = None
        self._latest_frame_counter = 0
        self._last_taken_frame_counter = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, *, url: str, auth_token: str | None) -> None:
        self.stop()
        self._stop_event.clear()
        self._reset_events()
        self.clear_latest_frame()
        self._thread = threading.Thread(
            target=self._run_thread,
            args=(url, auth_token),
            name="camera-hub-gui-websocket",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def take_latest_frame(self):
        with self._frame_lock:
            if self._latest_frame_counter == self._last_taken_frame_counter:
                return None
            if self._latest_frame is None or self._latest_frame_envelope is None:
                return None
            self._last_taken_frame_counter = self._latest_frame_counter
            return self._latest_frame_envelope, self._latest_frame

    def clear_latest_frame(self) -> None:
        with self._frame_lock:
            self._latest_frame = None
            self._latest_frame_envelope = None
            self._latest_frame_counter = 0
            self._last_taken_frame_counter = 0

    def _reset_events(self) -> None:
        self.events = queue.Queue(maxsize=EVENT_QUEUE_MAXSIZE)

    def _put_event(self, kind: str, value: object) -> None:
        while True:
            try:
                self.events.put_nowait((kind, value))
                return
            except queue.Full:
                try:
                    self.events.get_nowait()
                except queue.Empty:
                    return

    def _run_thread(self, url: str, auth_token: str | None) -> None:
        try:
            asyncio.run(self._listen(url, auth_token))
        except Exception as exc:
            self._put_event("error", safe_error_text(exc))
        finally:
            self._put_event("disconnected", None)

    async def _listen(self, url: str, auth_token: str | None) -> None:
        connect, header_arg = _load_connect()
        options: dict[str, object] = {}
        if auth_token:
            options[header_arg] = [("Authorization", f"Bearer {auth_token}")]

        self._put_event("connecting", url)
        while not self._stop_event.is_set():
            try:
                async with connect(url, **options) as websocket:
                    self._put_event("connected", url)
                    while not self._stop_event.is_set():
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=0.25)
                        except TimeoutError:
                            continue
                        self._handle_received_message(message)
            except Exception as exc:
                if self._stop_event.is_set():
                    return
                self._put_event("error", safe_error_text(exc))
                await asyncio.sleep(1.0)
                self._put_event("connecting", url)

    def _handle_received_message(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            self._handle_binary_message(message)
            return

        try:
            envelope = json.loads(message)
        except json.JSONDecodeError as exc:
            self._put_event("error", f"bad json: {exc.msg}")
            return
        if not isinstance(envelope, dict):
            self._put_event("error", "topic envelope must be an object")
            return

        if str(envelope.get("topic", "")) == CAMERA_IMAGE_COMPRESSED_TOPIC:
            self._handle_image_envelope(envelope, None)
            return

        self._put_event("message", envelope)

    def _handle_binary_message(self, message: bytes) -> None:
        try:
            envelope, data = parse_binary_topic_message(message)
        except ValueError as exc:
            self._put_event("error", str(exc))
            return

        if str(envelope.get("topic", "")) == CAMERA_IMAGE_COMPRESSED_TOPIC:
            self._handle_image_envelope(envelope, data)
            return

        self._put_event("message", envelope)

    def _handle_image_envelope(
        self,
        envelope: dict[str, Any],
        data: bytes | None,
    ) -> None:
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            self._put_event("error", "image topic payload must be an object")
            return

        try:
            if data is None:
                frame = decode_compressed_image(payload)
            else:
                frame = decode_binary_compressed_image(payload, data)
        except ValueError as exc:
            self._put_event("error", str(exc))
            return

        compact = compact_envelope_for_display(envelope)
        with self._frame_lock:
            self._latest_frame = frame
            self._latest_frame_envelope = compact
            self._latest_frame_counter += 1


class CameraHubGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MediaPipe Camera Hub Monitor")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.hub_url = tk.StringVar(value="ws://127.0.0.1:8765")
        self.auth_token = tk.StringVar(value="")
        self.target_gesture = tk.StringVar(value=GESTURE_SWORD_SIGN)
        self.show_preview = tk.BooleanVar(value=True)
        self.mirror_preview = tk.BooleanVar(value=True)
        self.show_overlay = tk.BooleanVar(value=True)

        self.runtime = HubTopicRuntime()
        self.latest_gesture: GestureSummary | None = None
        self.latest_status: CameraStatusSummary | None = None
        self.latest_frame = None
        self.latest_topic = "-"
        self._last_gesture_active: bool | None = None
        self._last_preview_at = 0.0
        self._last_json_view_at = 0.0
        self._preview_dirty = False

        self.connection_state = tk.StringVar(value="Disconnected")
        self.camera_state = tk.StringVar(value="-")
        self.frame_state = tk.StringVar(value="-")
        self.fps = tk.StringVar(value="-")
        self.frame_age = tk.StringVar(value="-")
        self.read_latency = tk.StringVar(value="-")
        self.read_failures = tk.StringVar(value="-")
        self.gesture_inference = tk.StringVar(value="-")
        self.gesture_publish_age = tk.StringVar(value="-")
        self.primary_gesture = tk.StringVar(value="-")
        self.best_gesture = tk.StringVar(value="-")
        self.target_state = tk.StringVar(value="-")
        self.stable_state = tk.StringVar(value="-")
        self.held_for = tk.StringVar(value="-")
        self.last_topic = tk.StringVar(value="-")
        self.last_message_at = tk.StringVar(value="-")
        self.topic_age = tk.StringVar(value="-")
        self.last_error = tk.StringVar(value="-")

        self._build()
        self._tick()

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)

        settings = ttk.LabelFrame(self.root, text="Hub", padding=12)
        settings.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="URL").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(settings, textvariable=self.hub_url).grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(settings, text="Token").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(settings, textvariable=self.auth_token, show="*").grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(settings, text="Target").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(settings, textvariable=self.target_gesture, width=20).grid(row=2, column=1, sticky="w", pady=4)

        toggles = ttk.Frame(settings)
        toggles.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(toggles, text="Preview", variable=self.show_preview).grid(row=0, column=0, padx=(0, 12))
        ttk.Checkbutton(toggles, text="Mirror", variable=self.mirror_preview).grid(row=0, column=1, padx=(0, 12))
        ttk.Checkbutton(toggles, text="Overlay", variable=self.show_overlay).grid(row=0, column=2)

        controls = ttk.Frame(self.root, padding=(12, 0, 12, 0))
        controls.grid(row=1, column=0, sticky="ew")
        ttk.Button(controls, text="Connect", command=self.connect).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(controls, text="Disconnect", command=self.disconnect).grid(row=0, column=1)

        state = ttk.LabelFrame(self.root, text="State", padding=12)
        state.grid(row=2, column=0, sticky="ew", padx=12, pady=12)
        state.columnconfigure(1, weight=1)
        rows = [
            ("Connection", self.connection_state),
            ("Camera", self.camera_state),
            ("Frame", self.frame_state),
            ("FPS", self.fps),
            ("Frame Age", self.frame_age),
            ("Read Latency", self.read_latency),
            ("Read Failures", self.read_failures),
            ("Gesture Inference", self.gesture_inference),
            ("Gesture Publish Age", self.gesture_publish_age),
            ("Primary", self.primary_gesture),
            ("Best", self.best_gesture),
            ("Target", self.target_state),
            ("Stable", self.stable_state),
            ("Held For", self.held_for),
            ("Last Topic", self.last_topic),
            ("Last Message", self.last_message_at),
            ("Topic Age", self.topic_age),
            ("Last Error", self.last_error),
        ]
        for row, (label, value) in enumerate(rows):
            ttk.Label(state, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Label(state, textvariable=value).grid(row=row, column=1, sticky="w", padx=(12, 0), pady=2)

        details = ttk.Frame(self.root, padding=(12, 0, 12, 12))
        details.grid(row=3, column=0, sticky="nsew")
        details.columnconfigure(0, weight=1)
        ttk.Label(details, text="Event Log").grid(row=0, column=0, sticky="w")
        self.event_log = tk.Listbox(details, height=8)
        self.event_log.grid(row=1, column=0, sticky="nsew", pady=(4, 12))
        ttk.Label(details, text="Last Topic JSON").grid(row=2, column=0, sticky="w")
        self.last_json = tk.Text(details, height=10, wrap="none")
        self.last_json.grid(row=3, column=0, sticky="nsew", pady=(4, 0))

        self.root.rowconfigure(3, weight=1)

    def connect(self) -> None:
        url = self.hub_url.get().strip()
        if not url:
            messagebox.showerror("Connect failed", "URL is required.")
            return
        self.last_error.set("-")
        self.latest_frame = None
        self._last_preview_at = 0.0
        self._preview_dirty = False
        self.runtime.start(
            url=url,
            auth_token=self.auth_token.get().strip() or None,
        )

    def disconnect(self) -> None:
        self.runtime.stop()
        self.runtime.clear_latest_frame()
        self.latest_frame = None
        self._preview_dirty = False
        self.connection_state.set("Disconnected")
        self._destroy_preview()

    def close(self) -> None:
        self.disconnect()
        self.root.destroy()

    def _tick(self) -> None:
        self._drain_events()
        self._apply_latest_frame()
        if self.show_preview.get() and self.latest_frame is not None and self._preview_dirty:
            now = time.monotonic()
            if now - self._last_preview_at >= 1.0 / PREVIEW_MAX_FPS:
                self._show_preview()
                self._last_preview_at = now
                self._preview_dirty = False
        elif not self.show_preview.get():
            self._destroy_preview()
        self.root.after(TICK_INTERVAL_MS, self._tick)

    def _drain_events(self) -> None:
        for _ in range(MAX_EVENTS_PER_TICK):
            try:
                kind, value = self.runtime.events.get_nowait()
            except queue.Empty:
                return
            if kind == "connecting":
                self.connection_state.set(f"Connecting {value}")
                self._append_event("connecting")
            elif kind == "connected":
                self.connection_state.set(f"Connected {value}")
                self._append_event("connected")
            elif kind == "disconnected":
                if not self.runtime.running:
                    self.connection_state.set("Disconnected")
                self._append_event("disconnected")
            elif kind == "error":
                self.last_error.set(str(value))
                self.connection_state.set("Error")
                self._append_event(f"error: {value}")
            elif kind == "message":
                if isinstance(value, dict):
                    self._handle_envelope(value)
                else:
                    self._handle_message(str(value))

    def _handle_message(self, message: str) -> None:
        try:
            envelope = json.loads(message)
        except json.JSONDecodeError as exc:
            self.last_error.set(f"bad json: {exc.msg}")
            return

        topic = str(envelope.get("topic", ""))
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            self.last_error.set("topic payload must be an object")
            return

        self._handle_envelope(envelope)

    def _handle_envelope(self, envelope: dict[str, Any]) -> None:
        topic = str(envelope.get("topic", ""))
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            self.last_error.set("topic payload must be an object")
            return

        self.latest_topic = topic or "-"
        self.last_topic.set(self.latest_topic)
        self.last_message_at.set(datetime.now().strftime("%H:%M:%S.%f")[:-3])
        self._set_topic_age(envelope)
        self._maybe_set_last_json(envelope)

        try:
            if topic == SWORD_SIGN_STATE_TOPIC:
                self._apply_gesture(payload)
            elif topic == CAMERA_STATUS_TOPIC:
                self._apply_status(payload)
            elif topic == CAMERA_IMAGE_COMPRESSED_TOPIC:
                self._append_event("image topic received")
        except ValueError as exc:
            self.last_error.set(str(exc))

    def _apply_latest_frame(self) -> None:
        update = self.runtime.take_latest_frame()
        if update is None:
            return
        envelope, frame = update
        self.latest_frame = frame
        self._preview_dirty = True
        self.latest_topic = str(envelope.get("topic", "")) or "-"
        self.last_topic.set(self.latest_topic)
        self.last_message_at.set(datetime.now().strftime("%H:%M:%S.%f")[:-3])
        self._set_topic_age(envelope)
        self._maybe_set_last_json(envelope)

    def _apply_gesture(self, payload: dict[str, Any]) -> None:
        summary = summarize_gesture_payload(
            payload,
            target=self.target_gesture.get().strip() or GESTURE_SWORD_SIGN,
        )
        self.latest_gesture = summary

        primary = DISPLAY_NAMES.get(summary.primary, summary.primary)
        best = DISPLAY_NAMES.get(summary.best_name, summary.best_name)
        self.primary_gesture.set(primary)
        self.best_gesture.set(f"{best} ({summary.best_confidence:.3f})")
        self.target_state.set(
            f"{'active' if summary.target_active else 'inactive'} "
            f"({summary.target_confidence:.3f})"
        )
        if summary.stable_active is None:
            self.stable_state.set("-")
            self.held_for.set("-")
        else:
            self.stable_state.set("active" if summary.stable_active else "inactive")
            self.held_for.set(f"{(summary.stable_held_for or 0.0):.2f}s")

        if self._last_gesture_active is None:
            self._last_gesture_active = summary.stable_active
        elif summary.stable_active is not None and self._last_gesture_active != summary.stable_active:
            self._last_gesture_active = summary.stable_active
            self._append_event(
                f"stable {'active' if summary.stable_active else 'inactive'}"
            )
        if summary.stable_activated:
            self._append_event("stable activated")
        if summary.stable_released:
            self._append_event("stable released")

    def _apply_status(self, payload: dict[str, Any]) -> None:
        summary = summarize_camera_status(payload)
        self.latest_status = summary

        index = "-" if summary.camera_index is None else str(summary.camera_index)
        opened = "-" if summary.opened is None else ("open" if summary.opened else "closed")
        read_ok = "-" if summary.frame_read_ok is None else ("read" if summary.frame_read_ok else "read failed")
        self.camera_state.set(f"{index} / {opened} / {read_ok}")
        self.frame_state.set("-" if summary.frame_id is None else str(summary.frame_id))
        self.fps.set("-" if summary.fps is None else f"{summary.fps:.1f}")
        self.frame_age.set(format_ms(summary.frame_age_ms))
        self.read_latency.set(format_ms(summary.read_latency_ms))
        self.read_failures.set(
            "-" if summary.read_failures is None else str(summary.read_failures)
        )
        self.gesture_inference.set(format_ms(summary.gesture_inference_ms))
        self.gesture_publish_age.set(format_ms(summary.gesture_publish_age_ms))

    def _show_preview(self) -> None:
        frame = self.latest_frame
        if frame is None:
            return
        display = cv2.flip(frame, 1) if self.mirror_preview.get() else frame.copy()
        if self.show_overlay.get():
            draw_preview_overlay(
                display,
                gesture=self.latest_gesture,
                status=self.latest_status,
                topic=self.latest_topic,
            )
        cv2.imshow(PREVIEW_WINDOW, display)
        if cv2.waitKey(1) & 0xFF == 27:
            self.disconnect()

    def _destroy_preview(self) -> None:
        try:
            cv2.destroyWindow(PREVIEW_WINDOW)
        except cv2.error:
            pass

    def _set_last_json(self, envelope: dict[str, Any]) -> None:
        self.last_json.delete("1.0", tk.END)
        self.last_json.insert(
            tk.END,
            json.dumps(envelope, ensure_ascii=False, indent=2),
        )

    def _maybe_set_last_json(self, envelope: dict[str, Any]) -> None:
        now = time.monotonic()
        if now - self._last_json_view_at < 1.0 / JSON_VIEW_MAX_FPS:
            return
        self._set_last_json(compact_envelope_for_display(envelope))
        self._last_json_view_at = now

    def _set_topic_age(self, envelope: dict[str, Any]) -> None:
        age = envelope_age_seconds(envelope)
        self.topic_age.set("-" if age is None else f"{age:.3f}s")

    def _append_event(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.event_log.insert(tk.END, f"{timestamp} {message}")
        if self.event_log.size() > 200:
            self.event_log.delete(0)
        self.event_log.see(tk.END)


def summarize_gesture_payload(
    payload: dict[str, Any],
    *,
    target: str = GESTURE_SWORD_SIGN,
) -> GestureSummary:
    gestures = payload.get("gestures")
    if not isinstance(gestures, dict):
        raise ValueError("gesture payload must contain gestures")

    primary = str(payload.get("primary") or "none")
    best_name = "none"
    best_confidence = 0.0
    for name, value in gestures.items():
        if not isinstance(value, dict):
            continue
        confidence = _float(value.get("confidence"), default=0.0)
        if confidence >= best_confidence:
            best_name = str(name)
            best_confidence = confidence

    target_payload = gestures.get(target)
    if not isinstance(target_payload, dict):
        target_payload = {}
    target_active = bool(target_payload.get("active", False))
    target_confidence = _float(target_payload.get("confidence"), default=0.0)

    stable_payload = (
        payload.get("stable", {})
        if isinstance(payload.get("stable"), dict)
        else {}
    )
    stable_gestures = (
        stable_payload.get("gestures", {})
        if isinstance(stable_payload.get("gestures"), dict)
        else {}
    )
    stable_target = stable_gestures.get(target)
    stable_active: bool | None = None
    stable_held_for: float | None = None
    stable_activated = False
    stable_released = False
    if isinstance(stable_target, dict):
        stable_active = bool(stable_target.get("active", False))
        stable_held_for = _float(stable_target.get("held_for"), default=0.0)
        stable_activated = bool(stable_target.get("activated", False))
        stable_released = bool(stable_target.get("released", False))

    return GestureSummary(
        primary=primary,
        best_name=best_name,
        best_confidence=best_confidence,
        target_active=target_active,
        target_confidence=target_confidence,
        stable_active=stable_active,
        stable_held_for=stable_held_for,
        stable_activated=stable_activated,
        stable_released=stable_released,
    )


def summarize_camera_status(payload: dict[str, Any]) -> CameraStatusSummary:
    camera = payload.get("camera") if isinstance(payload.get("camera"), dict) else {}
    capture = payload.get("capture") if isinstance(payload.get("capture"), dict) else {}
    processors = (
        payload.get("processors") if isinstance(payload.get("processors"), dict) else {}
    )
    sword_processor = (
        processors.get("sword_sign")
        if isinstance(processors.get("sword_sign"), dict)
        else {}
    )
    return CameraStatusSummary(
        camera_index=_optional_int(camera.get("selected_index")),
        opened=_optional_bool(camera.get("opened")),
        frame_read_ok=_optional_bool(camera.get("frame_read_ok")),
        fps=_optional_float(payload.get("fps")),
        frame_id=_optional_int(payload.get("frame_id")),
        frame_age_ms=_optional_float(capture.get("frame_age_ms")),
        read_latency_ms=_optional_float(capture.get("read_latency_ms")),
        read_failures=_optional_int(capture.get("read_failures")),
        gesture_inference_ms=_optional_float(sword_processor.get("inference_ms")),
        gesture_publish_age_ms=_optional_float(sword_processor.get("publish_age_ms")),
    )


def decode_compressed_image(payload: dict[str, Any]):
    if payload.get("format") != "jpeg":
        raise ValueError("compressed image payload must be jpeg")
    data = payload.get("data_base64")
    if not isinstance(data, str) or not data:
        raise ValueError("compressed image payload must contain data_base64")
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise ValueError("compressed image payload has invalid base64") from exc
    return decode_jpeg_bytes(raw)


def decode_binary_compressed_image(payload: dict[str, Any], data: bytes):
    if payload.get("format") != "jpeg":
        raise ValueError("compressed image payload must be jpeg")
    if not data:
        raise ValueError("compressed image payload must contain binary data")
    return decode_jpeg_bytes(data)


def decode_jpeg_bytes(data: bytes):
    encoded = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("compressed image payload could not be decoded")
    return frame


def compact_envelope_for_display(envelope: dict[str, Any]) -> dict[str, Any]:
    compact = copy.deepcopy(envelope)
    payload = compact.get("payload")
    if isinstance(payload, dict):
        data = payload.get("data_base64")
        if isinstance(data, str):
            payload["data_base64"] = f"<omitted {len(data)} base64 chars>"
    return compact


def envelope_age_seconds(envelope: dict[str, Any]) -> float | None:
    header = envelope.get("header")
    if not isinstance(header, dict):
        return None
    stamp = _optional_float(header.get("stamp"))
    if stamp is None:
        return None
    return max(0.0, time.time() - stamp)


def draw_preview_overlay(
    frame,
    *,
    gesture: GestureSummary | None,
    status: CameraStatusSummary | None,
    topic: str,
) -> None:
    color = (0, 180, 0)
    lines: list[str] = []
    if gesture is not None:
        color = (0, 180, 0) if gesture.target_active else (0, 0, 220)
        lines.extend(
            [
                f"primary: {gesture.primary}",
                f"best: {gesture.best_name} {gesture.best_confidence:.3f}",
                f"target: {'active' if gesture.target_active else 'inactive'} {gesture.target_confidence:.3f}",
            ]
        )
        if gesture.stable_active is not None:
            lines.append(
                f"stable: {'active' if gesture.stable_active else 'inactive'} held={gesture.stable_held_for or 0.0:.2f}s"
            )
    if status is not None:
        lines.append(f"fps: {status.fps if status.fps is not None else '-'}")
    lines.append(f"topic: {topic or '-'}")

    for index, line in enumerate(lines[:6]):
        cv2.putText(
            frame,
            line,
            (10, 30 + index * 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            color,
            2,
        )


def safe_error_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__


def _load_connect():
    try:
        from websockets.asyncio.client import connect

        return connect, "additional_headers"
    except ImportError:
        from websockets.legacy.client import connect

        return connect, "extra_headers"


def _float(value: object, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f} ms"


def _optional_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def main() -> None:
    root = tk.Tk()
    CameraHubGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
