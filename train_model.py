import os

import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPClassifier
import joblib

def train():
    if not os.path.exists('gesture_data.csv'):
        print("データファイルが見つかりません。先にデータを収集してください。")
        return

    print("データを読み込み中...")
    df = pd.read_csv('gesture_data.csv', header=None)
    X = df.iloc[:, 1:].values
    y = df.iloc[:, 0].values

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    print("学習を開始します...")
    # 少し複雑な形にも対応できるよう隠れ層を調整
    model = MLPClassifier(hidden_layer_sizes=(20, 10, 5), max_iter=2000)
    model.fit(X_train, y_train)

    print(f"学習完了。精度: {model.score(X_test, y_test):.2f}")
    joblib.dump(model, 'gesture_model.pkl')
    print("モデルを保存しました。")

if __name__ == "__main__":
    train()
