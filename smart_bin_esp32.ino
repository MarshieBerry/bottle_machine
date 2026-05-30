#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>
#include <HX711.h>

// UART wiring: Raspberry Pi TX -> ESP32 RX2, Pi RX -> ESP32 TX2, GND -> GND.
// ESP32 RX2/TX2 default here: GPIO16/GPIO17.
const uint32_t UART_BAUD = 115200;
const uint8_t UART_RX_PIN = 16;
const uint8_t UART_TX_PIN = 17;

// Change pins to match your build.
const uint8_t ULTRASONIC_TRIG_PIN = 5;
const uint8_t ULTRASONIC_ECHO_PIN = 18;  // Use level shifting if the sensor echoes 5 V.
const uint8_t HATCH_SERVO_PIN = 13;
const uint8_t DROP_SERVO_PIN = 14;
const uint8_t RELAY_1_PIN = 26;
const uint8_t RELAY_2_PIN = 27;
const uint8_t END_BUTTON_PIN = 32;       // Button to GND, uses INPUT_PULLUP.
const uint8_t LOADCELL_DOUT_PIN = 4;
const uint8_t LOADCELL_SCK_PIN = 15;

const bool RELAY_ACTIVE_LOW = true;
const float DETECT_DISTANCE_CM = 12.0;
const unsigned long OBJECT_STABLE_MS = 250;
const unsigned long CLEAR_STABLE_MS = 800;
const unsigned long RELAY_ON_MS = 5000;
const unsigned long DROP_OPEN_MS = 1200;

const int HATCH_OPEN_DEG = 15;
const int HATCH_CLOSED_DEG = 95;
const int DROP_CLOSED_DEG = 15;
const int DROP_OPEN_DEG = 95;

// Calibrate this with your load cell. Positive/negative sign depends on wiring.
const float LOADCELL_CALIBRATION_FACTOR = -7050.0;

HardwareSerial PiSerial(2);
Servo hatchServo;
Servo dropServo;
HX711 scale;

enum ActionState {
  IDLE,
  HOLDING_ITEM,
  SORT_RELAY_1,
  SORT_RELAY_2,
  DROP_ACCEPTED,
  DROP_REJECTED
};

ActionState action = IDLE;
bool sessionActive = false;
bool armedForItem = true;
bool itemHeld = false;
bool scaleReady = false;
bool endLatch = false;
unsigned long nearStartedMs = 0;
unsigned long clearStartedMs = 0;
unsigned long lastDistanceCheckMs = 0;
unsigned long actionStartedMs = 0;
String inputLine = "";

void setRelay(uint8_t pin, bool on) {
  digitalWrite(pin, RELAY_ACTIVE_LOW ? !on : on);
}

void allRelaysOff() {
  setRelay(RELAY_1_PIN, false);
  setRelay(RELAY_2_PIN, false);
}

String stateName() {
  switch (action) {
    case IDLE: return "idle";
    case HOLDING_ITEM: return "holding_item";
    case SORT_RELAY_1: return "sort_relay_1";
    case SORT_RELAY_2: return "sort_relay_2";
    case DROP_ACCEPTED: return "drop_accepted";
    case DROP_REJECTED: return "drop_rejected";
  }
  return "unknown";
}

void sendJson(JsonDocument &doc) {
  serializeJson(doc, PiSerial);
  PiSerial.print('\n');
  serializeJson(doc, Serial);
  Serial.println();
}

void sendEvent(const char *event) {
  StaticJsonDocument<160> doc;
  doc["event"] = event;
  doc["state"] = stateName();
  doc["session_active"] = sessionActive;
  sendJson(doc);
}

void sendAck(const char *cmd, bool ok = true, const char *error = nullptr) {
  StaticJsonDocument<220> doc;
  doc["cmd"] = cmd;
  doc["ok"] = ok;
  doc["state"] = stateName();
  doc["session_active"] = sessionActive;
  if (error != nullptr) {
    doc["error"] = error;
  }
  sendJson(doc);
}

