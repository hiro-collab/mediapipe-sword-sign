# Browser GUI Integration Guide

This guide describes how to build a browser-based GUI that subscribes to
`apps/serve_camera_hub.py`.

The camera hub owns the physical camera in one Python process and publishes
gesture state, camera status, and optional JPEG preview frames over WebSocket.
Browser clients should subscribe to these topics instead of opening the camera
directly.

For multiple cameras or more than a handful of browser clients, use MediaMTX for
video delivery and keep this Python hub focused on gesture/status topics. See
[MediaMTX Integration Guide](mediamtx_integration.md).

## Recommended Production Shape

For 5 cameras and 5-6 browser clients, do not send all video through Python
WebSocket frames. Use:

- MediaMTX WebRTC/HLS for browser video.
- Python topic WebSocket for gesture/status data.
- `--camera-source rtsp://127.0.0.1:8554/cam0` when Python inference should read
  from MediaMTX instead of directly opening a USB camera.
- `--camera-backend ffmpeg-pipe` for the Python RTSP reader so landmarks do not
  trail the live MediaMTX browser video because of OpenCV RTSP buffering.
- `--publish-jpeg-every 0` in Python workers unless debugging the Python image
  pipeline.

For a ready-made local debug monitor, open:

```powershell
Start-Process .\apps\browser_camera_hub_viewer.html
```

It has no npm or browser library dependency. It embeds the MediaMTX viewer,
connects to the Camera Hub WebSocket, and shows mirror mode, gesture/status
fields, gesture confidence scores, topic age, event log, last envelope JSON, and
hand landmarks when the hub is started with `--publish-landmarks`.

To measure whether the final browser overlay itself is late, run the camera-free
latency probe:

```powershell
uv run python apps/measure_browser_overlay_latency.py
```

The probe uses `tests\pict_for_debug\hand_in.png` and `hand_out.png`, starts a
local HTTP media page plus a Camera Hub-compatible WebSocket, and prints an
`open viewer:` URL for `apps\browser_camera_hub_viewer.html`. The viewer's
`Latency Probe` panel reports `Last Delta`, `Average`, `P95`, and `Min / Max`.
Use `--landmark-delay-ms 1000` as a sanity check; the reported delta should grow
by roughly one second.
The probe binds to localhost by default. If you bind it to a non-local host, add
`--allow-remote-probe` explicitly because the probe endpoints are intentionally
unauthenticated test fixtures.

## Recommended Hub Startup

The following all-Python startup is useful for local debugging with one camera.
For multi-camera browser distribution, prefer the MediaMTX setup above.

For a local Windows setup with a 640x480 preview, 20fps image topic, and about
5-6 browser clients:

```powershell
uv run python apps/serve_camera_hub.py `
  --host 127.0.0.1 `
  --port 8765 `
  --camera-backend dshow `
  --camera-width 640 `
  --camera-height 480 `
  --camera-fps 30 `
  --camera-fourcc MJPG `
  --publish-jpeg-every 0.05 `
  --jpeg-quality 60 `
  --gesture-every 0.1 `
  --gesture-model-complexity 0 `
  --max-clients 6
```

Notes:

- `--publish-jpeg-every 0.05` targets 20fps for the image topic.
- `--gesture-every 0.1` limits MediaPipe gesture inference to about 10fps.
- `--gesture-model-complexity 0` reduces MediaPipe CPU usage at some accuracy cost.
- `--jpeg-quality 50` to `70` is usually a good range for browser previews.
- `--max-clients 6` makes the expected fan-out explicit.

If gesture accuracy is more important than CPU, use `--gesture-model-complexity 1`.
If CPU or network load is still too high, try `--publish-jpeg-every 0.066`
for about 15fps.

## WebSocket Topics

The hub publishes topic envelopes. Gesture and status topics are text JSON
messages. The image topic is binary by default.

| Topic | WebSocket frame | Description |
| --- | --- | --- |
| `/vision/sword_sign/state` | text JSON | Gesture state and hold/stable state |
| `/camera/status` | text JSON | Camera state, capture fps, frame id, capture properties |
| `/camera/color/image_raw/compressed` | binary | JPEG preview frame with a topic envelope header |

JSON envelopes have this shape:

```json
{
  "schema_version": 1,
  "topic": "/vision/sword_sign/state",
  "msg_type": "mediapipe_sword_sign/GestureState",
  "header": {
    "seq": 123,
    "stamp": 1710000000.0,
    "frame_id": "camera"
  },
  "payload": {
    "type": "gesture_state",
    "primary": "sword_sign"
  }
}
```

## Binary Image Message Format

The binary image topic uses a small envelope prefix followed by JPEG bytes.

```text
8 bytes   magic: "MPSSBIN1"
4 bytes   unsigned big-endian JSON header length
N bytes   UTF-8 JSON topic envelope
rest      JPEG bytes
```

The JSON header is the same topic envelope shape as text messages. For image
frames, the payload contains metadata such as `format`, `encoding`, `transport`,
and `byte_length`. The actual JPEG data is the byte payload after the header.

## Minimal Browser Client

