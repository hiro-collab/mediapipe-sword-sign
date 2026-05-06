# MediaMTX Integration Guide

Use MediaMTX when you need multiple cameras or more than a few browser clients.
In this architecture, MediaMTX handles video ingest and fan-out, while Python
keeps responsibility for gesture recognition and topic/status messages.

## Architecture

```text
USB/IP cameras
  -> FFmpeg / camera protocol
  -> MediaMTX
      -> Browser GUI video: WebRTC or HLS
      -> Python vision workers: RTSP
  -> Python topic hub
      -> Browser GUI state: gesture/status WebSocket topics
```

The key point is that browser video no longer flows through Python. Python reads
only the stream it needs for MediaPipe inference.

## Why MediaMTX

MediaMTX is a media router/proxy that can publish and read streams with RTSP,
WebRTC, HLS, RTMP, SRT, and related protocols. It can serve multiple streams at
separate paths such as `/cam0`, `/cam1`, and `/cam2`.

For browser viewing, MediaMTX serves WebRTC pages at:

```text
http://127.0.0.1:8889/cam0
```

For Python/OpenCV inference, the same path can be read over RTSP:

```text
rtsp://127.0.0.1:8554/cam0
```

## Windows USB Camera Setup

1. Install MediaMTX and FFmpeg.

Verified tool versions:

- MediaMTX `v1.18.1` Windows amd64
- FFmpeg `2026-04-30-git-cc3ca17127` gyan.dev essentials build

2. Start MediaMTX with the explicit publisher-path sample config.

```powershell
mediamtx configs/mediamtx/mediamtx.publisher.example.yml
```

Starting `mediamtx` alone only starts the media server. It does not create
`/cam0` by itself. A camera stream must be published to MediaMTX, either by an
FFmpeg process or by `runOnInit` entries in a MediaMTX config file.
Depending on the active default config, unconfigured paths can also make FFmpeg
publishing fail with `Server returned 400 Bad Request`. Use
`configs/mediamtx/mediamtx.publisher.example.yml` for the first smoke test.

3. List DirectShow camera device names:

```powershell
ffmpeg -list_devices true -f dshow -i dummy
```

Example output may include:

```text
"HD Pro Webcam C920" (video)
```

## Quick Smoke Test Without A Config File

With MediaMTX already running, publish one camera to `/cam0` from another
terminal:

```powershell
ffmpeg -f dshow `
  -video_size 640x480 `
  -framerate 30 `
  -i video="HD Pro Webcam C920" `
  -c:v libx264 `
  -pix_fmt yuv420p `
  -preset ultrafast `
  -tune zerolatency `
  -b:v 800k `
  -f rtsp rtsp://127.0.0.1:8554/cam0
```

Then verify the path:

```powershell
ffprobe -rtsp_transport tcp -v error -show_entries stream=codec_type,width,height,avg_frame_rate -of default=noprint_wrappers=1 rtsp://127.0.0.1:8554/cam0
```

If this works, the Python worker can read the stream:

```powershell
uv run python apps/serve_camera_hub.py `
  --host 127.0.0.1 `
  --port 8765 `
  --interval 0 `
  --camera-source rtsp://127.0.0.1:8554/cam0 `
  --camera-backend ffmpeg `
  --frame-id cam0 `
  --publish-jpeg-every 0 `
  --gesture-every 0.05 `
  --gesture-model-complexity 0 `
  --release-grace-seconds 0.03 `
  --publish-landmarks
```

## Persistent MediaMTX Config

For repeated use, copy and edit the sample config:

```powershell
Copy-Item configs/mediamtx/mediamtx.windows.example.yml mediamtx.yml
notepad mediamtx.yml
```

Replace `CAMERA_0_NAME`, `CAMERA_1_NAME`, and so on with the camera names from
FFmpeg.

Stop any already running plain `mediamtx` process, then start MediaMTX with the
edited config:

```powershell
mediamtx mediamtx.yml
```

Open a browser stream:

```text
http://127.0.0.1:8889/cam0
```

Verify RTSP before starting Python:

```powershell
ffprobe -rtsp_transport tcp -v error -show_entries stream=codec_type,width,height,avg_frame_rate -of default=noprint_wrappers=1 rtsp://127.0.0.1:8554/cam0
```

## Python Gesture Worker From MediaMTX

For each camera that needs gesture recognition, start a Python worker that reads
the MediaMTX RTSP stream. Keep Python image publishing disabled unless you are
debugging the Python pipeline.

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
  --gesture-model-complexity 0
```

`ffmpeg-pipe` reads raw BGR frames from an FFmpeg subprocess instead of using
OpenCV's RTSP `VideoCapture`. Prefer it for overlay synchronization because it
avoids the extra OpenCV RTSP buffering that can make landmarks trail live video.

If this command waits for about 30 seconds and then reports
`camera not available: rtsp://127.0.0.1:8554/cam0`, MediaMTX is reachable but
the `/cam0` stream is not ready. Check:

- MediaMTX is running.
- FFmpeg is publishing to `rtsp://127.0.0.1:8554/cam0`, or `runOnInit` is
  configured and running.
- `ffprobe rtsp://127.0.0.1:8554/cam0` succeeds.
- The camera name in the FFmpeg command exactly matches the DirectShow device
  name.

If you need gesture recognition on multiple cameras today, run one worker per
camera on separate ports:

