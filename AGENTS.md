# Repository Guide

This repository contains the MediaPipe Sword Sign Camera Hub: a local vision module that detects `sword_sign`, `victory`, and `none`, then publishes Camera Hub topics for browser and downstream integrations.

## Structure

- `mediapipe_sword_sign/`: detector, gesture state types, hold tracking, and topic helpers.
- `apps/serve_camera_hub.py`: Camera Hub runtime for one camera or stream.
- `apps/browser_camera_hub_viewer.html`: local Browser Monitor for MediaMTX video and Camera Hub topics.
- `scripts/start_camera_hub_stack.bat`: Windows stack launcher for MediaMTX, FFmpeg publish, Camera Hub, and Browser Monitor.
- `docs/`: responsibility, integration contract, MediaMTX, Browser GUI, and retired-path notes.

Runtime logs, collected CSV, model artifacts, and local `.vrm/` content are not source.

## Commands

```powershell
uv run python -m unittest discover -s tests
uv run python apps/serve_camera_hub.py --host 127.0.0.1 --port 8765
scripts\start_camera_hub_stack.bat --camera-name "HD Pro Webcam C920"
```

Detector tools:

```powershell
uv run collect_data.py
uv run train_model.py
uv run predict.py
uv run python apps/settings_gui.py
```

## Documentation

Keep README as the module entrypoint. Detailed behavior is split by role:

- `docs/module-responsibilities.md`: requirements and responsibility boundary.
- `docs/integration-contract.md`: topic envelope, payloads, auth, and origins.
- `docs/mediamtx_integration.md`: MediaMTX, FFmpeg publish, and `ffmpeg-pipe` setup.
- `docs/browser_gui_integration.md`: Browser Monitor behavior and checks.
- `docs/retired-paths.md`: compatibility and diagnostic paths.

Files under `docs/archive/` are historical records. Do not cite them as active requirements or connection contracts unless a human explicitly asks for history.

## Change Rules

- Keep browser video on MediaMTX for the normal stack.
- Keep Python JPEG topic disabled unless testing Python image transport.
- Keep Home Control action names and room automation rules out of this generic gesture module.
- Use exact origins and token auth for non-local WebSocket exposure.