void sendStatus(const char *cmd = "status") {
  StaticJsonDocument<260> doc;
  doc["cmd"] = cmd;
  doc["ok"] = true;
  doc["state"] = stateName();
  doc["session_active"] = sessionActive;
  doc["armed"] = armedForItem;
  doc["item_held"] = itemHeld;
  doc["scale_ready"] = scaleReady;
  sendJson(doc);
}

float readDistanceCm() {
  digitalWrite(ULTRASONIC_TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(ULTRASONIC_TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(ULTRASONIC_TRIG_PIN, LOW);
  unsigned long duration = pulseIn(ULTRASONIC_ECHO_PIN, HIGH, 25000);
  if (duration == 0) {
    return -1.0;
  }
  return duration * 0.0343 / 2.0;
}

void resetMechanism() {
  allRelaysOff();
  dropServo.write(DROP_CLOSED_DEG);
  hatchServo.write(HATCH_OPEN_DEG);
  itemHeld = false;
  armedForItem = false;
  nearStartedMs = 0;
  clearStartedMs = 0;
  action = IDLE;
}

void armSession() {
  sessionActive = true;
  armedForItem = true;
  itemHeld = false;
  endLatch = false;
  nearStartedMs = 0;
  clearStartedMs = 0;
  action = IDLE;
  allRelaysOff();
  dropServo.write(DROP_CLOSED_DEG);
  hatchServo.write(HATCH_OPEN_DEG);
}

void updateEndButton() {
  bool pressed = digitalRead(END_BUTTON_PIN) == LOW;
  if (pressed && !endLatch && sessionActive) {
    endLatch = true;
    sendEvent("end_pressed");
  } else if (!pressed) {
    endLatch = false;
  }
}

void updateObjectSensor() {
  if (!sessionActive || action != IDLE || millis() - lastDistanceCheckMs < 80) {
    return;
  }
  lastDistanceCheckMs = millis();
  float distance = readDistanceCm();
  bool near = distance > 0 && distance <= DETECT_DISTANCE_CM;

  if (!armedForItem) {
    if (!near) {
      if (clearStartedMs == 0) {
        clearStartedMs = millis();
      } else if (millis() - clearStartedMs >= CLEAR_STABLE_MS) {
        armedForItem = true;
        clearStartedMs = 0;
        Serial.println("[SENSOR] Armed for next item");
      }
    } else {
      clearStartedMs = 0;
    }
    return;
  }

  if (near) {
    if (nearStartedMs == 0) {
      nearStartedMs = millis();
    } else if (millis() - nearStartedMs >= OBJECT_STABLE_MS) {
      hatchServo.write(HATCH_CLOSED_DEG);
      itemHeld = true;
      armedForItem = false;
      action = HOLDING_ITEM;
      sendEvent("item_detected");
    }
  } else {
    nearStartedMs = 0;
  }
}

void updateMechanism() {
  unsigned long elapsed = millis() - actionStartedMs;
  if (action == SORT_RELAY_1 && elapsed >= RELAY_ON_MS) {
    setRelay(RELAY_1_PIN, false);
    setRelay(RELAY_2_PIN, true);
    action = SORT_RELAY_2;
    actionStartedMs = millis();
  } else if (action == SORT_RELAY_2 && elapsed >= RELAY_ON_MS) {
    setRelay(RELAY_2_PIN, false);
    dropServo.write(DROP_OPEN_DEG);
    action = DROP_ACCEPTED;
    actionStartedMs = millis();
  } else if ((action == DROP_ACCEPTED || action == DROP_REJECTED) && elapsed >= DROP_OPEN_MS) {
    resetMechanism();
  }
}

void handleCommand(const String &line) {
  StaticJsonDocument<160> input;
  DeserializationError error = deserializeJson(input, line);
  if (error) {
    sendAck("unknown", false, "invalid_json");
    return;
  }

  const char *cmdRaw = input["cmd"];
  if (cmdRaw == nullptr) {
    sendAck("unknown", false, "missing_cmd");
    return;
  }
  String cmd = cmdRaw;

  if (cmd == "ping") {
    sendAck("ping");
  } else if (cmd == "status") {
    sendStatus("status");
  } else if (cmd == "start_session") {
    armSession();
    sendAck("start_session");
  } else if (cmd == "end_session") {
    sessionActive = false;
    resetMechanism();
    sendAck("end_session");
  } else if (cmd == "reset") {
    resetMechanism();
    sendAck("reset");
  } else if (cmd == "weight") {
    if (action != HOLDING_ITEM || !itemHeld) {
      sendAck("weight", false, "no_item_held");
      return;
    }
    if (!scaleReady || !scale.is_ready()) {
      sendAck("weight", false, "scale_not_ready");
      return;
    }
    float weightKg = scale.get_units(10);
    if (weightKg < 0) {
      weightKg = -weightKg;
    }
    StaticJsonDocument<180> doc;
    doc["cmd"] = "weight";
    doc["ok"] = true;
    doc["state"] = stateName();
    doc["weight_kg"] = weightKg;
    sendJson(doc);
  } else if (cmd == "sort") {
    if (action != HOLDING_ITEM || !itemHeld) {
      sendAck("sort", false, "no_item_held");
      return;
    }
    itemHeld = false;
    setRelay(RELAY_1_PIN, true);
    action = SORT_RELAY_1;
    actionStartedMs = millis();
    sendAck("sort");
  } else if (cmd == "reject") {
    if (action != HOLDING_ITEM || !itemHeld) {
      sendAck("reject", false, "no_item_held");
      return;
    }
    itemHeld = false;
    dropServo.write(DROP_OPEN_DEG);
    action = DROP_REJECTED;
    actionStartedMs = millis();
    sendAck("reject");
  } else {
    sendAck(cmdRaw, false, "unknown_cmd");
  }
}

void readPiSerial() {
  while (PiSerial.available()) {
    char c = (char)PiSerial.read();
    if (c == '\n') {
      String line = inputLine;
      inputLine = "";
      line.trim();
      if (line.length() > 0) {
        Serial.print("[PI] ");
        Serial.println(line);
        handleCommand(line);
      }
    } else if (c != '\r') {
      inputLine += c;
      if (inputLine.length() > 300) {
        inputLine = "";
      }
    }
  }
}

void setup() {
  Serial.begin(115200);
  PiSerial.begin(UART_BAUD, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);
  delay(500);
  Serial.println("[BOOT] ESP32 UART hardware controller starting");

  pinMode(ULTRASONIC_TRIG_PIN, OUTPUT);
  pinMode(ULTRASONIC_ECHO_PIN, INPUT);
  pinMode(END_BUTTON_PIN, INPUT_PULLUP);
  pinMode(RELAY_1_PIN, OUTPUT);
  pinMode(RELAY_2_PIN, OUTPUT);
  allRelaysOff();

  hatchServo.setPeriodHertz(50);
  dropServo.setPeriodHertz(50);
  hatchServo.attach(HATCH_SERVO_PIN, 500, 2400);
  dropServo.attach(DROP_SERVO_PIN, 500, 2400);
  hatchServo.write(HATCH_OPEN_DEG);
  dropServo.write(DROP_CLOSED_DEG);

  scale.begin(LOADCELL_DOUT_PIN, LOADCELL_SCK_PIN);
  scale.set_scale(LOADCELL_CALIBRATION_FACTOR);
  if (scale.is_ready()) {
    Serial.println("[LOADCELL] Taring. Keep platform empty.");
    scale.tare();
    scaleReady = true;
    Serial.println("[LOADCELL] Ready.");
  } else {
    Serial.println("[LOADCELL][ERROR] HX711 not ready. Check wiring.");
  }

  sendEvent("ready");
}

void loop() {
  readPiSerial();
  updateEndButton();
  updateObjectSensor();
  updateMechanism();
  delay(2);
}
