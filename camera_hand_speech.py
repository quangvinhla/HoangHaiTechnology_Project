import cv2
import time
import mediapipe as mp
import math
import serial
import threading
import numpy as np
from mtcnn import MTCNN
from keras_facenet import FaceNet
import speech_recognition as sr
import pyttsx3
import re
import os
from queue import Queue 

os.chdir(os.path.dirname(os.path.abspath(__file__)))
is_owner_authenticated = False

# Kết nối với Arduino qua COM3
try:
    ser = serial.Serial('COM3', 9600, timeout=1)
    time.sleep(2)
    print("✅ Kết nối Arduino thành công.")
except Exception as e:
    ser = None
    print("⚠️ Không kết nối được với Arduino:", e)

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
                if lmList[self.tipIds[0]][1] > lmList[self.tipIds[0] - 1][1]:
                    fingers.append(1)
                else:
                    fingers.append(0)
                for id in range(1, 5):
                    if lmList[self.tipIds[id]][2] < lmList[self.tipIds[id] - 2][2]:
                        fingers.append(1)
                    else:
                        fingers.append(0)
        return fingers

#  Class chính
#  Điều khiển camera
class CameraControl:
    def __init__(self):
        self.cap = cv2.VideoCapture(0)
        self.cap.set(3, 640)
        self.cap.set(4, 480)
        self.detector_hand = handDetector(maxHands=2)
        self.pTime = 0
        self.lastStates = [0]*8  # Trạng thái đèn 1-8

        # Nhận diện khuôn mặt chủ nhà 
        self.detector_face = MTCNN()
        self.embedder = FaceNet()
        self.known_embedding = self.load_known_face(r"d:\code_ttkt_vinh\anh\chu_nha.jpg")
        self.THRESHOLD = 0.6
        self.owner_recognized = False   # Chỉ khi True mới cho phép điều khiển đèn

    def load_known_face(self, path):
        img = cv2.imread(path)
        if img is None:
           raise Exception(f"❌ Không đọc được ảnh: {path}. Kiểm tra đường dẫn hoặc tên file.")

        print(f"📸 Ảnh đọc thành công: {path}, kích thước: {img.shape}")

        faces = self.detector_face.detect_faces(img)
        print(f"🔍 Số khuôn mặt tìm thấy: {len(faces)}")

        if not faces:
           raise Exception("❌ Không tìm thấy khuôn mặt chủ nhà trong ảnh.")

        x, y, w, h = faces[0]['box']
        face_crop = img[y:y+h, x:x+w]
        embedding = self.embedder.embeddings([face_crop])[0]
        return embedding

    def process_frame(self):
        global is_owner_authenticated 
        success, frame = self.cap.read()
        if not success:
            return None

        img = frame.copy()

        # Nhận diện khuôn mặt chủ nhà trước
        if not self.owner_recognized:
            faces = self.detector_face.detect_faces(frame)
            for res in faces:
                x, y, w, h = res['box']
                face = frame[y:y+h, x:x+w]
                embedding = self.embedder.embeddings([face])[0]
                distance = np.linalg.norm(embedding - self.known_embedding)
                if distance < self.THRESHOLD:
                    cv2.putText(frame, "CHU NHA", (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)
                    cv2.rectangle(frame, (x, y), (x+w, y+h), (0,255,0), 2)
                    print("✅ Xác nhận chủ nhà -> Bắt đầu cho phép điều khiển đèn")
                    self.owner_recognized = True
                    is_owner_authenticated = True  
                    
                else:
                    cv2.putText(frame, "NGUOI LA", (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,0,255), 2)
                    cv2.rectangle(frame, (x, y), (x+w, y+h), (0,0,255), 2)

            return frame 

        # Nếu chưa nhận diện chủ nhà -> bỏ qua điều khiển
        if not is_owner_authenticated:
            return img
        
        # Cho phép điều khiển ngón tay 
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

#  Điều khiển giọng nói
class SpeechRecognition:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.tts_engine = pyttsx3.init()
        self.tts_queue = Queue()
        self.listening = True

        voices = self.tts_engine.getProperty('voices')
        vietnamese_voice = next((voice.id for voice in voices if "vietnamese" in voice.name.lower() or "vi-vn" in voice.id.lower()), None)
        if vietnamese_voice:
            self.tts_engine.setProperty('voice', vietnamese_voice)
        else:
            print("Cảnh báo: Không tìm thấy giọng nói tiếng Việt.")
        self.tts_engine.setProperty('rate', 150)

        self.tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        self.tts_thread.start()

    def _tts_worker(self):
        while True:
            text = self.tts_queue.get()
            if text:
                self.tts_engine.say(text)
                self.tts_engine.runAndWait()

    def speak(self, text):
        self.tts_queue.put(text)

    def process_command(self, text):
        text = text.lower()
        actions = []

        if re.search(r"(?:bật|mở).*(?:đèn|led)", text):
            actions.append({"device": "đèn", "action": "ON", "relay": 1})
        if re.search(r"tắt.*(?:đèn|led)", text):
            actions.append({"device": "đèn", "action": "OFF", "relay": 1})

        if re.search(r"(?:bật|mở).*(?:camera|cam)", text):
            actions.append({"device": "camera", "action": "ON", "relay": 2})
        if re.search(r"tắt.*(?:camera|cam)", text):
            actions.append({"device": "camera", "action": "OFF", "relay": 2})

        if re.search(r"(?:bật|mở).*(?:quạt)", text):
            actions.append({"device": "quạt", "action": "ON", "relay": 3})
        if re.search(r"tắt.*(?:quạt)", text):
            actions.append({"device": "quạt", "action": "OFF", "relay": 3})

        if re.search(r"(?:bật|mở).*(?:điều hòa|máy lạnh)", text):
            actions.append({"device": "điều hòa", "action": "ON", "relay": 4})
        if re.search(r"tắt.*(?:điều hòa|máy lạnh)", text):
            actions.append({"device": "điều hòa", "action": "OFF", "relay": 4})

        if re.search(r"(?:bật|mở).*(?:tivi|tv)", text):
            actions.append({"device": "tivi", "action": "ON", "relay": 5})
        if re.search(r"tắt.*(?:tivi|tv)", text):
            actions.append({"device": "tivi", "action": "OFF", "relay": 5})

        if re.search(r"(?:bật|mở).*(?:bếp)", text):
            actions.append({"device": "bếp", "action": "ON", "relay": 6})
        if re.search(r"tắt.*(?:bếp)", text):
            actions.append({"device": "bếp", "action": "OFF", "relay": 6})

        if re.search(r"(?:bật|mở).*(?:máy giặt)", text):
            actions.append({"device": "máy giặt", "action": "ON", "relay": 7})
        if re.search(r"tắt.*(?:máy giặt)", text):
            actions.append({"device": "máy giặt", "action": "OFF", "relay": 7})

        if re.search(r"(?:mở).*(?:rèm phòng khách)", text):
            actions.append({"device": "rèm phòng khách", "action": "ON", "relay": 8})
        if re.search(r"(?:đóng).*(?:rèm phòng khách)", text):
            actions.append({"device": "rèm phòng khách", "action": "OFF", "relay": 8})

        # Dừng / Tiếp tục nghe
        if re.search(r"(?:dừng|tạm dừng).*(?:nghe|nhận dạng|lệnh)", text):
            return [{"action": "STOP_LISTENING"}]
        if not self.listening and re.search(r"(?:nghe|bắt đầu|tiếp tục).*(?:nghe|nhận dạng|lệnh)", text):
            return [{"action": "START_LISTENING"}]

        return actions if actions else None
        
    def handle_parsed_command(self, commands):
        global is_owner_authenticated
        if not is_owner_authenticated:
            print("⛔ Chưa nhận diện chủ nhà, bỏ qua lệnh giọng nói.")
            return
        
        if not isinstance(commands, list):
            commands = [commands]
        for command in commands:
            if "relay" in command:
                relay_num = command["relay"]
                action = command["action"]

                if ser:
                    if action == "ON":
                        ser.write(f"ON{relay_num}\n".encode())
                        print(f"✅ Bật Relay {relay_num}")
                        self.speak(f"Đã {('mở' if command['device']=='rèm phòng khách' else 'bật')} {command['device']}")
                    elif action == "OFF":
                        ser.write(f"OFF{relay_num}\n".encode())
                        print(f"✅ Tắt Relay {relay_num}")
                        self.speak(f"Đã {('đóng' if command['device']=='rèm phòng khách' else 'tắt')} {command['device']}")
                else:
                    print("⚠️ Arduino chưa kết nối hoặc chưa xác nhận chủ nhà!")
            elif command.get("action") == "STOP_LISTENING":
                self.listening = False
                self.speak("Đã dừng nghe lệnh")
            elif command.get("action") == "START_LISTENING":
                self.listening = True
                self.speak("Tiếp tục nghe lệnh")

    # Vòng lặp nghe giọng nói
    def listen_voice(self):
        while True:
            if self.listening:
                with sr.Microphone() as source:
                    try:
                        print("🎤 Đang nghe...")
                        self.recognizer.adjust_for_ambient_noise(source, duration=0.3)
                        audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=5)
                        text = self.recognizer.recognize_google(audio, language="vi-VN")
                        print(f"🎙️ Nghe được: {text}")

                        if is_owner_authenticated:
                            commands = self.process_command(text)
                            if commands:
                                self.handle_parsed_command(commands)
                            else:
                                print("⚠️ Lệnh không rõ.")
                                self.speak("Lệnh không rõ, bạn có thể nói lại không?")
                        else:
                            print("⛔ Chưa xác nhận chủ nhà, bỏ qua lệnh.")
                    except sr.UnknownValueError:
                        print("⚠️ Không nghe rõ.")
                    except sr.WaitTimeoutError:
                        print("⌛ Hết thời gian chờ, không nghe thấy gì.")
                    except Exception as e:
                        print("❌ Lỗi giọng nói:", e)
                        
            time.sleep(0.1)

# Hàm chạy main
# Hàm chạy camera loop
def camera_loop():
    cam = CameraControl()
    while True:
        img = cam.process_frame()
        if img is not None:
            cv2.imshow("Camera Control", img)
        time.sleep(0.02)

        if cv2.waitKey(10) & 0xFF == 27:
            break
    cam.release()

# --- File chính ---
if __name__ == "__main__":

    listener = SpeechRecognition()

    if ser:
        listener.speak("Kết nối Arduino thành công")
    else:
        listener.speak("Không kết nối được với Arduino")

    camera_thread = threading.Thread(target=camera_loop, daemon=True)
    camera_thread.start()

    voice_thread = threading.Thread(target=listener.listen_voice, daemon=True)
    voice_thread.start()

while True:
    if not voice_thread.is_alive():
        print("⚠️ Voice thread chết, khởi động lại...")
        voice_thread = threading.Thread(target=listener.listen_voice, daemon=True)
        voice_thread.start()
    time.sleep(2)




