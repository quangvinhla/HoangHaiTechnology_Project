# my_speech_recognition.py
import speech_recognition as sr
import pyttsx3
import re
import time
import serial

# Kết nối với Arduino qua COM3
try:
    ser = serial.Serial('COM3', 9600, timeout=1)
    time.sleep(2)
    print("✅ Kết nối Arduino thành công.")
except Exception as e:
    ser = None
    print("⚠️ Không kết nối được với Arduino:", e)

class SpeechRecognition:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.tts_engine = pyttsx3.init()
        voices = self.tts_engine.getProperty('voices')
        vietnamese_voice = next((voice.id for voice in voices if "vietnamese" in voice.name.lower() or "vi-vn" in voice.id.lower()), None)
        if vietnamese_voice:
            self.tts_engine.setProperty('voice', vietnamese_voice)
        else:
            print("Cảnh báo: Không tìm thấy giọng nói tiếng Việt.")
        self.tts_engine.setProperty('rate', 150)
        self.listening = True

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
                    print("⚠️ Arduino chưa kết nối!")
            elif command.get("action") == "STOP_LISTENING":
                self.listening = False
                self.speak("Đã dừng nghe lệnh")
            elif command.get("action") == "START_LISTENING":
                self.listening = True
                self.speak("Tiếp tục nghe lệnh")

    def speak(self, text):
        self.tts_engine.say(text)
        self.tts_engine.runAndWait()

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

                        commands = self.process_command(text)
                        if commands:
                            self.handle_parsed_command(commands)
                        else:
                            print("⚠️ Lệnh không rõ.")
                            self.speak("Lệnh không rõ, bạn có thể nói lại không?")
                    except sr.UnknownValueError:
                        print("⚠️ Không nghe rõ.")
                    except sr.WaitTimeoutError:
                        pass
            time.sleep(0.1)

# Chạy voice control
if __name__ == "__main__":
    listener = SpeechRecognition()
    listener.listen_voice()
