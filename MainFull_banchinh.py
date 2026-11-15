# smart_home_combined.py
import cv2
import mediapipe as mp
import numpy as np
from mtcnn import MTCNN
from keras_facenet import FaceNet
import math
import os
import uuid
import asyncio
import edge_tts
import serial
import time
import speech_recognition as sr
import threading
import re
from queue import Queue
from typing import Optional

TTS_TEMP_DIR = os.path.join(os.path.dirname(__file__), "tts_tmp")
VOICE_NAME = "vi-VN-HoaiMyNeural"
os.makedirs(TTS_TEMP_DIR, exist_ok=True)

# pygame.mixer mp3
try:
    from pygame import mixer
    mixer.init()
except Exception as e:
    mixer = None
    print(f"[TTS] Warning: mixer không khởi tạo được: {e}. Vẫn tiếp tục nhưng không phát âm thanh nếu không có mixer.")
# Async loop
class AsyncLoopThread:
    def __init__(self):
        self.loop = None
        self.thread = threading.Thread(target=self._start_loop, daemon=True)
        self._ready = threading.Event()
        self.thread.start()
        self._ready.wait()

    def _start_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def run_coro(self, coro):
        """Schedule coroutine on loop, return concurrent.futures.Future"""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

async_loop = AsyncLoopThread()
# Hàm async
async def _speak_async(text: str, voice: str = VOICE_NAME) -> None:
    if not text:
        return
    filename = os.path.join(TTS_TEMP_DIR, f"tts_{uuid.uuid4().hex}.mp3")
    try:
        communicate = edge_tts.Communicate(text=text, voice=voice)
        await communicate.save(filename)
        if mixer:
            try:
                mixer.music.load(filename)
                mixer.music.play()
                while mixer.music.get_busy():
                    await asyncio.sleep(0.1)
            except Exception as e:
                print(f"[TTS] Lỗi phát âm thanh: {e}")
        else:
            print(f"[TTS] Tạo file TTS: {filename} (mixer không khả dụng để phát).")
    except Exception as e:
        print(f"[TTS] Lỗi khi tạo TTS: {e}")
    finally:
        try:
            if os.path.exists(filename):
                os.remove(filename)
        except Exception:
            pass
def speak(text: str):
    """Gọi từ bất kỳ luồng nào để phát TTS (sẽ chạy trên async_loop)."""
    try:
        async_loop.run_coro(_speak_async(text))
    except Exception as e:
        print("[TTS] Lỗi khi schedule _speak_async:", e)

# ---------------------------
# Microphone lock để tránh xung đột
mic_lock = threading.Lock()
# Hàm đồng bộ 
def listen_for_command_sync(timeout=5, phrase_time_limit=5) -> str:
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 150
    recognizer.dynamic_energy_threshold = True

    with mic_lock:
        with sr.Microphone() as source:
            print("🎤 Đang hiệu chỉnh tiếng ồn nền...")
            recognizer.adjust_for_ambient_noise(source, duration=1)
            print("🎤 Đang lắng nghe...")
            try:
                audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
            except sr.WaitTimeoutError:
                print("❌ Quá thời gian chờ.")
                speak("Quá thời gian chờ, hãy nói lại.")
                return ""
    try:
        spoken_text = recognizer.recognize_google(audio, language="vi-VN")
        print(f"[DEBUG] Bạn nói: {spoken_text}")
        return spoken_text.lower()
    except sr.UnknownValueError:
        print("❌ Không nghe rõ.")
        speak("Xin lỗi, em chưa nghe rõ ạ.")
        return ""
    except sr.RequestError:
        print("❌ Không thể truy cập dịch vụ nhận diện giọng nói.")
        speak("Không kết nối được với dịch vụ nhận diện giọng nói.")
        return ""
    except Exception as e:
        print(f"❌ Có lỗi xảy ra: {e}")
        speak("Có lỗi xảy ra khi xử lý giọng nói.")
        return ""

async def listen_for_command_async(timeout=5, phrase_time_limit=5) -> str:
    loop = async_loop.loop
    return await loop.run_in_executor(None, listen_for_command_sync, timeout, phrase_time_limit)

