# MediaPipe Sword Sign Recognition

MediaPipeを使用した「刀印」および「チョキ」のジェスチャー判定モジュールです。
実験用スクリプトから検出ロジックを切り出し、外部アプリから `detect(frame) -> GestureState`
として利用できる形にしています。

更新履歴とセキュリティ対策の要点は `CHANGELOG.md` にまとめています。

## Architecture

このプロジェクトは Ports and Adapters に寄せた構成です。

- `mediapipe_sword_sign/`: gesture core。特徴量作成、モデル推論、`GestureState` protocolを担当
- `collect_data.py`: OpenCV + MediaPipeを使うデータ収集アダプタ
- `predict.py`: OpenCV表示つきのデバッグアプリ
- `train_model.py`: CSVから `gesture_model.pkl` を作る学習スクリプト

## Python API

```python
from mediapipe_sword_sign import SwordSignDetector

detector = SwordSignDetector()
state = detector.detect(frame_bgr)

print(state.sword_sign.active)
print(state.sword_sign.confidence)
print(state.to_json())
```

`GestureState.to_json()` は、Dify連携やWebSocket配信で使いやすいJSONを返します。

```json
{
  "type": "gesture_state",
  "timestamp": 1710000000.0,
  "source": "mediapipe_sword_sign",
  "hand_detected": true,
  "primary": "sword_sign",
  "gestures": {
    "sword_sign": { "active": true, "confidence": 0.92, "label": 0 },
    "victory": { "active": false, "confidence": 0.04, "label": 1 },
    "none": { "active": false, "confidence": 0.04, "label": 2 }
  }
}
```

## Scripts

1. データの収集
   `uv run collect_data.py`
2. モデルの学習
   `uv run train_model.py`
3. 推論の実行
   `uv run predict.py`
4. 単体テスト
   `uv run python -m unittest discover -s tests`

5. 設定GUI
   `uv run python apps/settings_gui.py`

## Security Notes

このリポジトリはローカル実験用ですが、公開・共有する場合は以下を守ってください。

- `.env`、ログ、CSV、`.pkl` は `.gitignore` 対象です。収集データや学習済みモデルは、公開リポジトリではなく信頼できる保管先で管理してください。
- `gesture_model.pkl` は joblib/pickle 形式です。信頼できない `.pkl` を読み込むと任意コード実行につながるため、既定ではプロジェクト配下のモデルだけを読み込みます。
- 外部から受け取ったモデルを使う場合は、SHA-256を確認して `--model-sha256` を指定してください。どうしても検証なしで読む場合だけ `--allow-untrusted-model` を明示します。

```powershell
Get-FileHash .\gesture_model.pkl -Algorithm SHA256
uv run python apps/settings_gui.py
uv run python apps/serve_websocket.py --model-path .\gesture_model.pkl --model-sha256 <SHA256>
```

WebSocketをlocalhost以外に公開する場合は、トークンなしでは起動を拒否します。トークンはコマンドライン引数ではなく環境変数で渡してください。

```powershell
$env:GESTURE_WS_TOKEN = "<random-token>"
uv run python apps/serve_websocket.py --host 0.0.0.0 --auth-token-env GESTURE_WS_TOKEN --allowed-origin http://localhost:3000
```

クライアントは `Authorization: Bearer <token>`、`X-Gesture-Token`、または検証済みローカル用途に限って `?token=<token>` で接続できます。URLクエリはログに残りやすいので、実運用ではヘッダーを使ってください。

## Without uv

