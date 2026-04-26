# MediaPipe Sword Sign Recognition

MediaPipeを使用した「刀印」および「チョキ」のジェスチャー判定モジュールです。
実験用スクリプトから検出ロジックを切り出し、外部アプリから `detect(frame) -> GestureState`
として利用できる形にしています。

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

## Adapters

UDPで `GestureState` JSONを送る場合:

```bash
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --print-json
```

WebSocketで接続中のクライアントへbroadcastする場合:

```bash
uv run python apps/serve_websocket.py --host 127.0.0.1 --port 8765
```

Difyや音声入力とのつなぎ方は、実運用コードではなく参考サンプルとして
`examples/sword_push_to_talk_sample.py` に置いています。