```powershell
uv run python apps/serve_camera_hub.py --port 8765 --interval 0 --camera-source rtsp://127.0.0.1:8554/cam0 --camera-backend ffmpeg-pipe --camera-width 640 --camera-height 480 --camera-fps 30 --frame-id cam0 --publish-jpeg-every 0 --gesture-every 0.1
uv run python apps/serve_camera_hub.py --port 8766 --interval 0 --camera-source rtsp://127.0.0.1:8554/cam1 --camera-backend ffmpeg-pipe --camera-width 640 --camera-height 480 --camera-fps 30 --frame-id cam1 --publish-jpeg-every 0 --gesture-every 0.1
```

For production, prefer a dedicated multi-camera vision service that publishes
all camera states through one topic endpoint. The current worker remains useful
as a simple per-camera building block.

## Low-Latency Gesture Feedback

If the browser or Python GUI reacts slowly when the hand leaves the sword sign,
separate intentional smoothing from stream latency:

- `Target` is current-frame classification.
- `Stable` includes hold/release smoothing.
- `--release-grace-seconds` intentionally delays stable release.
- The Python GUI shows `Topic Age`; if it grows, the hub is processing old
  frames.

Low-latency worker example:

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
  --gesture-every 0.05 `
  --gesture-model-complexity 0 `
  --release-grace-seconds 0.03 `
  --publish-landmarks
```

If you need to compare against the older OpenCV RTSP reader, start with
`--camera-backend ffmpeg`. If the local OpenCV build rejects one of the FFmpeg
options, retry with:

```powershell
--opencv-ffmpeg-capture-options none
```

## Browser GUI Integration

Browser GUI should use two connections:

- Video: MediaMTX WebRTC/HLS URL, for example `http://127.0.0.1:8889/cam0`
- State: Python topic WebSocket, for example `ws://127.0.0.1:8765`

Simple WebRTC iframe:

```html
<iframe
  src="http://127.0.0.1:8889/cam0?controls=false&muted=true&autoplay=true"
  width="640"
  height="480"
  scrolling="no">
</iframe>
```

For local debugging, this repository also includes a static browser monitor:

```powershell
Start-Process .\apps\browser_camera_hub_viewer.html
```

It embeds the MediaMTX viewer and subscribes to the Camera Hub WebSocket. It can
show mirror mode, current/stable gesture state, all gesture confidence scores,
topic age, camera status, event log, last envelope JSON, and MediaPipe hand
landmarks when the hub is started with `--publish-landmarks`.

## One-Terminal Startup

For day-to-day local use on Windows, start the whole stack from one terminal:

```powershell
scripts\start_camera_hub_stack.bat --camera-name "HD Pro Webcam C920"
```

If an earlier run may still be alive, let the startup check stop matching
MediaMTX, Camera Hub, or occupied stack ports first. The supervisor does not
match arbitrary FFmpeg processes by name:

```powershell
scripts\start_camera_hub_stack.bat --camera-name "HD Pro Webcam C920" --force-stop-existing
```

This starts MediaMTX, FFmpeg camera publish, the Python Camera Hub, and the
browser debug viewer. Logs are prefixed in the same terminal and also written to:

```text
.runtime\camera-hub-stack\logs
```

Stop the stack with `Ctrl+C` in that terminal. The supervisor first asks FFmpeg
to quit, then sends an interrupt to MediaMTX and Camera Hub, then falls back to
terminating any remaining process tree.

The startup script publishes H.264 with a short GOP (`--gop 30` by default) and
starts Camera Hub with `--hub-camera-backend ffmpeg-pipe` by default. That keeps
the browser video on MediaMTX while making the Python inference reader avoid
OpenCV RTSP buffering. Use `--hub-camera-backend ffmpeg` only when comparing
against the older OpenCV-backed path.

If `ffprobe` is slow or flaky on a live RTSP stream, use `--skip-rtsp-wait` to
start Camera Hub without the extra probe step.

For a richer GUI, use MediaMTX's WebRTC JavaScript reader and render into a
`<video>` element, then subscribe to Python gesture/status topics separately.

## Sizing Guidance

For 5 cameras and 5-6 browser clients:

- Let MediaMTX distribute video.
- Keep Python JPEG topic disabled: `--publish-jpeg-every 0`.
- Start with 640x480 streams.
- Use WebRTC for low latency, HLS when connectivity is easier than latency.
- Run MediaPipe only on cameras that need gesture recognition.
- Use `--gesture-every 0.1` to `0.2` to cap inference cost.

Avoid sending every camera as Python JPEG WebSocket frames to every browser.
That path scales bandwidth and CPU poorly.

## When To Consider LiveKit

MediaMTX is a good first media router for LAN and local dashboard use. Consider
LiveKit or another WebRTC SFU when:

- remote internet clients are common,
- many users need adaptive bitrate behavior,
- rooms, participants, permissions, and WebRTC lifecycle management become core
  application concerns,
- you need mobile SDKs or cloud scaling.

## References

- MediaMTX configuration: https://mediamtx.org/docs/usage/configuration
- MediaMTX generic webcams: https://mediamtx.org/docs/publish/generic-webcams
- MediaMTX browser reading: https://mediamtx.org/docs/read/web-browsers
- MediaMTX WebRTC reading: https://mediamtx.org/docs/read/webrtc
