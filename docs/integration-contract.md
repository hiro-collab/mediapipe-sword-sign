# Camera Hub Integration Contract

This document defines the wire contract for browser and downstream integrations.
README, changelog entries, and archived planning notes should point here when they mention wire behavior.

## Runtime Route

```text
FFmpeg -> MediaMTX /cam0 -> Browser video
MediaMTX RTSP /cam0 -> Camera Hub ffmpeg-pipe -> WebSocket topics
```

Default local endpoints:

| Surface | URL |
| --- | --- |
| Browser video | `http://127.0.0.1:8889/cam0` |
| Camera Hub topics | `ws://127.0.0.1:8765` |
| Camera Hub input | `rtsp://127.0.0.1:8554/cam0` |

## WebSocket Rules

- Localhost binding may run without auth for development.
- Non-local binding requires token auth unless `--allow-remote-unauthenticated` is explicitly used for a trusted test.
- Allowed origins must be exact origins. Wildcards are rejected.
- Query token auth is accepted for trusted local tools, but shared deployments should use a reverse proxy or backend that attaches auth headers.
- Payloads must be valid JSON with finite numeric values. `NaN` and `Infinity` are not part of the contract.

## Topic Envelope

Text topics are JSON objects. The envelope shape is:

```json
{
  "schema_version": 1,
  "topic": "/vision/sword_sign/state",
  "msg_type": "mediapipe_sword_sign/GestureState",
  "header": {
    "seq": 123,
    "stamp": 1710000000.0,
    "frame_id": "cam0"
  },
  "payload": {
    "type": "gesture_state",
    "primary": "sword_sign"
  }
}
```

Envelope rules:

- `schema_version` is a positive integer.
- `topic` starts with `/` and contains no empty path segment.
- `header.seq` is a non-negative integer.
- `header.stamp` is a finite Unix timestamp in seconds.
- `header.frame_id` identifies the camera stream, such as `cam0`.

## Topics

| Topic | Message Type | WebSocket Frame | Required In Normal Stack |
| --- | --- | --- | --- |
| `/vision/sword_sign/state` | `mediapipe_sword_sign/GestureState` | text JSON | yes |
| `/camera/status` | `mediapipe_sword_sign/CameraStatus` | text JSON | yes |
| `/camera/color/image_raw/compressed` | `sensor_msgs/CompressedImage` | binary by default | no |

The image topic is for Python image transport verification. Browser video in the normal stack uses MediaMTX.

## Gesture State Payload

`/vision/sword_sign/state` carries `payload.type = "gesture_state"` and includes:

- `schema_version`
- `sequence`
- `timestamp`
- `source`
- `hand_detected`
- `primary`
- `gestures.sword_sign`
- `gestures.victory`
- `gestures.none`
- `stable.gestures.<target>` when hold tracking is enabled

Each gesture entry has `active`, `confidence`, and optional `label`.
Stable entries have `active`, `activated`, `released`, `held_for`, and `confidence`.

## Camera Status Payload

`/camera/status` carries `payload.type = "camera_status"` and includes:

- `timestamp`
- `camera.selected_index`
- `camera.opened`
- `camera.frame_read_ok`
- `camera.source` when an RTSP or path source is used
- `frame_id`
- `fps`
- `capture.backend`
- `capture.width`
- `capture.height`
- `capture.frame_age_ms`
- `capture.read_latency_ms`
- `processors.sword_sign.enabled`
- processor metrics such as `last_frame_id`, `inference_ms`, and `publish_age_ms`

Consumers should tolerate additional fields and should key behavior on topic, message type, and documented payload fields.

## Binary Image Message

When `--publish-jpeg-every` is greater than `0`, the compressed image topic uses this binary frame by default:

```text
8 bytes   magic: "MPSSBIN1"
4 bytes   unsigned big-endian JSON header length
N bytes   UTF-8 JSON topic envelope
rest      JPEG bytes
```

`--image-transport json` exists for compatibility checks and sends JSON/base64. It is not the normal browser video path.

## Camera Hub Command

The MediaMTX route should start Camera Hub with RTSP input, `ffmpeg-pipe`, and disabled Python JPEG publishing:

```powershell
uv run python apps/serve_camera_hub.py `
  --host 127.0.0.1 `
  --port 8765 `
  --interval 0 `
  --camera-source rtsp://127.0.0.1:8554/cam0 `
  --camera-backend ffmpeg-pipe `
  --camera-width 640 `
  --camera-height 480 `
  --camera-fps 30 `
  --frame-id cam0 `
  --publish-jpeg-every 0 `
  --gesture-every 0.1 `
  --gesture-model-complexity 0 `
  --publish-landmarks
```

Any downstream script that opens Browser Monitor must also start or require MediaMTX and FFmpeg publish.
