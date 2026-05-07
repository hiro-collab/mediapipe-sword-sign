# Browser GUI Integration

Browser GUI は映像配信を担当しません。映像は MediaMTX、状態は Camera Hub WebSocket topic
から受け取り、UI は両方を同じ画面で表示します。

```text
MediaMTX WebRTC/HLS -> Browser video pane
Camera Hub WebSocket -> gesture/status/landmarks overlay
```

## GUI Responsibility

- USB camera を直接開かない。
- Python JPEG topic を通常の映像経路として使わない。
- `ws://127.0.0.1:8765` から `/vision/sword_sign/state` と `/camera/status` を購読する。
- landmarks overlay は Camera Hub が `--publish-landmarks` で配信した場合だけ描画する。
- 映像が空の場合は Browser 側で代替経路を作らず、MediaMTX と FFmpeg publish の起動を確認する。

## Browser Monitor

このリポジトリのローカル確認 UI は `apps/browser_camera_hub_viewer.html` です。

```powershell
Start-Process .\apps\browser_camera_hub_viewer.html
```

統合起動では、次のコマンドが MediaMTX URL と Camera Hub WebSocket URL を query parameter で渡して開きます。

```powershell
scripts\start_camera_hub_stack.bat --camera-name "HD Pro Webcam C920"
```

Browser Monitor が表示する主な情報:

- MediaMTX video pane
- gesture primary/stable state
- gesture confidence scores
- Camera Hub status
- topic age
- event log
- envelope JSON
- landmarks overlay

MediaMTX 標準ページ `http://127.0.0.1:8889/cam0` は映像だけを表示します。
gesture や landmarks の確認には Browser Monitor を使います。

## Connection Contract

GUI が実装として守るべき接続仕様は [Integration Contract](integration-contract.md) を正本にします。
特に以下を通常経路として扱います。

| Input | URL |
| --- | --- |
| Video | `http://127.0.0.1:8889/cam0?controls=false&muted=true&autoplay=true` |
| Topics | `ws://127.0.0.1:8765` |

WebSocket が localhost 以外に公開される場合、token と allowed origin の設定が必要です。
Browser の WebSocket API は任意の `Authorization` header を直接付けられないため、
共有環境では reverse proxy または app backend で token を扱います。

## Overlay Latency Probe

最終 GUI の overlay 遅延を camera なしで確認する probe があります。

```powershell
uv run python apps/measure_browser_overlay_latency.py
```

probe は `tests\pict_for_debug\hand_in.png` と `hand_out.png` を交互に表示し、
Camera Hub 互換の topic を送ります。画像はローカル検証用で `.gitignore` 対象です。
`--landmark-delay-ms 1000` を付けると計測値が約 1 秒増えるため、計測経路の確認に使えます。

probe は localhost 専用です。LAN に出す場合は、未認証の検証 endpoint を公開することを理解した上で
`--allow-remote-probe` を明示します。

## Python GUI

`apps/camera_hub_gui.py` は Python デバッグ GUI です。

```powershell
uv run python apps/camera_hub_gui.py
```

`--publish-jpeg-every 0` の MediaMTX 経路では Python GUI に映像は出ません。
映像は MediaMTX または Browser Monitor で見ます。Python GUI に JPEG preview を出す場合は、
検証対象が Python 画像 topic であることを明確にしてから `--publish-jpeg-every` を有効化します。

## Failure Cues

| Symptom | Check |
| --- | --- |
| Video pane is empty | MediaMTX 起動、FFmpeg publish、`ffprobe rtsp://127.0.0.1:8554/cam0` |
| State pane is disconnected | Camera Hub process、`ws://127.0.0.1:8765`、token/origin |
| Overlay trails video | Camera Hub が `--camera-backend ffmpeg-pipe` で RTSP を読んでいるか |
| Stable release feels slow | `--release-grace-seconds` と hold 設定 |

Browser 実装の詳細コード例は通常導線から外しました。必要な wire format は
[Integration Contract](integration-contract.md) を参照してください。
