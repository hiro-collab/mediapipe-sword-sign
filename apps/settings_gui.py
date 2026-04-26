from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2

from mediapipe_sword_sign import GESTURE_SWORD_SIGN, GESTURE_VICTORY, SwordSignDetector
from mediapipe_sword_sign.temporal import GestureHoldTracker
from mediapipe_sword_sign.types import DISPLAY_NAMES


PREVIEW_WINDOW = "Gesture Settings Preview"


class GestureSettingsGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MediaPipe Sword Sign Settings")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.camera_index = tk.IntVar(value=0)
        self.model_path = tk.StringVar(value=str(Path("gesture_model.pkl")))
        self.threshold = tk.DoubleVar(value=0.9)
        self.hold_seconds = tk.DoubleVar(value=0.5)
        self.release_grace_seconds = tk.DoubleVar(value=0.1)
        self.target_gesture = tk.StringVar(value=GESTURE_SWORD_SIGN)
        self.mirror_preview = tk.BooleanVar(value=True)
        self.show_landmarks = tk.BooleanVar(value=True)
        self.show_preview = tk.BooleanVar(value=True)

        self.detector: SwordSignDetector | None = None
        self.cap: cv2.VideoCapture | None = None
        self.hold_tracker = GestureHoldTracker()
        self.running = False
        self._mp_hands = None
        self._mp_drawing = None

        self.current_state = tk.StringVar(value="Stopped")
        self.current_confidence = tk.StringVar(value="-")
        self.hold_state = tk.StringVar(value="-")
        self.held_for = tk.StringVar(value="0.00s")
        self.event_state = tk.StringVar(value="-")

        self._build()
        self._tick()

    def _build(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)

        settings = ttk.LabelFrame(root, text="Settings", padding=12)
        settings.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="Model").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(settings, textvariable=self.model_path).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(settings, text="Browse", command=self._browse_model).grid(row=0, column=2, padx=(8, 0), pady=4)

        ttk.Label(settings, text="Camera").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Spinbox(settings, from_=0, to=10, textvariable=self.camera_index, width=8).grid(row=1, column=1, sticky="w", pady=4)

        ttk.Label(settings, text="Threshold").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Scale(settings, from_=0.5, to=1.0, variable=self.threshold, command=lambda _value: self._sync_labels()).grid(row=2, column=1, sticky="ew", pady=4)
        self.threshold_label = ttk.Label(settings, width=6)
        self.threshold_label.grid(row=2, column=2, sticky="e", pady=4)

        ttk.Label(settings, text="Hold").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Scale(settings, from_=0.0, to=3.0, variable=self.hold_seconds, command=lambda _value: self._sync_labels()).grid(row=3, column=1, sticky="ew", pady=4)
        self.hold_label = ttk.Label(settings, width=6)
        self.hold_label.grid(row=3, column=2, sticky="e", pady=4)

        ttk.Label(settings, text="Grace").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Scale(settings, from_=0.0, to=1.0, variable=self.release_grace_seconds, command=lambda _value: self._sync_labels()).grid(row=4, column=1, sticky="ew", pady=4)
        self.grace_label = ttk.Label(settings, width=6)
        self.grace_label.grid(row=4, column=2, sticky="e", pady=4)

        ttk.Label(settings, text="Target").grid(row=5, column=0, sticky="w", pady=4)
        target = ttk.Combobox(
            settings,
            textvariable=self.target_gesture,
            values=[GESTURE_SWORD_SIGN, GESTURE_VICTORY],
            state="readonly",
            width=18,
        )
        target.grid(row=5, column=1, sticky="w", pady=4)

        toggles = ttk.Frame(settings)
        toggles.grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(toggles, text="Mirror", variable=self.mirror_preview).grid(row=0, column=0, padx=(0, 12))
        ttk.Checkbutton(toggles, text="Landmarks", variable=self.show_landmarks).grid(row=0, column=1, padx=(0, 12))
        ttk.Checkbutton(toggles, text="Preview", variable=self.show_preview).grid(row=0, column=2)

        controls = ttk.Frame(root, padding=(12, 0, 12, 0))
        controls.grid(row=1, column=0, sticky="ew")
        ttk.Button(controls, text="Start", command=self.start).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(controls, text="Stop", command=self.stop).grid(row=0, column=1)

        state = ttk.LabelFrame(root, text="State", padding=12)
        state.grid(row=2, column=0, sticky="ew", padx=12, pady=12)
        state.columnconfigure(1, weight=1)
        rows = [
            ("Current", self.current_state),
            ("Confidence", self.current_confidence),
            ("Hold State", self.hold_state),
            ("Held For", self.held_for),
            ("Event", self.event_state),
        ]
        for row, (label, value) in enumerate(rows):
            ttk.Label(state, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Label(state, textvariable=value).grid(row=row, column=1, sticky="w", padx=(12, 0), pady=2)

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
        model_path = self.model_path.get().strip() or None
        try:
            self.detector = SwordSignDetector(
                model_path=model_path,
                threshold=self.threshold.get(),
            )
            self.detector.__enter__()
            self.cap = cv2.VideoCapture(int(self.camera_index.get()))
            if not self.cap.isOpened():
                raise RuntimeError(f"camera not available: {self.camera_index.get()}")
        except Exception as exc:
            self.stop()
            messagebox.showerror("Start failed", str(exc))
            return

        self.hold_tracker.reset()
        self.running = True
        self.event_state.set("Started")

    def stop(self) -> None:
        self.running = False
        if self.detector is not None:
            self.detector.close()
            self.detector = None
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.hold_tracker.reset()
        self.current_state.set("Stopped")
        self.current_confidence.set("-")
        self.hold_state.set("-")
        self.held_for.set("0.00s")
        self._destroy_preview()

    def close(self) -> None:
        self.stop()
        self.root.destroy()

    def _tick(self) -> None:
        if self.running and self.detector is not None and self.cap is not None:
            self._update_frame()
        self.root.after(15, self._tick)

    def _update_frame(self) -> None:
        assert self.detector is not None
        assert self.cap is not None

        self.detector.threshold = self.threshold.get()
        self.hold_tracker.configure(
            target=self.target_gesture.get(),
            hold_seconds=self.hold_seconds.get(),
            release_grace_seconds=self.release_grace_seconds.get(),
        )

        success, frame = self.cap.read()
        if not success:
            self.current_state.set("Frame read failed")
            return

        mirrored = self.mirror_preview.get()
        result = self.detector.detect_frame(frame, flip=mirrored)
        state = result.state
        hold = self.hold_tracker.update(state, now=time.monotonic())

        best = state.best_gesture()
        current_name = state.primary or "searching"
        confidence = best.confidence if best else 0.0
        self.current_state.set(DISPLAY_NAMES.get(current_name, current_name))
        self.current_confidence.set(f"{confidence:.3f}")
        self.hold_state.set("active" if hold.active else "inactive")
        self.held_for.set(f"{hold.held_for:.2f}s")
        if hold.activated:
            self.event_state.set(f"{hold.target} activated")
        elif hold.released:
            self.event_state.set(f"{hold.target} released")

        if self.show_preview.get():
            self._show_preview(frame, result.hand_landmarks, state, hold.active, mirrored)
        else:
            self._destroy_preview()

    def _show_preview(self, frame, hand_landmarks, state, stable_active: bool, mirrored: bool) -> None:
        display = cv2.flip(frame, 1) if mirrored else frame.copy()

        if self.show_landmarks.get() and hand_landmarks is not None:
            self._draw_landmarks(display, hand_landmarks)

        current = state.primary or "searching"
        status = f"{DISPLAY_NAMES.get(current, current)} / stable={stable_active}"
        color = (0, 180, 0) if stable_active else (0, 0, 220)
        cv2.putText(display, status, (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
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

    def _sync_labels(self) -> None:
        self.threshold_label.configure(text=f"{self.threshold.get():.2f}")
        self.hold_label.configure(text=f"{self.hold_seconds.get():.2f}s")
        self.grace_label.configure(text=f"{self.release_grace_seconds.get():.2f}s")


def main() -> None:
    root = tk.Tk()
    GestureSettingsGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
