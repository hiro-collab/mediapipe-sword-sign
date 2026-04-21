import cv2
import mediapipe as mp
import csv
import os

def main():
    mp_hands = mp.solutions.hands
    cap = cv2.VideoCapture(0)
    csv_path = 'gesture_data.csv'

    with mp_hands.Hands(model_complexity=1) as hands:
        print("s: 刀印, v: チョキ, n: その他(パーやグーなど), Esc: 終了")
        
        while cap.isOpened():
            success, image = cap.read()
            if not success: break

            image = cv2.cvtColor(cv2.flip(image, 1), cv2.COLOR_BGR2RGB)
            results = hands.process(image)
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

            label = None
            key = cv2.waitKey(1)
            if key == ord('s'): label = 0  # Sword
            if key == ord('v'): label = 1  # Victory
            if key == ord('n'): label = 2  # None (Other)
            if key == 27: break

            if results.multi_hand_landmarks and label is not None:
                for hand_landmarks in results.multi_hand_landmarks:
                    # 手首(0番)の座標を基準(0,0,0)にする
                    base_x = hand_landmarks.landmark[0].x
                    base_y = hand_landmarks.landmark[0].y
                    base_z = hand_landmarks.landmark[0].z

                    row = [label]
                    for lm in hand_landmarks.landmark:
                        # 相対座標を計算
                        row.extend([lm.x - base_x, lm.y - base_y, lm.z - base_z])
                    
                    with open(csv_path, 'a', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerow(row)
                print(f"Recorded label: {label}")

            cv2.imshow('Data Collection', image)

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()