# Map thiết bị
DEVICE_MAP = {
    "light": 1,      # Đèn
    "camera": 2,     # Camera
    "fan": 3,        # Quạt
    "ac": 4,         # Điều hòa
    "tv": 5,         # TV
    "stove": 6,      # Bếp
    "machine": 7,    # Máy giặt
    "curtain": 8     # Rèm phòng khách
}
# ---------------------------
# Hàm gửi lệnh tới Arduino
def send_command(ser: Optional[serial.Serial], command: str) -> bool:
    """
    Gửi lệnh tới Arduino với format chính xác và delay nhỏ để đảm bảo truyền ổn định.
    command: chuỗi dạng 'on1', 'off2', ...
    """
    if ser is None:
        print("[Arduino] ❌ Không gửi được lệnh vì chưa kết nối.")
        return False
    try:
        command = command.strip()
        if not command:
            print("[Arduino] ⚠ Lệnh rỗng, không gửi.")
            return False
        
        data = (command + "\n").encode("utf-8")
        ser.write(data)
        print(f"[Arduino] 📤 Gửi lệnh: {command}")
        
        time.sleep(0.15)
        
        return True
    except Exception as e:
        print(f"[Arduino] ❌ Lỗi gửi lệnh: {e}")
        return False
    
def send_device_command(ser: Optional[serial.Serial], device_key: str, state: str) -> bool:
    """Gửi ONx/OFFx tới Arduino theo DEVICE_MAP."""
    relay_num = DEVICE_MAP.get(device_key)
    if relay_num is None:
        print(f"[Arduino] Thiết bị chưa map relay: {device_key}")
        return False
    cmd = f"{'ON' if state.upper() == 'ON' else 'OFF'}{relay_num}"
    return send_command(ser, cmd)

