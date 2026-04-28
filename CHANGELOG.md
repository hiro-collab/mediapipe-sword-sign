# Changelog

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
