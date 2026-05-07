# MediaMTX Integration

MediaMTX は映像の ingest と fan-out を担当します。Camera Hub は MediaMTX の RTSP stream を読み、
gesture/status/landmarks topic だけを WebSocket で配信します。

```text
USB camera -> FFmpeg publish -> MediaMTX /cam0 -> Browser video
                                      |
                                      +-> RTSP -> Camera Hub ffmpeg-pipe -> topics
```

## Responsibility Boundary

- MediaMTX: WebRTC/HLS/RTSP の映像配信。
- FFmpeg: Windows USB camera を DirectShow で開き、MediaMTX に publish。
- Camera Hub: RTSP を `ffmpeg-pipe` で読み、MediaPipe 推論と topic 配信を実行。
- Browser GUI: MediaMTX の映像と Camera Hub topic を表示。

Python からブラウザへ JPEG を配る構成は検証用です。複数クライアントや複数カメラの導線では使いません。

## Tooling

確認済みの外部ツール:

| Tool | Version | Purpose |
| --- | --- | --- |
| MediaMTX | `v1.18.1` Windows amd64 | RTSP/WebRTC/HLS media server |
| FFmpeg / ffprobe | `2026-04-30-git-cc3ca17127` gyan.dev essentials build | DirectShow capture、H.264 encode、RTSP 確認 |

Camera Hub の Python 依存だけでは、複数ブラウザへの映像 fan-out を担当しません。

## One-Terminal Stack

ローカル統合確認は stack script を使います。

```powershell
scripts\start_camera_hub_stack.bat --camera-name "HD Pro Webcam C920"
```

この script は以下を起動します。

- MediaMTX
- FFmpeg publish to `rtsp://127.0.0.1:8554/cam0`
- Camera Hub with `--camera-backend ffmpeg-pipe`
- Browser Monitor

起動時に以下の URL が terminal に表示されます。

- `Browser Monitor video`
- `Camera Hub input`
- `Camera Hub topics`

前回の MediaMTX / Camera Hub / stack port が残っている場合:

```powershell
scripts\start_camera_hub_stack.bat --camera-name "HD Pro Webcam C920" --force-stop-existing
```

ログは `.runtime\camera-hub-stack\logs` に保存されます。終了は起動した terminal で `Ctrl+C` です。

## Manual Stack

切り分け時は各コマンドを別 terminal で起動します。

1. MediaMTX を明示 publisher 設定で起動:

```powershell
mediamtx configs\mediamtx\mediamtx.publisher.example.yml
```

MediaMTX だけでは `/cam0` stream は作られません。FFmpeg publish が必要です。

2. DirectShow camera 名を確認:

```powershell
ffmpeg -list_devices true -f dshow -i dummy
```

3. `/cam0` へ publish:

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

4. RTSP path を確認:

```powershell
ffprobe -rtsp_transport tcp -v error -show_entries stream=codec_type,width,height,avg_frame_rate -of default=noprint_wrappers=1 rtsp://127.0.0.1:8554/cam0
```

5. Browser video を確認:

```text
http://127.0.0.1:8889/cam0
```

6. Camera Hub を RTSP 入力で起動:

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

`ffmpeg-pipe` は FFmpeg subprocess から raw BGR frame を受け取る backend です。
OpenCV RTSP reader の内部 buffering で landmarks が映像より遅れる場合を避けるために使います。

## Persistent Config

毎回 FFmpeg コマンドを起動しない場合は、MediaMTX の sample config をコピーして camera 名を編集します。

```powershell
Copy-Item configs\mediamtx\mediamtx.windows.example.yml mediamtx.yml
notepad mediamtx.yml
mediamtx mediamtx.yml
```

`CAMERA_0_NAME` などを `ffmpeg -list_devices` の表示名へ置き換えます。
一時 publish の確認だけなら `configs\mediamtx\mediamtx.publisher.example.yml` を使います。

## Multiple Cameras

複数 camera で gesture 認識が必要な場合は、camera ごとに MediaMTX path と Camera Hub port を分けます。

```powershell
uv run python apps/serve_camera_hub.py --port 8765 --interval 0 --camera-source rtsp://127.0.0.1:8554/cam0 --camera-backend ffmpeg-pipe --camera-width 640 --camera-height 480 --camera-fps 30 --frame-id cam0 --publish-jpeg-every 0 --gesture-every 0.1
uv run python apps/serve_camera_hub.py --port 8766 --interval 0 --camera-source rtsp://127.0.0.1:8554/cam1 --camera-backend ffmpeg-pipe --camera-width 640 --camera-height 480 --camera-fps 30 --frame-id cam1 --publish-jpeg-every 0 --gesture-every 0.1
```

多数 camera を常用する場合は、複数 worker を束ねる vision service を統合側で設計します。
この module は per-camera worker と topic 契約を提供します。

## Failure Cues

`camera not available: rtsp://127.0.0.1:8554/cam0` の主な原因:

- MediaMTX が起動していない。
- FFmpeg publish が `/cam0` へ到達していない。
- DirectShow camera 名が一致していない。
- `ffprobe rtsp://127.0.0.1:8554/cam0` が失敗している。
- OpenCV RTSP fallback を使っていて buffering または option rejection が起きている。

OpenCV RTSP fallback を比較する場合だけ `--camera-backend ffmpeg` を使います。
OpenCV が option を拒否する場合は `--opencv-ffmpeg-capture-options none` で切り分けます。

## References

- MediaMTX configuration: https://mediamtx.org/docs/usage/configuration
- MediaMTX generic webcams: https://mediamtx.org/docs/publish/generic-webcams
- MediaMTX browser reading: https://mediamtx.org/docs/read/web-browsers
- MediaMTX WebRTC reading: https://mediamtx.org/docs/read/webrtc
