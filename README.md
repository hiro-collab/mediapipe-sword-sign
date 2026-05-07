# MediaPipe Sword Sign Camera Hub

MediaPipe Hands と学習済みモデルで `sword_sign` / `victory` / `none` を判定する
ローカル vision module です。単体では Python API とデバッグ用スクリプトを提供し、
統合時は Camera Hub が gesture/status topic を WebSocket で配信します。

## Module Role

- `mediapipe_sword_sign/`: BGR frame から `GestureState` を返す gesture detector core。
- `apps/serve_camera_hub.py`: カメラ入力を読み、gesture/status/landmarks を topic envelope で配信する Camera Hub。
- `apps/serve_browser_monitor.py`: Browser Monitor HTML を localhost HTTP で配る静的 viewer server。
- `scripts/start_camera_hub_stack.bat`: MediaMTX、FFmpeg publish、Camera Hub、Browser Monitor をまとめて起動するローカル統合入口。
- Home Control の操作実行、UI の業務ロジック、複数ブラウザへの映像配信はこの module の責務ではありません。

## Primary Integration Route

通常の統合導線は **FFmpeg publish + MediaMTX video + Camera Hub topic** です。
Camera Hub は MediaMTX の RTSP stream を `ffmpeg-pipe` backend で読み、Python からの
JPEG 映像配信は無効にします。

```text
USB camera -> FFmpeg -> MediaMTX /cam0 -> Browser video
                         |
                         +-> RTSP -> Camera Hub -> WebSocket topics
```

```powershell
scripts\start_camera_hub_stack.bat --camera-name "HD Pro Webcam C920"
```

手動で Camera Hub だけを起動する場合:

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

MediaMTX と FFmpeg の詳細手順は [MediaMTX Integration](docs/mediamtx_integration.md) を参照してください。

## Topic Surface

Camera Hub の通常購読先は `ws://127.0.0.1:8765` です。

| Topic | Frame | Purpose |
| --- | --- | --- |
| `/vision/sword_sign/state` | text JSON | `GestureState` と hold/grace 後の stable 判定 |
| `/camera/status` | text JSON | capture FPS、frame id、backend、processor 状態 |
| `/camera/color/image_raw/compressed` | binary | Python JPEG preview。検証時だけ `--publish-jpeg-every` で有効化 |

Topic envelope と payload の接続契約は [Integration Contract](docs/integration-contract.md) に分離しています。

## Python Detector API

```python
from mediapipe_sword_sign import SwordSignDetector

detector = SwordSignDetector()
state = detector.detect(frame_bgr)

print(state.sword_sign.active)
print(state.sword_sign.confidence)
print(state.to_json())
```

一定時間の継続判定は `GestureHoldTracker` が担当します。`SwordSignDetector` 自体は
1 frame の分類結果を返します。

## Local Tools

| Command | Purpose |
| --- | --- |
| `uv run collect_data.py` | gesture 学習用 CSV の収集 |
| `uv run train_model.py` | `gesture_model.pkl` の生成 |
| `uv run predict.py` | OpenCV 表示つき detector 確認 |
| `uv run python apps/settings_gui.py` | 閾値、hold/grace、モデル指定の調整 |
| `uv run python apps/serve_browser_monitor.py` | Browser Monitor を `http://127.0.0.1:8770/...` で静的配信 |
| `uv run python -m unittest discover -s tests` | 単体テスト |

統合スタックでは `scripts\start_camera_hub_stack.bat` が viewer server を起動し、
`http://127.0.0.1:8770/browser_camera_hub_viewer.html?mediaUrl=...&wsUrl=...`
を開きます。`file://` で HTML を直接開く導線は単体デバッグ用です。

`publish_udp.py`、`serve_websocket.py`、Python JPEG topic、OpenCV RTSP fallback は通常導線ではありません。
互換・検証・切り分け用途は [Retired And Compatibility Paths](docs/retired-paths.md) にまとめています。

## Security Boundaries

- `.env`、ログ、CSV、`.pkl` は `.gitignore` 対象です。学習データとモデルは信頼できる保管先で管理してください。
- `gesture_model.pkl` は joblib/pickle 形式です。外部から受け取ったモデルは SHA-256 を確認して `--model-sha256` を指定してください。
- WebSocket を localhost 以外で公開する場合は token と exact origin を設定してください。wildcard origin は使いません。
- MediaMTX の外部公開、LAN 公開、TLS 終端は統合側の責務です。この module は localhost 開発を基準にしています。

## Documents

- [Module Responsibilities](docs/module-responsibilities.md): 要求仕様と責務境界。
- [Integration Contract](docs/integration-contract.md): topic envelope、payload、auth/origin。
- [Browser GUI Integration](docs/browser_gui_integration.md): Browser Monitor の接続と確認観点。
- [MediaMTX Integration](docs/mediamtx_integration.md): FFmpeg publish、MediaMTX、`ffmpeg-pipe` の手順。
- [Retired And Compatibility Paths](docs/retired-paths.md): 互換アダプタ、検証用導線、archive の扱い。
- [Changelog](CHANGELOG.md): 日付つきの変更要約。
