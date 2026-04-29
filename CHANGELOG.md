# Changelog

## 2026-04-29 - Edge-centered UDP diagnostics

モジュール利用側の追加要望を受け、汎用publisherの互換性を保ったままedge中心の運用を選べるようにしました。

- `apps/publish_udp.py`
  - `--state-every` を追加し、`0` / `off` / `none` で通常の `gesture_state` 定期送信を止められるように変更。
  - `--edge-only` を追加し、`gesture_edge` と明示的に有効化した heartbeat/status だけで運用しやすく変更。
  - `gesture_heartbeat` payloadへ `frame_id`、`fps`、`hand_detected`、`primary_gesture` を常に追加。
  - `--schema-json` の `gesture_status` / `gesture_heartbeat` 契約に diagnostic fields を反映。
- `tests/test_publish_udp.py`
  - edge-onlyオプション、state送信抑制、heartbeat diagnostic fields、schema契約の回帰テストを追加。
- `README.md`
  - edge中心運用とheartbeat diagnostic fieldsの利用方法を追記。

## 2026-04-29 - Latency output redaction hardening

latency instrumentation追加後のセキュリティ再確認として、stdout/debugへ出る実行時識別子と時刻情報の扱いを安全側に寄せました。

- `apps/publish_udp.py`
  - `--redact-output` を追加し、`--print-json` / `--status-json` の `turn_id`、token系field、wall time、monotonic timeを伏せられるように変更。
  - `--debug` のedge行は、既定で `turn_id` や絶対時刻を出さず、`pipeline_ms` と `confidence` だけを表示するように変更。
  - UDP receiverへ送るpayloadは変更せず、redactionはstdout/debug出力だけに限定。
- `tests/test_publish_udp.py`
  - redaction対象fieldとedge debug出力の回帰テストを追加。
- `README.md`
  - 共有ログや画面共有時の `--redact-output` 利用方針を追記。

## 2026-04-29 - Gesture latency instrumentation

モジュール利用側からの遅延計測依頼に合わせ、`mediapipe-sword-sign` 側で検出からUDP送信までを追えるpayloadにしました。

- `apps/publish_udp.py`
  - UDPの `gesture_state` payloadへ `frame_id`、`detected_at`、`sent_at`、`fps`、`confidence` をtop-levelで追加。
  - 同じpayloadへ `detected_at_monotonic`、`sent_at_monotonic`、`target_gesture`、stable active中の `turn_id` も追加。
  - `GestureHoldTracker` をUDP publisherでも使い、stable active/released の `gesture_edge` payloadを追加送信。
  - `--hold` / `--hold-seconds`、`--grace` / `--release-grace-seconds`、`--target-gesture` を追加。
  - `--latency-profile low` を追加し、未指定時の `threshold=0.8`、`hold=0.1s`、`grace=0.05s` をまとめて選べるように変更。
  - `--schema-json` と `--health-json` に新しい計測・profile項目を反映。
- `tests/test_publish_udp.py`
  - profile解決、UDP送信用payload、edge event payloadの回帰テストを追加。
- `README.md`
  - 低遅延profile、edge event、receiver側で見るべき到達遅延の計算方法を追記。

確認済み:

```text
uv run python -m unittest discover -s tests
Ran 50 tests ... OK
```

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