# ---------------------------
# Main conversation sequence 
async def main_sequence(ser: Optional[serial.Serial]):
    await _speak_async("Chào mừng chủ nhân đã về nhà, mời chủ nhân vào nhà.")

    entered = False
    user_input = await listen_for_command_async()

    if any(keyword in user_input for keyword in ["mở cửa", "có", "vâng", "vào"]):
        entered = True
        await _speak_async("Cửa đã mở, mời chủ nhân vào nhà.")
        send_command(ser, "door_open")

    else:
        await _speak_async("Chủ nhân có muốn vào nhà không để tôi giúp?")
        response = await listen_for_command_async()
        if any(keyword in response for keyword in ["có", "vâng", "vào"]):
            await _speak_async("Cửa đã mở, mời chủ nhân vào nhà.")
            send_command(ser, "door_open")
        else:
            await _speak_async("Hệ thống tạm dừng một chút.")
            await asyncio.sleep(5)

            await _speak_async("Cảm ơn chủ nhân đã quay lại.")
            return
            
    POSITIVE_KEYWORDS = ["khỏe", "ok", "ổn", "bình thường", "tốt", "vui", "phấn khởi", "hạnh phúc"]
    await _speak_async("Hôm nay chủ nhân về nhà có vui, khỏe và phấn khởi không để tôi chăm sóc chủ nhân?")
    await asyncio.sleep(0.1)
    health_status = await listen_for_command_async()

    if any(keyword in health_status for keyword in POSITIVE_KEYWORDS):
        await _speak_async("Rất vui vì chủ nhân vẫn khỏe mạnh. Hôm nay chủ nhân cần tôi phục vụ yêu cầu gì không?")

        # Trạng thái thiết bị (khởi tạo OFF)
        device_states = {
            "light": "OFF",
            "fan": "OFF",
            "tv": "OFF",
            "camera": "OFF",
            "stove": "OFF",
            "ac": "OFF",
            "machine": "OFF",
            "curtain": "OFF",
        }
        while True:
            command = await listen_for_command_async()
            if not command:
                continue

            # ==== ĐÈN ====
            if "bật" in command and "đèn" in command:
                if device_states["light"] != "ON":
                    send_device_command(ser, "light", "ON")
                    await asyncio.sleep(0.2)
                    device_states["light"] = "ON"
                    await _speak_async("Đèn đã được bật.")
                else:
                    await _speak_async("Đèn đã bật rồi ạ.")

            elif "tắt" in command and "đèn" in command:
                if device_states["light"] != "OFF":
                    send_device_command(ser, "light", "OFF")
                    await asyncio.sleep(0.2)
                    device_states["light"] = "OFF"
                    await _speak_async("Đèn đã được tắt.")
                else:
                    await _speak_async("Đèn đã tắt rồi ạ.")

            # ==== CAMERA ====
            elif "bật" in command and "camera" in command:
                if device_states["camera"] != "ON":
                    send_device_command(ser, "camera", "ON")
                    await asyncio.sleep(0.2)
                    device_states["camera"] = "ON"
                    await _speak_async("Camera đã được bật.")
                else:
                    await _speak_async("Camera đã bật rồi ạ.")

            elif "tắt" in command and "camera" in command:
                if device_states["camera"] != "OFF":
                    send_device_command(ser, "camera", "OFF")
                    await asyncio.sleep(0.2)
                    device_states["camera"] = "OFF"
                    await _speak_async("Camera đã được tắt.")
                else:
                    await _speak_async("Camera đã tắt rồi ạ.")

            # ==== QUẠT ====
            elif "bật" in command and "quạt" in command:
                if device_states["fan"] != "ON":
                    send_device_command(ser, "fan", "ON")
                    await asyncio.sleep(0.2)
                    device_states["fan"] = "ON"
                    await _speak_async("Quạt đã được bật.")
                else:
                    await _speak_async("Quạt đã bật rồi ạ.")

            elif "tắt" in command and "quạt" in command:
                if device_states["fan"] != "OFF":
                    send_device_command(ser, "fan", "OFF")
                    await asyncio.sleep(0.2)
                    device_states["fan"] = "OFF"
                    await _speak_async("Quạt đã được tắt.")
                else:
                    await _speak_async("Quạt đã tắt rồi ạ.")

            # ==== TV ====
            elif "bật" in command and ("ti vi" in command or "tivi" in command or "tv" in command):
                if device_states["tv"] != "ON":
                    send_device_command(ser, "tv", "ON")
                    await asyncio.sleep(0.2)
                    device_states["tv"] = "ON"
                    await _speak_async("Ti vi đã được bật.")
                else:
                    await _speak_async("Ti vi đã bật rồi ạ.")

            elif "tắt" in command and ("ti vi" in command or "tivi" in command or "tv" in command):
                if device_states["tv"] != "OFF":
                    send_device_command(ser, "tv", "OFF")
                    await asyncio.sleep(0.2)
                    device_states["tv"] = "OFF"
                    await _speak_async("Ti vi đã được tắt.")
                else:
                    await _speak_async("Ti vi đã tắt rồi ạ.")

            # ==== BẾP ====
            elif "bật" in command and "bếp" in command:
                if device_states["stove"] != "ON":
                    send_device_command(ser, "stove", "ON")
                    await asyncio.sleep(0.2)
                    device_states["stove"] = "ON"
                    await _speak_async("Bếp đã được bật.")
                else:
                    await _speak_async("Bếp đã bật rồi ạ.")

            elif "tắt" in command and "bếp" in command:
                if device_states["stove"] != "OFF":
                    send_device_command(ser, "stove", "OFF")
                    await asyncio.sleep(0.2)
                    device_states["stove"] = "OFF"
                    await _speak_async("Bếp đã được tắt.")
                else:
                    await _speak_async("Bếp đã tắt rồi ạ.")

            # ==== ĐIỀU HÒA ====
            elif "bật" in command and "điều hòa" in command:
                if device_states["ac"] != "ON":
                    send_device_command(ser, "ac", "ON")
                    await asyncio.sleep(0.2)
                    device_states["ac"] = "ON"
                    await _speak_async("Điều hòa đã được bật.")
                else:
                    await _speak_async("Điều hòa đã bật rồi ạ.")

            elif "tắt" in command and "điều hòa" in command:
                if device_states["ac"] != "OFF":
                    send_device_command(ser, "ac", "OFF")
                    await asyncio.sleep(0.2)
                    device_states["ac"] = "OFF"
                    await _speak_async("Điều hòa đã được tắt.")
                else:
                    await _speak_async("Điều hòa đã tắt rồi ạ.")

            # ==== MÁY GIẶT ====
            elif "bật" in command and "máy giặt" in command:
                if device_states["machine"] != "ON":
                    send_device_command(ser, "machine", "ON")
                    await asyncio.sleep(0.2)
                    device_states["machine"] = "ON"
                    await _speak_async("Máy giặt đã được bật.")
                else:
                    await _speak_async("Máy giặt đã bật rồi ạ.")

            elif "tắt" in command and "máy giặt" in command:
                if device_states["machine"] != "OFF":
                    send_device_command(ser, "machine", "OFF")
                    await asyncio.sleep(0.2)
                    device_states["machine"] = "OFF"
                    await _speak_async("Máy giặt đã được tắt.")
                else:
                    await _speak_async("Máy giặt đã tắt rồi ạ.")

            # ==== RÈM PHÒNG KHÁCH ====
            elif "mở" in command and "rèm phòng khách" in command:
                if device_states["curtain"] != "ON":
                    send_device_command(ser, "curtain", "ON")
                    await asyncio.sleep(0.2)
                    device_states["curtain"] = "ON"
                    await _speak_async("Rèm phòng khách đã được mở.")
                else:
                    await _speak_async("Rèm phòng khách đã mở rồi ạ.")

            elif "đóng" in command and "rèm phòng khách" in command:
                if device_states["curtain"] != "OFF":
                    send_device_command(ser, "curtain", "OFF")
                    await asyncio.sleep(0.2)
                    device_states["curtain"] = "OFF"
                    await _speak_async("Rèm phòng khách đã được đóng.")
                else:
                    await _speak_async("Rèm phòng khách đã đóng rồi ạ.")

            # ==== THOÁT ====
            elif "thoát" in command or "kết thúc" in command:
                await _speak_async("Chúc chủ nhân vui vẻ và hạnh phúc bên ngôi nhà thân yêu.")
                break

            else:
                await _speak_async("Tôi chưa hiểu lệnh, xin mời chủ nhân nói lại.")

    elif "yếu" in health_status:
        await _speak_async("Chúc chủ nhân mau khỏe, hệ thống sẽ bật chế độ nghỉ ngơi yên tĩnh.")
    else:
        await _speak_async("Tôi chưa nghe rõ tình trạng sức khỏe của chủ nhân.")

    await _speak_async("Tôi sẽ phục vụ chủ nhân chu đáo, cần gì chỉ cần ra lệnh. Cảm ơn chủ nhân!")

