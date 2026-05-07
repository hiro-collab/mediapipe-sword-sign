# Changelog

## 2026-05-07 - Camera Hub Topic Envelope Split

- Added topic envelope helpers in `mediapipe_sword_sign/topics.py` for ROS2-shaped JSON without requiring ROS2.
- Added `gesture_state_payload()` helpers with schema version, sequence, and stable hold state.
- Added `apps/serve_camera_hub.py` to own one camera input and publish `/vision/sword_sign/state` plus `/camera/status`.
- Added `apps/camera_hub_gui.py` and `apps/browser_camera_hub_viewer.html` for local monitoring.
- Added `apps/serve_browser_monitor.py` so the Browser Monitor can be served as localhost HTTP instead of only `file://`.
- Added MediaMTX stack startup so video is handled by MediaMTX while Camera Hub publishes lightweight topics.
- Hardened WebSocket and browser-viewer boundaries: exact origins, token normalization, message/queue limits, query-token flood resistance, URL protocol checks, and redacted RTSP credentials in stack logs.
- Kept `room_light` and Home Control action mapping outside this generic gesture module.
- Pruned Markdown guidance so README is an entrypoint, MediaMTX setup lives in `docs/mediamtx_integration.md`, topic contracts live in `docs/integration-contract.md`, and compatibility paths live in `docs/retired-paths.md`.

## 2026-04-28 - UDP Auth Token Support

- Added optional UDP `auth_token` payload support through `SWORD_VOICE_AGENT_AUTH_TOKEN`, `--auth-token-env`, or `--auth-token`.
- Kept tokens out of `--print-json`, `--status-json`, and debug output.
- Added optional `auth_token` to `--schema-json`.
- Documented UDP token use as compatibility behavior rather than the Camera Hub topic path.

## 2026-04-28 - UDP Diagnostics Security Hardening

- Validated UDP CLI inputs such as port, camera index, scan limit, threshold, and interval.
- Rejected non-local UDP destinations unless `--allow-remote-udp` is explicit.
- Hid internal model paths and hashes from `--health-json` and `--check-config` errors.
- Aligned direct dependencies between `pyproject.toml`, `requirements.txt`, and `uv.lock`.

## 2026-04-27 - Baseline Security Hardening

- Added a trust boundary around joblib/pickle model loading.
- Required token and origin controls for non-local WebSocket exposure.
- Added `.env`, logs, CSV, and `.pkl` outputs to `.gitignore`.
- Stopped treating `gesture_data.csv` and `gesture_model.pkl` as shareable repository artifacts.
