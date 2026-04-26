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

5. 設定GUI
   `uv run python apps/settings_gui.py`

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
- `Confidence`: 現在フレームで最も高かった推論信頼度です。
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
