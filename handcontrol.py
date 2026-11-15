import cv2
import time
import mediapipe as mp
import math
import serial

#  Kết nối Arduino
try:
    ser = serial.Serial('COM3', 9600, timeout=1)
    time.sleep(2)
    print("✅ Kết nối Arduino thành công.")
except:
    ser = None
    print("⚠️ Không kết nối được Arduino.")

#  Class handDetector
class handDetector:
    def __init__(self, mode=False, maxHands=2, detectionCon=0.5, trackCon=0.5):
        self.mode = mode
        self.maxHands = maxHands
        self.detectionCon = detectionCon
        self.trackCon = trackCon
        self.mpHands = mp.solutions.hands
        self.hands = self.mpHands.Hands(
            static_image_mode=self.mode,
            max_num_hands=self.maxHands,
            min_detection_confidence=self.detectionCon,
            min_tracking_confidence=self.trackCon
        )
        self.mpDraw = mp.solutions.drawing_utils
        self.tipIds = [4, 8, 12, 16, 20]
        self.results = None
        self.img_shape = None

    def findHands(self, img, draw=True):
        self.img_shape = img.shape
        imgRGB = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        self.results = self.hands.process(imgRGB)
        if self.results.multi_hand_landmarks:
            for handLms in self.results.multi_hand_landmarks:
                if draw:
                    self.mpDraw.draw_landmarks(img, handLms, self.mpHands.HAND_CONNECTIONS)
        return img

    def fingersUp(self, handNo=0):
        lmList = []
        fingers = []
        if self.results.multi_hand_landmarks and len(self.results.multi_hand_landmarks) > handNo:
            myHand = self.results.multi_hand_landmarks[handNo]
            h, w, c = self.img_shape
            for id, lm in enumerate(myHand.landmark):
                cx, cy = int(lm.x * w), int(lm.y * h)
                lmList.append([id, cx, cy])
            if lmList:
                # Ngón cái
                if lmList[self.tipIds[0]][1] > lmList[self.tipIds[0] - 1][1]:
                    fingers.append(1)
                else:
                    fingers.append(0)
                # 4 ngón còn lại
                for id in range(1, 5):
                    if lmList[self.tipIds[id]][2] < lmList[self.tipIds[id] - 2][2]:
                        fingers.append(1)
                    else:
                        fingers.append(0)
        return fingers

#  Class chính
class CameraControl:
    def __init__(self):
        self.cap = cv2.VideoCapture(0)
        self.cap.set(3, 640)
        self.cap.set(4, 480)
        self.detector_hand = handDetector(maxHands=2)
        self.pTime = 0
        self.lastStates = [0]*8  # Trạng thái đèn 1-8

    def process_frame(self):
        success, frame = self.cap.read()
        if not success:
            return None

        img = frame.copy()
        img = self.detector_hand.findHands(img)

        if self.detector_hand.results.multi_hand_landmarks:
            for hand_idx, hand_handedness in enumerate(self.detector_hand.results.multi_handedness):
                label = hand_handedness.classification[0].label  # "Left" hoặc "Right"
                fingers = self.detector_hand.fingersUp(hand_idx)

                if label == "Left":
                    for i in range(5):
                        device = i + 1  # Đèn 1-5
                        state = fingers[i]
                        if state != self.lastStates[device-1]:
                            if ser:
                                cmd = ("ON" if state else "OFF") + str(device) + "\n"
                                ser.write(cmd.encode())
                            print(f"{'🟢 Bật' if state else '🔴 Tắt'} đèn {device}")
                            self.lastStates[device-1] = state

                elif label == "Right":
                    for i in range(3):  # Chỉ dùng 3 ngón
                        device = i + 6  # Đèn 6-8
                        state = fingers[i]
                        if state != self.lastStates[device-1]:
                            if ser:
                                cmd = ("ON" if state else "OFF") + str(device) + "\n"
                                ser.write(cmd.encode())
                            print(f"{'🟢 Bật' if state else '🔴 Tắt'} đèn {device}")
                            self.lastStates[device-1] = state

        # Hiển thị FPS
        cTime = time.time()
        fps = 1 / (cTime - self.pTime + 1e-5)
        self.pTime = cTime
        cv2.putText(img, f"FPS: {int(fps)}", (20, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 2)

        return img

    def release(self):
        self.cap.release()
        cv2.destroyAllWindows()

#  Chạy chương trình
if __name__ == "__main__":
    cam = CameraControl()
    while True:
        img = cam.process_frame()
        if img is not None:
            cv2.imshow("Camera Control", img)
        if cv2.waitKey(1) & 0xFF == 27:
            break
    cam.release()
