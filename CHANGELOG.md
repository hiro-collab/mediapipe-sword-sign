# Changelog

## 2026-05-01 - Stable payload security follow-up

`add stable websocket payload` 更新後のセキュリティ確認で、外部へ配信するJSONの厳格化を追加しました。

- `mediapipe_sword_sign/payloads.py`
  - `sequence`、stable target、`held_for`、`confidence` の値域検証を追加。
  - WebSocket payloadで `NaN` / `Infinity` をJSONへ出さないよう `allow_nan=False` を指定。
- `mediapipe_sword_sign/types.py` / `mediapipe_sword_sign/adapters/udp.py` / `apps/publish_udp.py`
  - `GestureState`、UDP payload、CLI JSON出力も厳格JSONに統一。
- `tests/`
  - 非有限値、負のsequence、制御文字入りstable targetの回帰テストを追加。

## 2026-05-01 - Security hardening pass

Qiita記事の観点に合わせ、ローカル実験用のままでも公開事故につながりやすい箇所を追加で固めました。

- `mediapipe_sword_sign/features.py` / `mediapipe_sword_sign/detector.py`
  - 特徴量、しきい値、MediaPipe model complexityの入力検証を追加し、NaN/Infinityや範囲外値を拒否。
- `mediapipe_sword_sign/adapters/websocket.py` / `apps/serve_websocket.py`
  - WebSocketのポート、接続数、受信メッセージサイズ、受信キューの検証と既定上限を追加。
  - 空トークンを未設定として扱い、クエリパラメータ過多の認証リクエストを拒否。
  - wildcard Originを拒否。
- `apps/settings_gui.py` / `apps/gesture_monitor_gui.py`
  - GUIの起動失敗メッセージでモデルパスなどの内部情報を直接表示しないよう変更。
  - 診断GUIでlocalhost以外にWebSocket公開する場合、Tokenなしでは開始しないよう変更。
- `SECURITY.md`
  - シークレット、pickleモデル、通信公開、入力検証、依存関係監査の運用チェックリストを追加。
- `tests/`
  - 上記の入力検証・エラー秘匿・WebSocket制限の回帰テストを追加。

## 2026-04-28 - UDP auth token support

モジュール利用側からの指摘を受け、`sword-voice-agent` など認証付きUDP receiverと安全に連携しやすいようにしました。

- `apps/publish_udp.py`
  - `SWORD_VOICE_AGENT_AUTH_TOKEN` 環境変数または `--auth-token` で、UDP payloadに `auth_token` を付与できるように変更。
  - `--auth-token-env` で参照する環境変数名を変更できるように追加。
  - `--print-json` / `--status-json` / debug出力にはtokenを出さず、UDP送信直前のpayloadだけに付与。
  - `--schema-json` に任意の `auth_token` fieldを追記。
- `mediapipe_sword_sign/adapters/udp.py`
  - `UdpGesturePublisher` が `GestureState` と汎用payloadの両方に任意の `auth_token` を付与できるように変更。
- `README.md`
  - `SWORD_VOICE_AGENT_AUTH_TOKEN` の設定方法、`--auth-token` の注意点、remote UDPではtokenが暗号化ではないことを明記。
  - `--heartbeat-every` は `sword-voice-agent` の通常receiverでは拒否または無視される場合があるため、heartbeat対応receiver向けであることを明記。

## 2026-04-28 - UDP diagnostics security hardening

更新されたUDP診断・ヘルス出力まわりを再確認し、公開時に事故になりやすい設定を安全側に寄せました。

- `apps/publish_udp.py`
  - `--port`, `--camera-index`, `--camera-scan-limit`, `--threshold`, `--interval` の入力検証を追加。
  - UDP送信先がlocalhost以外の場合、`--allow-remote-udp` を明示しない限り起動を拒否。
  - `--health-json` / `--check-config` のモデルエラーを短いエラーコードにし、内部パスやハッシュ値を出さないように変更。
- `tests/test_publish_udp.py`
  - 入力検証、非localhost UDP送信拒否、モデルエラー秘匿の回帰テストを追加。
- `pyproject.toml` / `uv.lock`
  - 直接依存を `requirements.txt` と同じ固定バージョンに揃え、環境再現性を改善。
- `README.md`
  - リモートUDP送信時の明示許可と、ヘルスチェックのエラー秘匿について追記。

確認済み:

```text
uv run python -m unittest discover -s tests
Ran 40 tests ... OK

uv run --with pip-audit pip-audit -r requirements.txt
No known vulnerabilities found

uv run python apps\publish_udp.py --schema-json
OK

uv run python apps\publish_udp.py --check-config --host 192.0.2.10
Refuses non-local UDP without --allow-remote-udp
```

## 2026-04-27 - Baseline security hardening

- joblib/pickleモデル読み込みに信頼境界を追加。
- WebSocketの非localhost公開にトークン認証・Origin制限・最大接続数を追加。
- `.env`, ログ, CSV, `.pkl` を `.gitignore` 対象に追加。
- `gesture_data.csv` と `gesture_model.pkl` はGit追跡から外し、ローカルのignoredファイルとして扱う方針に変更。