uvがない環境でも、Pythonの仮想環境とpipで実行できます。
プロジェクトを作り直す必要はありません。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python apps/settings_gui.py
```

学習や推論も同じ環境で実行できます。

```powershell
python collect_data.py
python train_model.py
python predict.py
```

## Settings GUI

設定GUIは、モデルや判定しきい値を調整しながら現在フレーム判定と継続判定を確認するためのデバッグツールです。

起動すると、まず `MediaPipe Sword Sign Settings` という設定ウィンドウが表示されます。
`Start` を押すとカメラが開き、`Gesture Settings Preview` というOpenCVのプレビューウィンドウも表示されます。

```bash
uv run python apps/settings_gui.py
```

主な操作項目です。

- `Model`: 使用する `gesture_model.pkl` を指定します。通常は初期値のままで動きます。
- `Camera`: 使用するカメラ番号です。内蔵カメラは多くの場合 `0` です。
- `Threshold`: 1フレームごとの推論結果を active とみなす信頼度しきい値です。
- `Hold`: current frame の active 判定が何秒続いたら stable active とみなすかを指定します。
- `Grace`: 一瞬だけ手が外れた場合に、stable active を維持する猶予時間です。
- `Target`: 継続判定の対象gestureです。通常は `sword_sign` を選びます。
- `Mirror`: プレビューと判定入力を左右反転します。
- `Landmarks`: プレビューにMediaPipeの手ランドマークを表示します。
- `Preview`: OpenCVプレビューウィンドウの表示/非表示を切り替えます。

状態表示の見方です。

- `Current`: `SwordSignDetector` による現在フレームの判定です。
- `Best Confidence`: 現在フレームで最も高かった推論信頼度です。
- `Target Confidence`: `Target` で選択しているgestureの現在フレーム信頼度です。
- `Hold State`: `Hold` と `Grace` を加味した継続判定の状態です。
- `Held For`: 対象gestureが継続している時間です。
- `Event`: stable active への切り替わり、または release を表示します。

停止するには設定ウィンドウの `Stop` を押します。プレビューウィンドウが有効な場合は、プレビュー側で `Esc` を押しても停止できます。

## Temporal Gesture State

`SwordSignDetector` は現在フレームの判定だけを担当します。
「一定時間継続したら有効」といった時間判定は `GestureHoldTracker` で後段処理します。

```python
from mediapipe_sword_sign import GestureHoldTracker, SwordSignDetector

detector = SwordSignDetector()
hold = GestureHoldTracker(target="sword_sign", hold_seconds=0.5, release_grace_seconds=0.1)

state = detector.detect(frame_bgr)
stable = hold.update(state)

