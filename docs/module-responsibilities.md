# Camera Hub Module Responsibilities

This document is the responsibility boundary for the MediaPipe sword-sign gesture module.
It is not a change log and does not describe experiment history.

## Functional Requirements

- Detect `sword_sign`, `victory`, and `none` from BGR frames.
- Return frame-level `GestureState` through `SwordSignDetector.detect(frame_bgr)`.
- Add duration-based activation/release state with `GestureHoldTracker` when a runtime needs stable behavior.
- Publish Camera Hub topics with finite numeric values and explicit schema versions.
- Keep video fan-out outside Python in MediaMTX for browser integrations.
- Keep module behavior independent from Home Control action names, room-light rules, or voice-agent commands.

## Responsibility Boundary

| Component | Owns | Does Not Own |
| --- | --- | --- |
| `mediapipe_sword_sign/` | feature extraction, model loading, gesture state types, hold tracking, topic helpers | camera device ownership, UI rendering, home automation |
| `apps/serve_camera_hub.py` | one camera/stream input, MediaPipe inference, status metrics, WebSocket topic publishing | browser video fan-out, MediaMTX lifecycle outside local stack, downstream actions |
| `scripts/start_camera_hub_stack.bat` | local Windows startup for MediaMTX, FFmpeg publish, Camera Hub, Browser Monitor | service deployment, TLS, production process supervision |
| Browser Monitor | local visualization of MediaMTX video and Camera Hub topics | camera capture, gesture inference, action execution |
| MediaMTX config samples | local media routing examples | security policy for public media service exposure |

## Integration Requirements

- Browser video must come from MediaMTX WebRTC/HLS in the normal stack.
- Camera Hub must read MediaMTX RTSP through `--camera-backend ffmpeg-pipe` for low-latency overlay alignment.
- Python JPEG topic must stay disabled with `--publish-jpeg-every 0` unless testing Python image transport.
- WebSocket topics must use the envelope described in [Integration Contract](integration-contract.md).
- Non-local WebSocket exposure requires token and exact allowed origin settings.
- Multiple cameras use distinct MediaMTX paths and distinct Camera Hub ports unless a separate multi-camera service is introduced by the integrator.

## Artifacts Outside The Contract

- `CHANGELOG.md` is a dated change summary, not a runtime specification.
- `docs/archive/` contains historical source text kept for traceability.
- `examples/sword_push_to_talk_sample.py` is a reference sample, not a required integration.
- `apps/publish_udp.py` and `apps/serve_websocket.py` are compatibility paths; see [Retired And Compatibility Paths](retired-paths.md).
