# MediaPipe Sword Sign Recognition

MediaPipeを使用した「刀印」および「チョキ」のジェスチャー判定モジュールです。
実験用スクリプトから検出ロジックを切り出し、外部アプリから `detect(frame) -> GestureState`
として利用できる形にしています。

更新履歴は `CHANGELOG.md`、セキュリティ運用の確認事項は `SECURITY.md` にまとめています。

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

6. 診断GUI
   `uv run python apps/gesture_monitor_gui.py`

## Security Notes

このリポジトリはローカル実験用ですが、公開・共有する場合は以下を守ってください。

- `.env`、ログ、CSV、`.pkl` は `.gitignore` 対象です。収集データや学習済みモデルは、公開リポジトリではなく信頼できる保管先で管理してください。
- `gesture_model.pkl` は joblib/pickle 形式です。信頼できない `.pkl` を読み込むと任意コード実行につながるため、既定ではプロジェクト配下のモデルだけを読み込みます。
- 外部から受け取ったモデルを使う場合は、SHA-256を確認して `--model-sha256` を指定してください。どうしても検証なしで読む場合だけ `--allow-untrusted-model` を明示します。
- 特徴量、しきい値、ポート、接続数などの外部入力はサーバー側でも検証します。GUI/CLIのエラー表示では、モデルパスなどの内部情報をそのまま出さない方針です。

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
WebSocketサーバーは既定で最大8クライアント、受信メッセージ4096 bytes、受信キュー4件に制限しています。必要がある場合だけ `--max-clients`、`--max-message-bytes`、`--max-queue` を調整してください。

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

## Gesture Monitor GUI

診断GUIは、カメラ入力、MediaPipe hand landmarks、ジェスチャー判定、WebSocket配信状態を同時に確認するための汎用デバッグツールです。
特定の受信アプリには依存せず、既存の `GestureState` JSONをWebSocketで配信します。

```bash
uv run python apps/gesture_monitor_gui.py
```

初期値では `ws://127.0.0.1:8765` でWebSocket broadcasterを起動します。
`Start` を押すとカメラ、検出器、WebSocket broadcasterが起動し、OpenCVの `Gesture Monitor Preview` ウィンドウにカメラ映像と手ランドマークを表示します。

主な確認項目です。

- `Primary`: thresholdを超えて実際に有効扱いされたgestureです。threshold未満の場合は `none` になります。
- `Best`: threshold未満でも、現在フレームで最もconfidenceが高いgestureです。
- `Raw sword_sign`: `GestureState.gestures.sword_sign.active` とconfidenceです。
- `Stable sword_sign`: `GestureHoldTracker` による継続判定後のactive状態です。
- `Confidence`: `sword_sign` のconfidenceを大きく表示します。姿勢、距離、照明、threshold調整に使います。
- `Event Log`: raw active/inactiveの変化、stable activated/releasedを記録します。
- `WebSocket`: broadcasterの起動状態です。
- `Clients`: 接続中WebSocketクライアント数です。
- `Last Client`: 最後に接続したWebSocketクライアントの接続元です。
- `FPS`: カメラ読み取りと推論の概算FPSです。
- `Last Generated JSON`: 最後に生成した `GestureState` JSONです。クライアントが0件でも更新されます。
- `Last Published At` / `Last Publish Result`: 生成時刻と、生成のみか接続中クライアントへ送信したかを表示します。

設定項目です。

- `Camera`: 使用するカメラ番号です。内蔵カメラは多くの場合 `0` です。
- `Threshold`: 1フレームごとの推論結果をactiveとみなすconfidenceしきい値です。
- `Hold`: raw activeが何秒続いたらstable activeとみなすかを指定します。
- `Grace`: 一瞬だけ手が外れた場合にstable activeを維持する猶予時間です。
- `Host` / `Port`: WebSocket broadcasterの待受アドレスです。初期値は `127.0.0.1:8765` です。
- `Token`: WebSocket接続にtokenを要求する場合に指定します。クライアントは `Authorization: Bearer <token>`、`X-Gesture-Token`、またはローカル検証用途の `?token=<token>` で接続できます。
- `Mirror`: プレビューと判定入力を左右反転します。
- `Landmarks`: プレビューにMediaPipeの手ランドマークを表示します。
- `Preview`: OpenCVプレビューウィンドウの表示/非表示を切り替えます。

`Host` に `127.0.0.1`、`localhost`、`::1` 以外を指定した場合は警告を表示します。
ローカル以外へ公開する場合は、信頼できるネットワーク内に限定し、tokenの利用を検討してください。
初期版の診断GUIでは `allowed-origin` と `max-clients` の詳細設定はGUIに出していません。`max-clients` は既存のWebSocket broadcasterの既定値を使います。

