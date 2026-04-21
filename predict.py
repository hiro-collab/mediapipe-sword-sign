import cv2
import mediapipe as mp
import joblib
import numpy as np

def main():
    try:
        model = joblib.load('gesture_model.pkl')
    except:
        print("モデルファイルが見つかりません。")
        return

    mp_hands = mp.solutions.hands
    mp_drawing = mp.solutions.drawing_utils
    cap = cv2.VideoCapture(0)

    # ラベルに「None」を追加
    labels = {0: "Sword", 1: "Victory", 2: "None"}

    with mp_hands.Hands(model_complexity=1) as hands:
        while cap.isOpened():
            success, image = cap.read()
            if not success: break

            image = cv2.cvtColor(cv2.flip(image, 1), cv2.COLOR_BGR2RGB)
            results = hands.process(image)
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

            if results.multi_hand_landmarks:
                for hand_landmarks in results.multi_hand_landmarks:
                    # 座標の正規化（手首基準）
                    base_x = hand_landmarks.landmark[0].x
                    base_y = hand_landmarks.landmark[0].y
                    base_z = hand_landmarks.landmark[0].z
                    
                    landmarks = []
                    for lm in hand_landmarks.landmark:
                        landmarks.extend([lm.x - base_x, lm.y - base_y, lm.z - base_z])
                    
                    # 確率を算出
                    probabilities = model.predict_proba([landmarks])[0]
                    prediction = np.argmax(probabilities)
                    confidence = probabilities[prediction]

                    # 判定ロジック：自信が90%以上かつ、ラベルが「None」でない場合のみ表示
                    THRESHOLD = 0.9
                    if confidence > THRESHOLD and prediction != 2:
                        gesture_name = labels.get(prediction, "Unknown")
                        color = (0, 255, 0) # 緑
                    else:
                        gesture_name = "Searching..."
                        color = (0, 0, 255) # 赤

                    mp_drawing.draw_landmarks(image, hand_landmarks, mp_hands.HAND_CONNECTIONS)
                    cv2.putText(image, f"{gesture_name} ({confidence:.2f})", (10, 50),
                                cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)

            cv2.imshow('Gesture Recognition', image)
            if cv2.waitKey(1) & 0xFF == 27: break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()