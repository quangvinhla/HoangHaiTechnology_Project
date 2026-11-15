// Khai báo chân điều khiển relay (IN1 đến IN8)
const int relayPins[] = {2, 3, 4, 5, 6, 7, 8, 9};
const int numRelays = sizeof(relayPins) / sizeof(relayPins[0]);

void setup() {
  Serial.begin(9600);
  for (int i = 0; i < numRelays; i++) {
    pinMode(relayPins[i], OUTPUT);
    digitalWrite(relayPins[i], LOW); // Relay tắt lúc đầu (active HIGH)
  }
  Serial.println("Arduino san sang nhan lenh.");
}

void loop() {
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();     // Xóa khoảng trắng và ký tự thừa
    Serial.println("Received: " + command);

    if (command.startsWith("ON")) {
      int index = command.substring(2).toInt() - 1;
      if (index >= 0 && index < numRelays && command.substring(2).length() > 0) {
        digitalWrite(relayPins[index], HIGH);      // Bật relay (mức HIGH)
        Serial.println("Den " + String(index + 1) + " bat");
      } else {
        Serial.println("Lenh khong hop le");
      }
    } 
    else if (command.startsWith("OFF")) {
      int index = command.substring(3).toInt() - 1;
      if (index >= 0 && index < numRelays && command.substring(3).length() > 0) {
        digitalWrite(relayPins[index], LOW);     // Tắt relay (mức LOW)
        Serial.println("Den " + String(index + 1) + " tat");
      } else {
        Serial.println("Lenh khong hop le");
      }
    } 
    else {
      Serial.println("Lenh khong hop le");
    }
  }
}
