# Security Policy

このプロジェクトはローカル実験用のジェスチャー認識モジュールですが、WebSocket/UDPで外部アプリと接続できるため、共有・公開前に以下を確認してください。

## Secrets

- `.env`、APIキー、共有トークンはGitに入れないでください。
- WebSocketトークンは `GESTURE_WS_TOKEN`、UDPトークンは `SWORD_VOICE_AGENT_AUTH_TOKEN` などの環境変数から渡してください。
- コマンドライン引数でトークンを渡すと、履歴やプロセス一覧に残ることがあります。

## Model Files

- `gesture_model.pkl` は joblib/pickle 形式です。信頼できないファイルを読み込むと任意コード実行につながります。
- 既定ではプロジェクト配下のモデルだけを読み込みます。
- 外部から受け取ったモデルを使う場合は、SHA-256を確認して `--model-sha256` を指定してください。
- `--allow-untrusted-model` は検証用の最後の逃げ道です。共有環境や常用起動では使わないでください。

## Network Exposure

- WebSocketとUDPの既定値は `127.0.0.1` です。
- WebSocketをlocalhost以外に公開する場合は、トークンが必須です。
- WebSocketには最大接続数、受信メッセージサイズ、受信キューの上限があります。必要以上に大きくしないでください。
- `--allowed-origin` を使い、ブラウザ接続元を必要なOriginだけに絞ってください。
- UDPの `auth_token` は送信元確認用の共有秘密であり、暗号化ではありません。リモートUDPは信頼できるLAN、VPN、またはトンネル内だけで使ってください。

## Inputs And Errors

- 特徴量としきい値は有限値・範囲内で検証します。
- GUI/CLIでは、モデルパスや内部パスをそのままユーザー向けエラーに出さない方針です。
- ログやJSON出力にトークン、APIキー、個人情報を入れないでください。

## Dependency Checks

依存関係は `pyproject.toml`、`requirements.txt`、`uv.lock` で固定しています。公開前や定期確認では以下を実行してください。

```powershell
uv run python -m unittest discover -s tests
uv run --with pip-audit pip-audit -r requirements.txt
```

脆弱性が見つかった場合は、影響範囲を確認してから固定バージョンを更新してください。