print(state.sword_sign.active)  # current frame
print(stable.active)            # duration-based state
```

## Adapters

UDPで `GestureState` JSONを送る場合:

```bash
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --print-json
```

UDP送信先はデフォルトではlocalhost系だけを許可します。別マシンへ送る場合は、ジェスチャー状態をネットワークへ出すことを確認したうえで `--allow-remote-udp` を付けてください。

```bash
uv run python apps/publish_udp.py --host 192.0.2.10 --port 8765 --allow-remote-udp
```

UDP受信側がpayload内の `auth_token` を検証する場合は、起動前に `SWORD_VOICE_AGENT_AUTH_TOKEN` を設定してください。
設定されている場合、UDPで送る `gesture_state` / `gesture_status` / `gesture_heartbeat` payloadに `auth_token` を追加します。

```powershell
$env:SWORD_VOICE_AGENT_AUTH_TOKEN = "<random-token>"
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765
```

検証用に `--auth-token <token>` で直接指定することもできますが、コマンド履歴やプロセス一覧に残りやすいため、共有環境や常用起動では環境変数を使ってください。
`auth_token` は受信側が送信元を拒否するための共有秘密であり、暗号化ではありません。リモートUDPは信頼できるLAN、VPN、またはトンネル内で使ってください。

`apps/publish_udp.py` はデフォルトではGUIを出さず、UDP送信だけを行います。
カメラ入力、手検出、分類信頼度、UDP送信先を1行サマリで確認したい場合は `--debug` を指定します。
`--debug-every 30` は30フレームごと、`--debug-every 2s` は2秒ごとに表示します。

```bash
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --debug --debug-every 30
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --debug --debug-every 2s
```

録音開始までの遅延を詰める検証では、`--latency-profile low` を指定すると `threshold=0.8`、
`hold=0.1s`、`grace=0.05s` を既定値として使います。誤検出とのバランスを見る場合は、presetを使ったまま個別値だけ上書きできます。

```bash
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --latency-profile low --debug
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --latency-profile low --threshold 0.86 --hold 0.15 --grace 0.05
```

OpenCVのプレビューウィンドウが必要な場合だけ `--preview` を指定します。
プレビューには `primary`、`sword confidence`、`hand detected`、UDP送信先をoverlay表示します。

```bash
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --preview --debug
```

MediaPipe経由でprotobufのdeprecation warningが通常ログに混ざる場合は、必要なときだけ
`--suppress-protobuf-warnings` を追加してください。デフォルトではwarningの扱いを変更しません。

統合側から起動前確認や機械読み取りを行うための汎用オプションもあります。

```bash
uv run python apps/publish_udp.py --version
uv run python apps/publish_udp.py --schema-json
uv run python apps/publish_udp.py --list-cameras
uv run python apps/publish_udp.py --health-json --host 127.0.0.1 --port 8765
uv run python apps/publish_udp.py --check-config --host 127.0.0.1 --port 8765
```

`--health-json` と `--check-config` のモデルエラーは、内部パスやハッシュ値を出さない短いエラーコードで返します。

統合起動側が残プロセスを安全に扱うため、長時間起動時は `--runtime-status-file` を指定できます。
起動後に `module`、`pid`、`parent_pid`、`started_at`、UDP送信先の `host` / `port`、
`health_url`、`shutdown_url` または `shutdown_command`、redact済みの `command_line` をJSONで書きます。
正常終了時は同じファイルへ `state: "stopped"`、`stopped_at`、`exit_reason` を書きます。削除はしません。

```bash
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --runtime-status-file .runtime/mediapipe-sword-sign.json
```

任意で control HTTP を有効化できます。`--control-http-port` はstrict portです。使用中なら別portへ逃げずにエラー終了します。
`GET /health` は `ok`、`module`、`pid`、`uptime_s`、control HTTP の `host` / `port` を返します。
`POST /shutdown` は即時killではなくメインループへ停止要求を送り、カメラ、UDP socket、OpenCV windowを通常終了経路で閉じます。

```bash
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --control-http-port 18765 --runtime-status-file .runtime/mediapipe-sword-sign.json
```

control HTTPをloopback以外へbindする場合はtokenが必須です。tokenは `Authorization: Bearer ...` または
`X-Sword-Agent-Token` で渡します。起動引数に直接書く代わりに、通常は `SWORD_AGENT_CONTROL_TOKEN` を使ってください。

```powershell
$env:SWORD_AGENT_CONTROL_TOKEN = "<random-token>"
uv run python apps/publish_udp.py --control-http-host 0.0.0.0 --control-http-port 18765 --control-token-env SWORD_AGENT_CONTROL_TOKEN
```

実行中の状態をJSON行で監視したい場合は `--status-json` を指定します。
`--status-every` は `--debug-every` と同じく、裸の数値ならフレーム間隔、`s` 付きなら秒間隔です。

```bash
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --status-json --status-every 1s
```

UDP受信側へ送信中であることを示すheartbeatを出したい場合は、明示的に `--heartbeat-every` を指定します。
既存receiverとの互換性を保つため、heartbeatはデフォルトでは送信しません。
`sword-voice-agent` の通常UDP receiverは `gesture_state` を処理対象にするため、`gesture_heartbeat` は拒否または無視されることがあります。
heartbeatは、そのmessage typeを明示的に扱う受信側との疎通確認に限定してください。
heartbeat payloadには `frame_id`、`fps`、`hand_detected`、`primary_gesture` を含めます。

```bash
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --heartbeat-every 5s
```

通常の `GestureState` payloadには、送信アダプタが汎用 `metadata` を追加します。
UDP送信時のpayloadには top-level と `metadata` の両方に `frame_id`、`detected_at`、
`detected_at_monotonic`、`fps`、`confidence`、`target_gesture` を含めます。
送信直前には top-level に `sent_at` と `sent_at_monotonic` も入ります。
受信側は到達時刻を `received_at` としてログに出せば、`received_at - detected_at` と
`received_at - sent_at` でジェスチャー検出からreceiver到達までの遅延を確認できます。
`*_monotonic` は送信プロセス内の順序確認用なので、別プロセスの到達差分はwall time側で見てください。

`--hold` / `--grace` で指定した stable 判定が切り替わった瞬間だけ、追加で `gesture_edge`
payloadを送ります。`event` は `gesture_active` または `gesture_released` で、stable active中は
同じ `turn_id` を通常の `gesture_state` と edge payload に付与します。

既定では互換性のため `gesture_state` を毎フレーム送ります。UDP量を抑え、edge中心で受けたい場合は
`--state-every off` または `--edge-only` を指定してください。この場合も `gesture_edge` は即時送信され、
明示的に有効化した heartbeat/status で `fps`、`hand_detected`、`primary_gesture` を確認できます。

```bash
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --edge-only --heartbeat-every 1s
```

`--print-json` を使うと、UDPへ送るpayloadを同じJSON Lines形式で確認できます。
共有ログや画面共有で `turn_id`、wall time、monotonic timeを出したくない場合は `--redact-output` を併用してください。
`--redact-output` はstdout/debug出力だけを伏せ、UDP receiverへ送るpayloadは変更しません。

```bash
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --print-json --redact-output
```

起動時は選択カメラとUDP送信先をstderrに短く表示し、終了時は `stopped` をstderrに出します。
ログ、`--print-json`、`--status-json` にはtokenやAPI keyなどの秘密情報を出さない方針です。
`--debug` のedge行は、既定で `turn_id` や絶対時刻を出さず、検出から送信payload生成までの `pipeline_ms` だけを表示します。

WebSocketで接続中のクライアントへbroadcastする場合:

```bash
uv run python apps/serve_websocket.py --host 127.0.0.1 --port 8765
```

Difyや音声入力とのつなぎ方は、実運用コードではなく参考サンプルとして
`examples/sword_push_to_talk_sample.py` に置いています。
