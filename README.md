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

## 初期セットアップ

```powershell
uv sync
uv run python -m unittest discover -s tests
```

通常の統合導線では FFmpeg と MediaMTX も必要です。PowerShell から次が見えることを確認します。

```powershell
ffmpeg -version
ffprobe -version
mediamtx --version
```

学習用 CSV、`gesture_model.pkl`、replay 動画、抽出 frame、`.runtime/`、`.cache/`、`.venv/` はローカル資材です。Git に入れる前提にしないでください。

## dotenv / local config

この repo には標準の `.env.example` はありません。Camera Hub の通常起動は CLI 引数で設定します。
control-plane から起動する場合は、control-plane 側の `.env` に `MEDIAPIPE_SWORD_SIGN_*` や
FFmpeg / MediaMTX のパスを設定します。

ローカル学習済みモデルを使う場合は、信頼できる保管先に置き、必要なら起動引数または control-plane
設定で model path と SHA-256 を指定します。外部から受け取った pickle/joblib モデルを無検証で使わないでください。

## Primary Integration Route

通常の統合導線は **FFmpeg publish + MediaMTX video + Camera Hub topic** です。
Camera Hub は MediaMTX の RTSP stream を bounded OpenCV `ffmpeg` backend で読み、Python からの
JPEG 映像配信は無効にします。

```text
USB camera -> FFmpeg -> MediaMTX /cam0 -> Browser video
                         |
                         +-> RTSP -> Camera Hub -> WebSocket topics
```

```powershell
scripts\start_camera_hub_stack.bat
```

The current local capture request defaults to Logitech StreamCam at
`1920x1080`, `30 fps`, and `--ffmpeg-input-codec mjpeg`. These are requested
settings only: use the device capability listing and runtime diagnostics as the
authority for the achieved mode. The selector does not manufacture an
unsupported 60 fps mode. Other cameras may explicitly override the name, size,
fps, and codec after their local capability listing is checked.

手動で Camera Hub だけを起動する場合:

```powershell
uv run python apps/serve_camera_hub.py `
  --host 127.0.0.1 `
  --port 8765 `
  --interval 0 `
  --camera-source rtsp://127.0.0.1:8554/cam0 `
  --camera-backend ffmpeg `
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

ローカル動画を使った replay mode は開発・検証用です。実カメラの前で同じ gesture を繰り返さずに、
通常の `/camera/status` と `/vision/sword_sign/state` topic を確認できます。

```powershell
uv run python apps/serve_camera_hub.py `
  --host 127.0.0.1 `
  --port 8765 `
  --replay-video <workspace>\local\media\movie\samples20260603\sword.mp4 `
  --replay-loop `
  --interval 0.033 `
  --gesture-every 0.1 `
  --gesture-model-complexity 0
```

Home Control Stack から起動された場合、この stack script は `HOME_CONTROL_STACK_STATE_DIR` 配下に
この supervisor が起動した子プロセスだけの manifest を書きます。停止側はその manifest を使い、
Chrome など別用途のプロセスを停止対象にしません。

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
| `uv run python apps/extract_video_training_rows.py <local-video> --label sword` | ローカル動画から gesture 学習用 row を抽出 |
| `uv run train_model.py` | `gesture_model.pkl` の生成 |
| `uv run predict.py` | OpenCV 表示つき detector 確認 |
| `uv run python apps/settings_gui.py` | 閾値、hold/grace、モデル指定の調整 |
| `uv run python apps/extract_debug_frames.py <local-video>` | ローカル動画から ignored debug fixtures と detector manifest を生成 |
| `uv run python apps/serve_browser_monitor.py` | Browser Monitor を `http://127.0.0.1:8770/...` で静的配信 |
| `uv run python -m unittest discover -s tests` | 単体テスト |

統合スタックでは `scripts\start_camera_hub_stack.bat` が viewer server を起動し、
`http://127.0.0.1:8770/browser_camera_hub_viewer.html?mediaUrl=...&wsUrl=...`
を開きます。`file://` で HTML を直接開く導線は単体デバッグ用です。

`publish_udp.py`、`serve_websocket.py`、Python JPEG topic、`ffmpeg-pipe` RTSP compatibility diagnostic は通常導線ではありません。
互換・検証・切り分け用途は [Retired And Compatibility Paths](docs/retired-paths.md) にまとめています。

### Local video training-row extraction

`apps/extract_video_training_rows.py` は、実カメラの対話式収集を繰り返さずに、
ローカル動画から `train_model.py` 互換の training row CSV を作る開発・検証用 helper です。
`collect_data.py` と同じく `features_from_hand_landmarks` を使い、デフォルトでは
MediaPipe 処理前に左右反転します。

```powershell
uv run python apps/extract_video_training_rows.py `
  <local-video> `
  --label sword `
  --every-frames 5 `
  --max-rows 500
```

Label mapping:

```text
0 = sword
1 = victory
2 = none
```

Sampling behavior:

- `--start-frame` 以降を `--every-frames` 間隔で読む。
- hand landmarks が検出された frame だけ CSV row を書く。
- `--max-rows` は frame 数ではなく、書き込む hand row 数の上限。
- `--flip` / `--no-flip` で mirror 挙動を切り替える。デフォルトは `--flip`。
- デフォルト出力先は `.runtime/training_rows/`。CSV と summary は local/generated artifact として扱う。
- `--output-csv` は `--output-dir` 配下の相対パスだけを受け付ける。
- 同じ動画と同じ引数では同じ frame sampling を行う。乱数 seed は使わない。

CLI は raw row values を標準出力しません。表示するのは source filename、SHA-256、
label、件数、生成ファイル名などの summary だけです。生成 CSV、summary、候補モデルは
そのまま commit / push しないでください。

## Security Boundaries

- `.env`、ログ、CSV、`.pkl` は `.gitignore` 対象です。学習データとモデルは信頼できる保管先で管理してください。
- ローカル replay 動画、抽出フレーム、`tests/pict_for_debug/` 配下の manifest/summary は local-only fixture です。
  Git に含めず、必要な場合はパスだけを coordination ledger に記録してください。
- `gesture_model.pkl` は joblib/pickle 形式です。外部から受け取ったモデルは SHA-256 を確認して `--model-sha256` を指定してください。
- WebSocket を localhost 以外で公開する場合は token と exact origin を設定してください。wildcard origin は使いません。
- MediaMTX の外部公開、LAN 公開、TLS 終端は統合側の責務です。この module は localhost 開発を基準にしています。

## Documents

- [Module Responsibilities](docs/module-responsibilities.md): 要求仕様と責務境界。
- [Integration Contract](docs/integration-contract.md): topic envelope、payload、auth/origin。
- [Browser GUI Integration](docs/browser_gui_integration.md): Browser Monitor の接続と確認観点。
- [MediaMTX Integration](docs/mediamtx_integration.md): FFmpeg publish、MediaMTX、bounded OpenCV `ffmpeg` reader の手順。
- [Retired And Compatibility Paths](docs/retired-paths.md): 互換アダプタ、検証用導線、archive の扱い。
- [Changelog](CHANGELOG.md): 日付つきの変更要約。
