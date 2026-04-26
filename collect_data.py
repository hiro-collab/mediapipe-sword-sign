import cv2
import mediapipe as mp
import csv

from mediapipe_sword_sign import features_from_hand_landmarks

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
                    row = [label]
                    row.extend(features_from_hand_landmarks(hand_landmarks))
                    
                    with open(csv_path, 'a', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerow(row)
                print(f"Recorded label: {label}")

            cv2.imshow('Data Collection', image)

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
