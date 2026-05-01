from __future__ import annotations

import asyncio
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mediapipe_sword_sign import GESTURE_SWORD_SIGN, SwordSignDetector
from mediapipe_sword_sign.adapters import WebSocketGestureBroadcaster
from mediapipe_sword_sign.temporal import GestureHoldTracker
from mediapipe_sword_sign.types import DISPLAY_NAMES, GestureState


PREVIEW_WINDOW = "Gesture Monitor Preview"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class WebSocketRuntime:
    def __init__(self) -> None:
        self.broadcaster: WebSocketGestureBroadcaster | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.broadcaster is not None and self.loop is not None

    @property
    def client_count(self) -> int:
        if self.broadcaster is None:
            return 0
        return len(self.broadcaster.clients)

    def start(self, *, host: str, port: int, auth_token: str | None) -> None:
        self.stop()
        self.loop = asyncio.new_event_loop()
        self.broadcaster = WebSocketGestureBroadcaster(
            host,
            port,
            auth_token=auth_token,
        )
        self.thread = threading.Thread(
            target=self._run_loop,
            name="gesture-monitor-websocket",
            daemon=True,
        )
        self.thread.start()
        future = asyncio.run_coroutine_threadsafe(self.broadcaster.start(), self.loop)
        future.result(timeout=5)

    def stop(self) -> None:
        broadcaster = self.broadcaster
        loop = self.loop
        if broadcaster is not None and loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(broadcaster.stop(), loop)
            try:
                future.result(timeout=5)
            except Exception:
                pass
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=5)
        if loop is not None and not loop.is_closed():
            loop.close()
        self.broadcaster = None
        self.loop = None
        self.thread = None

    def publish(self, state: GestureState) -> Future[int] | None:
        broadcaster = self.broadcaster
        loop = self.loop
        if broadcaster is None or loop is None or not loop.is_running():
            return None
        return asyncio.run_coroutine_threadsafe(self._publish(state), loop)

    async def _publish(self, state: GestureState) -> int:
        assert self.broadcaster is not None
        client_count = len(self.broadcaster.clients)
        await self.broadcaster.publish(state)
        return client_count

    def _run_loop(self) -> None:
        assert self.loop is not None
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()


class GestureMonitorGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MediaPipe Sword Sign Gesture Monitor")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.camera_index = tk.IntVar(value=0)
        self.model_path = tk.StringVar(value=str(Path("gesture_model.pkl")))
        self.model_sha256 = tk.StringVar(value="")
        self.allow_untrusted_model = tk.BooleanVar(value=False)
        self.threshold = tk.DoubleVar(value=0.9)
        self.hold_seconds = tk.DoubleVar(value=0.5)
        self.release_grace_seconds = tk.DoubleVar(value=0.1)
        self.host = tk.StringVar(value="127.0.0.1")
        self.port = tk.IntVar(value=8765)
        self.auth_token = tk.StringVar(value="")
        self.mirror_preview = tk.BooleanVar(value=True)
        self.show_landmarks = tk.BooleanVar(value=True)
        self.show_preview = tk.BooleanVar(value=True)

        self.detector: SwordSignDetector | None = None
        self.cap: cv2.VideoCapture | None = None
        self.websocket = WebSocketRuntime()
        self.hold_tracker = GestureHoldTracker()
        self.running = False
        self._mp_hands = None
        self._mp_drawing = None
        self._raw_active: bool | None = None
        self._stable_active: bool | None = None
        self._last_publish_future: Future[int] | None = None

        self.capture_state = tk.StringVar(value="Stopped")
        self.websocket_state = tk.StringVar(value="Stopped")
        self.client_count = tk.StringVar(value="0")
        self.primary_gesture = tk.StringVar(value="none")
        self.best_gesture = tk.StringVar(value="-")
        self.sword_raw_state = tk.StringVar(value="inactive")
        self.sword_confidence = tk.StringVar(value="0.000")
        self.stable_state = tk.StringVar(value="inactive")
        self.held_for = tk.StringVar(value="0.00s")
        self.last_published_at = tk.StringVar(value="-")
        self.last_publish_result = tk.StringVar(value="-")

        self._build()
        self._tick()

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)

        settings = ttk.LabelFrame(self.root, text="Settings", padding=12)
        settings.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="Model").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(settings, textvariable=self.model_path).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(settings, text="Browse", command=self._browse_model).grid(row=0, column=2, padx=(8, 0), pady=4)

        ttk.Label(settings, text="Model SHA-256").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(settings, textvariable=self.model_sha256).grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)

        ttk.Label(settings, text="Camera").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Spinbox(settings, from_=0, to=10, textvariable=self.camera_index, width=8).grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(settings, text="Threshold").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Scale(settings, from_=0.5, to=1.0, variable=self.threshold, command=lambda _value: self._sync_labels()).grid(row=3, column=1, sticky="ew", pady=4)
        self.threshold_label = ttk.Label(settings, width=6)
        self.threshold_label.grid(row=3, column=2, sticky="e", pady=4)

        ttk.Label(settings, text="Hold").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Scale(settings, from_=0.0, to=3.0, variable=self.hold_seconds, command=lambda _value: self._sync_labels()).grid(row=4, column=1, sticky="ew", pady=4)
        self.hold_label = ttk.Label(settings, width=6)
        self.hold_label.grid(row=4, column=2, sticky="e", pady=4)

        ttk.Label(settings, text="Grace").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Scale(settings, from_=0.0, to=1.0, variable=self.release_grace_seconds, command=lambda _value: self._sync_labels()).grid(row=5, column=1, sticky="ew", pady=4)
        self.grace_label = ttk.Label(settings, width=6)
        self.grace_label.grid(row=5, column=2, sticky="e", pady=4)

        ttk.Label(settings, text="Host").grid(row=6, column=0, sticky="w", pady=4)
        ttk.Entry(settings, textvariable=self.host).grid(row=6, column=1, sticky="ew", pady=4)

        ttk.Label(settings, text="Port").grid(row=7, column=0, sticky="w", pady=4)
        ttk.Spinbox(settings, from_=1, to=65535, textvariable=self.port, width=8).grid(row=7, column=1, sticky="w", pady=4)

        ttk.Label(settings, text="Token").grid(row=8, column=0, sticky="w", pady=4)
        ttk.Entry(settings, textvariable=self.auth_token, show="*").grid(row=8, column=1, columnspan=2, sticky="ew", pady=4)

        toggles = ttk.Frame(settings)
        toggles.grid(row=9, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(toggles, text="Mirror", variable=self.mirror_preview).grid(row=0, column=0, padx=(0, 12))
        ttk.Checkbutton(toggles, text="Landmarks", variable=self.show_landmarks).grid(row=0, column=1, padx=(0, 12))
        ttk.Checkbutton(toggles, text="Preview", variable=self.show_preview).grid(row=0, column=2, padx=(0, 12))
        ttk.Checkbutton(toggles, text="Allow untrusted model", variable=self.allow_untrusted_model).grid(row=0, column=3)

        controls = ttk.Frame(self.root, padding=(12, 0, 12, 0))
        controls.grid(row=1, column=0, sticky="ew")
        ttk.Button(controls, text="Start", command=self.start).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(controls, text="Stop", command=self.stop).grid(row=0, column=1)

        state = ttk.LabelFrame(self.root, text="State", padding=12)
        state.grid(row=2, column=0, sticky="ew", padx=12, pady=12)
        state.columnconfigure(1, weight=1)
        rows = [
            ("Capture", self.capture_state),
            ("WebSocket", self.websocket_state),
            ("Clients", self.client_count),
            ("Primary", self.primary_gesture),
            ("Best", self.best_gesture),
            ("Raw sword_sign", self.sword_raw_state),
            ("Stable sword_sign", self.stable_state),
            ("Held For", self.held_for),
            ("Last Published At", self.last_published_at),
            ("Last Publish Result", self.last_publish_result),
        ]
        for row, (label, value) in enumerate(rows):
            ttk.Label(state, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Label(state, textvariable=value).grid(row=row, column=1, sticky="w", padx=(12, 0), pady=2)

        confidence = ttk.LabelFrame(self.root, text="Confidence", padding=12)
        confidence.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        ttk.Label(confidence, text="sword_sign", font=("", 14, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(confidence, textvariable=self.sword_confidence, font=("", 24, "bold")).grid(row=0, column=1, sticky="e", padx=(24, 0))
        confidence.columnconfigure(1, weight=1)

        details = ttk.Frame(self.root, padding=(12, 0, 12, 12))
        details.grid(row=4, column=0, sticky="nsew")
        details.columnconfigure(0, weight=1)

        ttk.Label(details, text="Event Log").grid(row=0, column=0, sticky="w")
        self.event_log = tk.Listbox(details, height=8)
        self.event_log.grid(row=1, column=0, sticky="nsew", pady=(4, 12))

        ttk.Label(details, text="Last Generated JSON").grid(row=2, column=0, sticky="w")
        self.last_json = tk.Text(details, height=10, wrap="none")
        self.last_json.grid(row=3, column=0, sticky="nsew", pady=(4, 0))

        self.root.rowconfigure(4, weight=1)
        self._sync_labels()

    def _browse_model(self) -> None:
        path = filedialog.askopenfilename(
            title="Select gesture model",
            filetypes=[("Pickle model", "*.pkl"), ("All files", "*.*")],
        )
        if path:
            self.model_path.set(path)

    def start(self) -> None:
        self.stop()
        host = self.host.get().strip() or "127.0.0.1"
        if host not in LOCAL_HOSTS:
            confirmed = messagebox.askyesno(
                "Non-local WebSocket host",
                "The selected host is not 127.0.0.1, localhost, or ::1.\n"
                "Only bind to a non-local address on a trusted network and prefer using a token.\n\n"
                "Continue?",
            )
            if not confirmed:
                return

        model_path = self.model_path.get().strip() or None
        model_sha256 = self.model_sha256.get().strip() or None
        auth_token = self.auth_token.get().strip() or None
        try:
            self.detector = SwordSignDetector(
                model_path=model_path,
                expected_model_sha256=model_sha256,
                allow_untrusted_model=self.allow_untrusted_model.get(),
                threshold=self.threshold.get(),
            )
            self.detector.__enter__()
            self.cap = cv2.VideoCapture(int(self.camera_index.get()))
            if not self.cap.isOpened():
                raise RuntimeError(f"camera not available: {self.camera_index.get()}")
            self.websocket.start(
                host=host,
                port=int(self.port.get()),
                auth_token=auth_token,
            )
        except Exception as exc:
            self.stop()
            messagebox.showerror("Start failed", str(exc))
            return

        self.hold_tracker.reset()
        self._raw_active = None
        self._stable_active = None
        self.running = True
        self.capture_state.set("Running")
        self.websocket_state.set(f"Listening on ws://{host}:{int(self.port.get())}")
        self._append_event("started")

    def stop(self) -> None:
        self.running = False
        if self.detector is not None:
            self.detector.close()
            self.detector = None
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.websocket.stop()
        self.hold_tracker.reset()
        self._last_publish_future = None
        self.capture_state.set("Stopped")
        self.websocket_state.set("Stopped")
        self.client_count.set("0")
        self.primary_gesture.set("none")
        self.best_gesture.set("-")
        self.sword_raw_state.set("inactive")
        self.sword_confidence.set("0.000")
        self.stable_state.set("inactive")
        self.held_for.set("0.00s")
        self._destroy_preview()

    def close(self) -> None:
        self.stop()
        self.root.destroy()

    def _tick(self) -> None:
        if self.running and self.detector is not None and self.cap is not None:
            self._update_frame()
        self.client_count.set(str(self.websocket.client_count))
        self._finish_publish_if_ready()
        self.root.after(15, self._tick)

    def _update_frame(self) -> None:
        assert self.detector is not None
        assert self.cap is not None

        self.detector.threshold = self.threshold.get()
        self.hold_tracker.configure(
            target=GESTURE_SWORD_SIGN,
            hold_seconds=self.hold_seconds.get(),
            release_grace_seconds=self.release_grace_seconds.get(),
        )

        success, frame = self.cap.read()
        if not success:
            self.capture_state.set("Frame read failed")
            return

        mirrored = self.mirror_preview.get()
        result = self.detector.detect_frame(frame, flip=mirrored)
        state = result.state
        hold = self.hold_tracker.update(state, now=time.monotonic())
        best = state.best_gesture()
        sword = state.sword_sign
        primary = state.primary or "none"

        self.primary_gesture.set(DISPLAY_NAMES.get(primary, primary))
        if best is None:
            self.best_gesture.set("-")
        else:
            best_name = DISPLAY_NAMES.get(best.name, best.name)
            self.best_gesture.set(f"{best_name} ({best.confidence:.3f})")
        self.sword_raw_state.set(f"{'active' if sword.active else 'inactive'} ({sword.confidence:.3f})")
        self.sword_confidence.set(f"{sword.confidence:.3f}")
        self.stable_state.set("active" if hold.active else "inactive")
        self.held_for.set(f"{hold.held_for:.2f}s")

        self._record_transitions(sword.active, hold.active, hold.activated, hold.released)
        self._publish_state(state)

        if self.show_preview.get():
            self._show_preview(frame, result.hand_landmarks, state, hold, mirrored)
        else:
            self._destroy_preview()

    def _record_transitions(
        self,
        raw_active: bool,
        stable_active: bool,
        stable_activated: bool,
        stable_released: bool,
    ) -> None:
        if self._raw_active is None:
            self._raw_active = raw_active
        elif self._raw_active != raw_active:
            self._raw_active = raw_active
            self._append_event(f"raw changed: {'active' if raw_active else 'inactive'}")

        if self._stable_active is None:
            self._stable_active = stable_active
        elif self._stable_active != stable_active:
            self._stable_active = stable_active

        if stable_activated:
            self._append_event("stable activated")
        elif stable_released:
            self._append_event("stable released")

    def _publish_state(self, state: GestureState) -> None:
        message = state.to_json()
        self.last_json.delete("1.0", tk.END)
        self.last_json.insert(tk.END, message)
        self.last_published_at.set(datetime.now().strftime("%H:%M:%S.%f")[:-3])
        if self._last_publish_future is not None and not self._last_publish_future.done():
            self.last_publish_result.set("publish pending")
            return
        future = self.websocket.publish(state)
        if future is None:
            self.last_publish_result.set("generated")
            return
        self._last_publish_future = future

    def _finish_publish_if_ready(self) -> None:
        future = self._last_publish_future
        if future is None or not future.done():
            return
        self._last_publish_future = None
        try:
            sent_count = future.result()
        except Exception as exc:
            self.last_publish_result.set(f"publish failed: {exc}")
            self.websocket_state.set("Error")
            return
        if sent_count:
            self.last_publish_result.set(f"sent to {sent_count} client(s)")
        else:
            self.last_publish_result.set("generated")

    def _show_preview(self, frame, hand_landmarks, state: GestureState, hold, mirrored: bool) -> None:
        display = cv2.flip(frame, 1) if mirrored else frame.copy()

        if self.show_landmarks.get() and hand_landmarks is not None:
            self._draw_landmarks(display, hand_landmarks)

        primary = state.primary or "none"
        best = state.best_gesture()
        best_text = "-" if best is None else f"{DISPLAY_NAMES.get(best.name, best.name)} {best.confidence:.3f}"
        sword = state.sword_sign
        color = (0, 180, 0) if sword.active else (0, 0, 220)
        stable_color = (0, 180, 0) if hold.active else (0, 180, 220)

        cv2.putText(
            display,
            f"sword_sign confidence: {sword.confidence:.3f}",
            (10, 36),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.95,
            color,
            2,
        )
        cv2.putText(
            display,
            f"raw: {'active' if sword.active else 'inactive'}  stable: {'active' if hold.active else 'inactive'}",
            (10, 72),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            stable_color,
            2,
        )
        cv2.putText(
            display,
            f"primary: {DISPLAY_NAMES.get(primary, primary)}  best: {best_text}",
            (10, 104),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            2,
        )
        cv2.imshow(PREVIEW_WINDOW, display)
        if cv2.waitKey(1) & 0xFF == 27:
            self.stop()

    def _draw_landmarks(self, image, hand_landmarks) -> None:
        if self._mp_drawing is None or self._mp_hands is None:
            import mediapipe as mp

            self._mp_drawing = mp.solutions.drawing_utils
            self._mp_hands = mp.solutions.hands
        self._mp_drawing.draw_landmarks(
            image,
            hand_landmarks,
            self._mp_hands.HAND_CONNECTIONS,
        )

    def _destroy_preview(self) -> None:
        try:
            cv2.destroyWindow(PREVIEW_WINDOW)
        except cv2.error:
            pass

    def _append_event(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.event_log.insert(tk.END, f"{timestamp} {message}")
        if self.event_log.size() > 200:
            self.event_log.delete(0)
        self.event_log.see(tk.END)

    def _sync_labels(self) -> None:
        self.threshold_label.configure(text=f"{self.threshold.get():.2f}")
        self.hold_label.configure(text=f"{self.hold_seconds.get():.2f}s")
        self.grace_label.configure(text=f"{self.release_grace_seconds.get():.2f}s")


def main() -> None:
    root = tk.Tk()
    GestureMonitorGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