# ---------------------------
# Camera + Hand detector + Face recognition 
# ---------------------------
is_owner_authenticated = False  # global flag để các phần khác biết
owner_sequence_started = False  # kiểm soát chỉ start 1 lần

# Kết nối Arduino (COM3)
try:
    ser = serial.Serial('COM3', 9600, timeout=1)
    time.sleep(2)
    print("✅ Kết nối Arduino thành công.")
    speak("Kết nối Arduino thành công")
except Exception as e:
    ser = None
    print("⚠️ Không kết nối được với Arduino:", e)
    speak("Không kết nối được với Arduino")

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
        self.owner_recognized = False   # nội bộ

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
        global is_owner_authenticated, owner_sequence_started
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

                    # Khởi động kịch bản main_sequence một lần duy nhất
                    if not owner_sequence_started:
                        owner_sequence_started = True
                        # schedule main_sequence trên async loop
                        async_loop.run_coro(main_sequence(ser))
                else:
                    cv2.putText(frame, "NGUOI LA", (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,0,255), 2)
                    cv2.rectangle(frame, (x, y), (x+w, y+h), (0,0,255), 2)

            return frame 

        # Nếu chưa nhận diện chủ nhà -> bỏ qua điều khiển
        if not is_owner_authenticated:
            return img

        # Cho phép điều khiển ngón tay 
        img = self.detector_hand.findHands(img)

        if self.detector_hand.results and self.detector_hand.results.multi_hand_landmarks:
            for hand_idx, hand_handedness in enumerate(self.detector_hand.results.multi_handedness):
                label = hand_handedness.classification[0].label  # "Left" hoặc "Right"
                fingers = self.detector_hand.fingersUp(hand_idx)

                if label == "Left":
                    for i in range(5):
                        device = i + 1  # Đèn 1-5
                        state = fingers[i] if i < len(fingers) else 0
                        if state != self.lastStates[device-1]:
                            if ser:
                                cmd = ("ON" if state else "OFF") + str(device) + "\n"
                                ser.write(cmd.encode())
                            print(f"{'🟢 Bật' if state else '🔴 Tắt'} đèn {device}")
                            self.lastStates[device-1] = state

                elif label == "Right":
                    for i in range(3):  # Chỉ dùng 3 ngón
                        device = i + 6  # Đèn 6-8
                        state = fingers[i] if i < len(fingers) else 0
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

# ---------------------------
# SpeechRecognition class (dùng SR nghe liên tục, edge-tts để phản hồi)
# ---------------------------
class SpeechRecognition:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.tts_queue = Queue()
        self.listening = True

        self.tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        self.tts_thread.start()

    def _tts_worker(self):
        while True:
            text = self.tts_queue.get()
            if text:
                speak(text)

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

    # Vòng lặp nghe giọng nói (blocking, chạy trong thread)
    def listen_voice(self):
        while True:
            if self.listening:
                with mic_lock:
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

# ---------------------------
# Vòng lặp camera
# ---------------------------
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

# ---------------------------
# Main
# ---------------------------
if __name__ == "__main__":
    listener = SpeechRecognition()

    camera_thread = threading.Thread(target=camera_loop, daemon=True)
    camera_thread.start()

    voice_thread = threading.Thread(target=listener.listen_voice, daemon=True)
    voice_thread.start()

    # Giám sát thread voice, restart nếu chết
    try:
        while True:
            if not voice_thread.is_alive():
                print("⚠️ Voice thread chết, khởi động lại...")
                voice_thread = threading.Thread(target=listener.listen_voice, daemon=True)
                voice_thread.start()
            time.sleep(2)
    except KeyboardInterrupt:
        print("🔌 Dừng bởi người dùng.")
    finally:
        if ser:
            try:
                ser.close()
                print("🔌 Đã ngắt kết nối Arduino.")
            except Exception:
                pass
