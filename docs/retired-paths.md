# Retired And Compatibility Paths

This file keeps short notes for paths that remain in the repository but are not the normal integration contract.
Use [Integration Contract](integration-contract.md) for runtime wiring.

## Compatibility Rule

Compatibility and diagnostic tools may stay available, but they must not be cited as the primary Camera Hub route.
If a use case depends on one of these paths, document the reason in the integration repository.

## `apps/publish_udp.py`

UDP publishing is retained for older receivers such as `sword-voice-agent`.
Status: compatibility-only.

- Sends `gesture_state`, optional status JSON, and optional heartbeat payloads.
- Remote UDP requires `--allow-remote-udp`.
- `SWORD_VOICE_AGENT_AUTH_TOKEN` or `--auth-token-env` can add `auth_token`.
- Token transport is a shared-secret check, not encryption.

Use it only for compatibility with UDP receivers or low-level diagnostics.

## Python JPEG Topic

`/camera/color/image_raw/compressed` is disabled in the MediaMTX route with `--publish-jpeg-every 0`.
Enable it only when testing Python image transport or the Python debug GUI:

```powershell
uv run python apps/serve_camera_hub.py --publish-jpeg-every 0.05
```

For browser video, use MediaMTX WebRTC/HLS instead.

`JPEG Debug Preview` is a useful label for this diagnostic mode, but it is not a
product video path and should not appear in startup docs as the normal way to view
camera video.

## Direct `file://` Browser Monitor

Opening `apps/browser_camera_hub_viewer.html` directly as a `file://` URL is
retained for single-file debugging.

Normal integrated stacks should start `apps/serve_browser_monitor.py` and open:

```text
http://127.0.0.1:8770/browser_camera_hub_viewer.html?mediaUrl=...&wsUrl=...
```

The query parameters are the same as the direct file URL. The HTTP viewer server
only serves static HTML; it does not own camera capture, inference, or video
distribution.

## FFmpeg Pipe Compatibility Diagnostic

`--camera-backend ffmpeg` uses OpenCV's FFmpeg-backed `VideoCapture`.
It is the current canonical MediaMTX RTSP reader and uses bounded open/read
timeouts.

`--camera-backend ffmpeg-pipe` is compatibility-required only for the explicit
maintainer blocking/latency diagnostic route from `scripts/camera_hub_stack.py
--hub-camera-backend ffmpeg-pipe` to `apps/serve_camera_hub.py
--camera-backend ffmpeg-pipe`. It must not be presented as a second canonical
reader. Remove the compatibility flag only after all operator and documentation
consumers and explicit first-party invocations have migrated, repository search
plus consumer/test inventory prove that no current or named external consumer
remains, and canonical bounded open/read/disconnect/reconnect/cleanup proof is
green. A pipe-read rewrite alone is not deletion proof.

If OpenCV rejects capture options, use:

```powershell
--opencv-ffmpeg-capture-options none
```

## Direct USB Camera Hub

Camera Hub can still open a USB camera index for local detector checks.
Integrated browser workflows should prefer FFmpeg publish to MediaMTX, then RTSP input into Camera Hub.

## Archive

The following pre-pruning Markdown snapshots were copied for traceability and are not integration specs:

- `docs/archive/2026-05-07-markdown-pruning/README.before-pruning.md`
- `docs/archive/2026-05-07-markdown-pruning/browser_gui_integration.before-pruning.md`
- `docs/archive/2026-05-07-markdown-pruning/mediamtx_integration.before-pruning.md`
- `docs/archive/2026-05-07-markdown-pruning/CHANGELOG.before-pruning.md`

Do not use archive files as normal onboarding or connection-contract material.