WebSocketで配信されるpayloadは `GestureState.to_json()` と互換です。
追加フィールドとして `schema_version`、単調増加する `sequence`、`GestureHoldTracker` 後のstable判定を含みます。
既存クライアントは従来どおり `gestures.sword_sign.active` と `gestures.sword_sign.confidence` を参照できます。
継続判定後の状態を使いたいクライアントは `stable.gestures.sword_sign.active` を参照してください。

```json
{
  "schema_version": 1,
  "sequence": 123,
  "type": "gesture_state",
  "timestamp": 1710000000.0,
  "source": "mediapipe_sword_sign",
  "hand_detected": true,
  "primary": "sword_sign",
  "gestures": {
    "sword_sign": {
      "active": true,
      "confidence": 0.95
    }
  },
  "stable": {
    "gestures": {
      "sword_sign": {
        "active": true,
        "activated": false,
        "released": false,
        "held_for": 0.72,
        "confidence": 0.95
      }
    }
  }
}
```

任意のWebSocketクライアントは、次のURLへ接続して `gesture_state` を受信できます。

```text
ws://127.0.0.1:8765
```

ブラウザ上のWebSocketクライアントは、接続時に `Authorization` や `X-Gesture-Token` ヘッダーを自由に付けられないことがあります。
ローカル限定の診断ではtokenなし、または `ws://127.0.0.1:8765?token=<token>` を使うのが簡単です。
LANや外部ネットワークへ公開する場合は、query tokenがログに残るリスクを避けるため、認証付きのローカルproxyやHTTPS/WSS終端を前段に置く構成を推奨します。
Pythonなど任意ヘッダーを付けられるクライアントでは `Authorization: Bearer <token>` または `X-Gesture-Token` も利用できます。

### AITuberKit GestureVoiceBridge Example

AITuberKitの `GestureVoiceBridge` と接続する場合も、診断GUI側の配信形式は汎用の `GestureState` JSONのままです。
診断GUIを初期値の `127.0.0.1:8765` で起動し、AITuberKit側で次の環境変数を設定します。

```env
NEXT_PUBLIC_GESTURE_VOICE_BRIDGE_ENABLED="true"
NEXT_PUBLIC_GESTURE_VOICE_WS_URL="ws://127.0.0.1:8765"
NEXT_PUBLIC_GESTURE_VOICE_GESTURE="sword_sign"
NEXT_PUBLIC_GESTURE_VOICE_MIN_CONFIDENCE="0.9"
```

AITuberKit側は `gestures.sword_sign.active` と `gestures.sword_sign.confidence` を参照します。
stable判定を使う場合は、受信側で `stable.gestures.sword_sign.active`、`activated`、`released` を参照してください。
heartbeatや特定パスは不要です。診断GUIでtokenを設定した場合は、AITuberKit側のURLを `ws://127.0.0.1:8765?token=<token>` のように指定してください。

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

実行中の状態をJSON行で監視したい場合は `--status-json` を指定します。
`--status-every` は `--debug-every` と同じく、裸の数値ならフレーム間隔、`s` 付きなら秒間隔です。

```bash
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --status-json --status-every 1s
```

UDP受信側へ送信中であることを示すheartbeatを出したい場合は、明示的に `--heartbeat-every` を指定します。
既存receiverとの互換性を保つため、heartbeatはデフォルトでは送信しません。
`sword-voice-agent` の通常UDP receiverは `gesture_state` を処理対象にするため、`gesture_heartbeat` は拒否または無視されることがあります。
heartbeatは、そのmessage typeを明示的に扱う受信側との疎通確認に限定してください。

```bash
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --heartbeat-every 5s
```

通常の `GestureState` payloadには、送信アダプタが汎用 `metadata` を追加します。
現在は `frame_id`、`hand_detected`、`primary_gesture`、`fps` を含みます。
起動時は選択カメラとUDP送信先をstderrに短く表示し、終了時は `stopped` をstderrに出します。
ログ、`--print-json`、`--status-json` にはtokenやAPI keyなどの秘密情報を出さない方針です。

WebSocketで接続中のクライアントへbroadcastする場合:

```bash
uv run python apps/serve_websocket.py --host 127.0.0.1 --port 8765
```

WebSocket payloadには、raw `GestureState` に加えて `schema_version`、`sequence`、stable判定が含まれます。
stable判定の時間はCLIから調整できます。

```bash
uv run python apps/serve_websocket.py --hold-seconds 0.5 --release-grace-seconds 0.1
```

Difyや音声入力とのつなぎ方は、実運用コードではなく参考サンプルとして
`examples/sword_push_to_talk_sample.py` に置いています。
