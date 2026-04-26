import cv2
import mediapipe as mp

from mediapipe_sword_sign import SwordSignDetector
from mediapipe_sword_sign.types import DISPLAY_NAMES

def main():
    try:
        detector = SwordSignDetector(threshold=0.9)
    except FileNotFoundError:
        print("モデルファイルが見つかりません。")
        return

    mp_hands = mp.solutions.hands
    mp_drawing = mp.solutions.drawing_utils
    cap = cv2.VideoCapture(0)

    with detector:
        while cap.isOpened():
            success, image = cap.read()
            if not success: break

            image = cv2.flip(image, 1)
            result = detector.detect_frame(image)
            state = result.state
            best = state.best_gesture()

            if state.primary:
                gesture_name = DISPLAY_NAMES.get(state.primary, state.primary)
                confidence = state.gesture(state.primary).confidence
                color = (0, 255, 0)
            else:
                gesture_name = "Searching..."
                confidence = best.confidence if best else 0.0
                color = (0, 0, 255)

            if result.hand_landmarks:
                mp_drawing.draw_landmarks(image, result.hand_landmarks, mp_hands.HAND_CONNECTIONS)

            cv2.putText(image, f"{gesture_name} ({confidence:.2f})", (10, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)

            cv2.imshow('Gesture Recognition', image)
            if cv2.waitKey(1) & 0xFF == 27: break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