```html
<img id="preview" width="640" height="480" />
<pre id="state"></pre>

<script type="module">
const MAGIC = "MPSSBIN1";
const MAGIC_BYTES = new TextEncoder().encode(MAGIC);
const decoder = new TextDecoder();

const preview = document.querySelector("#preview");
const state = document.querySelector("#state");
let previousUrl = null;

function parseBinaryTopicMessage(buffer) {
  const bytes = new Uint8Array(buffer);
  for (let index = 0; index < MAGIC_BYTES.length; index += 1) {
    if (bytes[index] !== MAGIC_BYTES[index]) {
      throw new Error("unknown binary topic message");
    }
  }

  const view = new DataView(buffer);
  const headerLength = view.getUint32(MAGIC_BYTES.length, false);
  const headerStart = MAGIC_BYTES.length + 4;
  const headerEnd = headerStart + headerLength;
  const envelope = JSON.parse(decoder.decode(bytes.slice(headerStart, headerEnd)));
  const jpegBytes = buffer.slice(headerEnd);
  return { envelope, jpegBytes };
}

function renderJpeg(jpegBytes) {
  const url = URL.createObjectURL(new Blob([jpegBytes], { type: "image/jpeg" }));
  preview.onload = () => {
    if (previousUrl) {
      URL.revokeObjectURL(previousUrl);
    }
    previousUrl = url;
  };
  preview.src = url;
}

function handleEnvelope(envelope) {
  if (envelope.topic === "/vision/sword_sign/state") {
    state.textContent = JSON.stringify(envelope.payload, null, 2);
  } else if (envelope.topic === "/camera/status") {
    console.debug("camera status", envelope.payload);
  }
}

const ws = new WebSocket("ws://127.0.0.1:8765");
ws.binaryType = "arraybuffer";

ws.onmessage = (event) => {
  if (typeof event.data === "string") {
    handleEnvelope(JSON.parse(event.data));
    return;
  }

  const { envelope, jpegBytes } = parseBinaryTopicMessage(event.data);
  if (envelope.topic === "/camera/color/image_raw/compressed") {
    renderJpeg(jpegBytes);
  }
};
</script>
```

## Browser App Architecture

For a smooth GUI, keep video frames out of normal app state.

- Store the latest image frame separately from React/Vue/Svelte state.
- Render only the latest image; do not queue old frames.
- Use `requestAnimationFrame` if drawing to a `<canvas>`.
- Avoid displaying full image topic JSON in a live debug panel.
- Throttle status and gesture UI updates to 5-10Hz if the framework rerenders
  a large tree.
- In React, keep image URLs or `ImageBitmap` objects in refs instead of state
  when possible.

If the app needs overlays, draw them on a canvas after the JPEG image is decoded.
If CPU becomes high in the browser, make overlays optional and update them at a
lower rate than the image.

## 5-6 Client Sizing

The hub encodes one JPEG frame and sends it to each connected client. Network
fan-out still scales with client count.

Approximate bandwidth:

```text
40 KB/frame * 20 fps = 800 KB/s per client
800 KB/s * 6 clients = 4.8 MB/s total
```

Actual values depend on scene detail and `--jpeg-quality`.

Recommended starting point for 5-6 clients:

- 640x480 image topic
- 20fps image topic: `--publish-jpeg-every 0.05`
- JPEG quality 50-60
- gesture inference 5-10fps: `--gesture-every 0.1` to `0.2`
- `--max-clients 6`

If clients run on different machines, prefer a wired LAN or reliable Wi-Fi.
For HD, 30fps, or many more clients, consider moving video preview to WebRTC or
an MJPEG/H.264 streaming service and keeping this hub for gesture/status topics.

## Authentication And Origins

When the hub binds to localhost, authentication is optional. When binding to a
non-local host, the server refuses unauthenticated startup unless
`--allow-remote-unauthenticated` is explicitly set.
Wildcard WebSocket origins are rejected; list the exact browser origins that
should connect.

For remote browser clients, use a token:

```powershell
$env:CAMERA_HUB_WS_TOKEN = "<random-token>"
uv run python apps/serve_camera_hub.py `
  --host 0.0.0.0 `
  --auth-token-env CAMERA_HUB_WS_TOKEN `
  --allowed-origin http://localhost:5173
```

The browser WebSocket API cannot set custom `Authorization` headers directly.
Use one of these approaches:

- Preferred for deployed apps: connect through an application backend or reverse
  proxy that adds the `Authorization: Bearer ...` header.
- Acceptable for local trusted tools: use `ws://host:8765?token=<token>`.

Query tokens can appear in logs and browser history, so avoid them for shared or
internet-facing deployments. Use `wss://` behind a TLS reverse proxy when the
connection leaves localhost or a trusted LAN.

## Verification Checklist

Before handing a browser GUI to users:

- Confirm `/camera/status` reports the expected `capture.fps`, width, height,
  and backend.
- Confirm image topic fps with a separate lightweight WebSocket client.
- Confirm browser CPU with 1, 3, and 6 connected clients.
- Confirm old image frames are dropped instead of queued.
- Confirm the app reconnects after hub restart.
- Confirm origin and token settings before remote access.

## Useful References

- Hub server: `apps/serve_camera_hub.py`
- Python monitor GUI behavior: `apps/camera_hub_gui.py`
- Topic constants and binary parser: `mediapipe_sword_sign/topics.py`
