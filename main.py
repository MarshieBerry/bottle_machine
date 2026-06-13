from __future__ import annotations

import dataclasses
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import cv2
import requests
import serial
from display_status import create_display
from picamera2 import Picamera2
from ultralytics import YOLO

try:
    import config
except ImportError:
    print("Missing config.py. Copy config.example.py to config.py and edit it.")
    raise SystemExit(1)


def log(label: str, message: str) -> None:
    print(f"[{label}] {message}", flush=True)


@dataclasses.dataclass
class Detection:
    accepted: bool
    labels: set[str]
    bottle_confidence: float


@dataclasses.dataclass
class Session:
    uid: str
    user_name: str = "Unknown"
    accepted_bottles: int = 0
    rejected_items: int = 0
    total_weight_kg: float = 0.0
    total_points: int = 0

    def add_bottle(self, points: int) -> None:
        self.accepted_bottles += 1
        self.total_points += points


class SupabaseBackend:
    def __init__(self) -> None:
        self.base_url = f"{config.SUPABASE_URL.rstrip('/')}/rest/v1"
        self.headers = {
            "Content-Type": "application/json",
            "apikey": config.SUPABASE_API_KEY,
        }
        if config.SUPABASE_API_KEY.count(".") == 2:
            self.headers["Authorization"] = f"Bearer {config.SUPABASE_API_KEY}"

    def request(
        self,
        method: str,
        route: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        request_headers = dict(self.headers)
        if headers:
            request_headers.update(headers)
        response = requests.request(
            method,
            f"{self.base_url}{route}",
            json=payload,
            params=params,
            headers=request_headers,
            timeout=config.SUPABASE_TIMEOUT_SEC,
        )
        try:
            result = response.json()
        except ValueError:
            result = {"status": "HTTP_ERROR", "message": response.text}
        if not response.ok:
            raise RuntimeError(f"Supabase HTTP {response.status_code}: {result}")
        return result

    def find_or_register_user(self, uid: str) -> str | None:
        try:
            rows = self.request(
                "GET",
                "/recycling_users",
                params={
                    "rfid": f"eq.{uid}",
                    "select": "rfid,student_id,total_points,total_weight_kg,total_bottles",
                    "limit": "1",
                },
            )
        except requests.RequestException as exc:
            log("SUPABASE", f"Card lookup network error: {exc}")
            return None
        except RuntimeError as exc:
            log("SUPABASE", str(exc))
            return None

        if rows:
            user = rows[0]
            student_id = str(user["student_id"])
            log("USER", f"Found student ID {student_id}; current points: {user.get('total_points', 0)}")
            return student_id

        log("USER", "RFID is not registered.")
        student_id = input("Enter your student ID to create account, or press Enter to cancel: ").strip()
        if not student_id:
            return None
        try:
            rows = self.request(
                "POST",
                "/recycling_users",
                payload={"rfid": uid, "student_id": student_id},
                headers={"Prefer": "return=representation"},
            )
        except (requests.RequestException, RuntimeError) as exc:
            log("SUPABASE", f"Registration failed: {exc}")
            return None
        if not rows:
            log("SUPABASE", "Registration returned no new user row.")
            return None
        log("USER", f"Registered student ID {student_id} to card {uid}.")
        return student_id

    def confirm_session(self, session: Session) -> bool:
        payload = {
            "p_event_id": f"{config.KIOSK_ID}-{uuid.uuid4().hex}",
            "p_kiosk_id": config.KIOSK_ID,
            "p_rfid": session.uid,
            "p_weight_kg": session.total_weight_kg,
            "p_bottle_count": session.accepted_bottles,
            "p_points": session.total_points,
        }
        try:
            result = self.request("POST", "/rpc/finish_recycling_session", payload=payload)
        except (requests.RequestException, RuntimeError) as exc:
            log("SUPABASE", f"Could not submit session: {exc}")
            return False
        row = result[0] if isinstance(result, list) and result else result
        log("SUPABASE", f"Saved session; updated account totals: {row}")
        return bool(row)


class Esp32Uart:
    def __init__(self) -> None:
        self.serial = serial.Serial(
            config.UART_PORT,
            config.UART_BAUD,
            timeout=config.UART_READ_TIMEOUT_SEC,
        )
        time.sleep(2)
        self.serial.reset_input_buffer()
        self.serial.reset_output_buffer()

    def close(self) -> None:
        self.serial.close()

    def send(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")) + "\n"
        self.serial.write(data.encode("utf-8"))
        self.serial.flush()
        log("UART->ESP", data.strip())

    def read_message(self, timeout_sec: float | None = None) -> dict[str, Any] | None:
        deadline = time.monotonic() + (timeout_sec if timeout_sec is not None else config.UART_EVENT_TIMEOUT_SEC)
        while time.monotonic() < deadline:
            line = self.serial.readline()
            if not line:
                continue
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                log("UART", f"ESP log: {text}")
                continue
            log("UART<-ESP", json.dumps(message, separators=(",", ":")))
            return message
        return None

    def command(self, command: str, timeout_sec: float | None = None) -> dict[str, Any]:
        self.send({"cmd": command})
        deadline = time.monotonic() + (timeout_sec if timeout_sec is not None else config.UART_COMMAND_TIMEOUT_SEC)
        while time.monotonic() < deadline:
            message = self.read_message(timeout_sec=max(0.1, deadline - time.monotonic()))
            if message is None:
                break
            if message.get("event"):
                # Keep asynchronous events visible; command caller can handle the next event loop.
                log("ESP32", f"Async event while waiting for command response: {message}")
                continue
            if message.get("cmd") == command:
                if not message.get("ok", False):
                    raise RuntimeError(f"ESP32 rejected {command}: {message}")
                return message
        raise TimeoutError(f"No ESP32 response for command {command}")

    def wait_for_event(self) -> dict[str, Any] | None:
        return self.read_message(timeout_sec=config.UART_EVENT_TIMEOUT_SEC)

    def verify_connection(self) -> bool:
        try:
            response = self.command("ping", timeout_sec=config.UART_COMMAND_TIMEOUT_SEC)
        except (RuntimeError, TimeoutError, serial.SerialException) as exc:
            log("UART", f"ESP32 did not answer ping: {exc}")
            log("UART", "Check ESP32 power, uploaded UART sketch, Pi TX/RX crossing, and shared GND.")
            return False
        log("UART", f"ESP32 ping OK: {response}")
        return True


class MockEsp32:
    def __init__(self, display=None) -> None:
        self.display = display

    def close(self) -> None:
        return

    def verify_connection(self) -> bool:
        log("ESP32", "ESP32_ENABLED is False; using terminal mock instead of UART hardware.")
        return True

    def command(self, command: str, timeout_sec: float | None = None) -> dict[str, Any]:
        log("MOCK-ESP32", f"Command: {command}")
        return {"cmd": command, "ok": True, "state": "mock"}

    def wait_for_event(self) -> dict[str, Any] | None:
        if hasattr(self.display, "get_action") and getattr(self.display, "is_real_display", lambda: False)():
            log("MOCK-ESP32", "Waiting for display input: Enter=item, E=end.")
            while True:
                if hasattr(self.display, "supports_actions") and not self.display.supports_actions():
                    break
                if hasattr(self.display, "wait_for_action"):
                    action = self.display.wait_for_action(timeout_sec=0.25)
                else:
                    action = self.display.get_action()
                if action == "item":
                    log("MOCK-ESP32", "Display Enter simulated item detection.")
                    return {"event": "item_detected"}
                if action == "end":
                    log("MOCK-ESP32", "Display E simulated END button.")
                    return {"event": "end_pressed"}
                time.sleep(0.05)

        choice = input("\nPress Enter to simulate item detection, or type end to finish: ").strip().lower()
        if choice == "end":
            return {"event": "end_pressed"}
        if choice:
            log("INPUT", "Unknown command. Press Enter for item detection or type end.")
            return None
        return {"event": "item_detected"}


class CardReader:
    def __init__(self) -> None:
        self.mode = getattr(config, "RFID_MODE", "usb_keyboard")
        self.reader = None
        self._evdev = None
        self._rfid_buffer = ""

        if self.mode == "rc522":
            from mfrc522 import SimpleMFRC522

            self.reader = SimpleMFRC522()
        elif self.mode == "usb_event":
            self.reader = self._open_usb_event_reader()
        elif self.mode != "usb_keyboard":
            raise ValueError(f"Unknown RFID_MODE: {self.mode}")

    def _open_usb_event_reader(self):
        from evdev import InputDevice, ecodes, list_devices

        self._evdev = ecodes
        configured = getattr(config, "RFID_INPUT_DEVICE", "").strip()
        if configured:
            if configured.startswith("event"):
                configured = f"/dev/input/{configured}"
            if not os.path.exists(configured):
                raise RuntimeError(f"RFID_INPUT_DEVICE does not exist: {configured}")
            device = InputDevice(configured)
            log("RFID", f"Using USB input device: {configured} ({device.name})")
            capabilities = device.capabilities()
            key_codes = capabilities.get(ecodes.EV_KEY, [])
            log("RFID", f"Device key capability count: {len(key_codes)}")
            return device

        candidates = []
        for path in list_devices():
            device = InputDevice(path)
            capabilities = device.capabilities()
            key_codes = capabilities.get(ecodes.EV_KEY, [])
            if ecodes.KEY_ENTER in key_codes and any(
                code in key_codes for code in (ecodes.KEY_0, ecodes.KEY_1, ecodes.KEY_A)
            ):
                candidates.append(device)

        if len(candidates) == 1:
            device = candidates[0]
            log("RFID", f"Auto-selected USB RFID input: {device.path} ({device.name})")
            return device

        log("RFID", "Set RFID_INPUT_DEVICE in config.py to one of these candidates:")
        for device in candidates:
            log("RFID", f"  {device.path}  {device.name}")
        raise RuntimeError("Could not auto-select USB RFID input device. Run: python find_rfid.py")

    def _key_to_char(self, code: int) -> str | None:
        assert self._evdev is not None
        mapping = {
            self._evdev.KEY_0: "0",
            self._evdev.KEY_1: "1",
            self._evdev.KEY_2: "2",
            self._evdev.KEY_3: "3",
            self._evdev.KEY_4: "4",
            self._evdev.KEY_5: "5",
            self._evdev.KEY_6: "6",
            self._evdev.KEY_7: "7",
            self._evdev.KEY_8: "8",
            self._evdev.KEY_9: "9",
            self._evdev.KEY_A: "A",
            self._evdev.KEY_B: "B",
            self._evdev.KEY_C: "C",
            self._evdev.KEY_D: "D",
            self._evdev.KEY_E: "E",
            self._evdev.KEY_F: "F",
            self._evdev.KEY_G: "G",
            self._evdev.KEY_H: "H",
            self._evdev.KEY_I: "I",
            self._evdev.KEY_J: "J",
            self._evdev.KEY_K: "K",
            self._evdev.KEY_L: "L",
            self._evdev.KEY_M: "M",
            self._evdev.KEY_N: "N",
            self._evdev.KEY_O: "O",
            self._evdev.KEY_P: "P",
            self._evdev.KEY_Q: "Q",
            self._evdev.KEY_R: "R",
            self._evdev.KEY_S: "S",
            self._evdev.KEY_T: "T",
            self._evdev.KEY_U: "U",
            self._evdev.KEY_V: "V",
            self._evdev.KEY_W: "W",
            self._evdev.KEY_X: "X",
            self._evdev.KEY_Y: "Y",
            self._evdev.KEY_Z: "Z",
            self._evdev.KEY_KP0: "0",
            self._evdev.KEY_KP1: "1",
            self._evdev.KEY_KP2: "2",
            self._evdev.KEY_KP3: "3",
            self._evdev.KEY_KP4: "4",
            self._evdev.KEY_KP5: "5",
            self._evdev.KEY_KP6: "6",
            self._evdev.KEY_KP7: "7",
            self._evdev.KEY_KP8: "8",
            self._evdev.KEY_KP9: "9",
            self._evdev.KEY_MINUS: "-",
        }
        if code in mapping:
            return mapping[code]
        return None

    def wait_for_uid(self) -> str:
        if self.mode == "usb_event":
            log("RFID", "Scan USB RFID card now. Reading directly from /dev/input.")
            assert self.reader is not None
            assert self._evdev is not None
            self._rfid_buffer = ""
            for event in self.reader.read_loop():
                if event.type != self._evdev.EV_KEY or event.value != 1:
                    continue
                log("RFID-RAW", f"key code={event.code} name={self._evdev.KEY.get(event.code, event.code)}")
                if event.code in (self._evdev.KEY_ENTER, self._evdev.KEY_KPENTER):
                    uid = self._rfid_buffer.strip()
                    self._rfid_buffer = ""
                    if uid:
                        log("RFID", f"Card read: {uid}")
                        time.sleep(config.RFID_DEBOUNCE_SEC)
                        return uid
                    continue
                char = self._key_to_char(event.code)
                if char is not None:
                    self._rfid_buffer += char

        if self.mode == "usb_keyboard":
            log("RFID", "Scan USB RFID card now. It should type the UID and press Enter.")
            while True:
                uid = input("RFID UID: ").strip()
                if uid:
                    log("RFID", f"Card read: {uid}")
                    time.sleep(config.RFID_DEBOUNCE_SEC)
                    return uid
                log("RFID", "No UID received. Make sure this terminal is focused before scanning.")

        log("RFID", "Tap RC522 RFID card to start.")
        assert self.reader is not None
        if not hasattr(self.reader, "read_no_block"):
            log(
                "RFID",
                "Using blocking RC522 read(). If this stays here, check SPI is enabled and RC522 wiring/power.",
            )
            card_id, _ = self.reader.read()
            uid = str(card_id)
            log("RFID", f"Card read: {uid}")
            time.sleep(config.RFID_DEBOUNCE_SEC)
            return uid

        while True:
            card_id, _ = self.reader.read_no_block()
            if card_id is not None:
                uid = str(card_id)
                log("RFID", f"Card read: {uid}")
                time.sleep(config.RFID_DEBOUNCE_SEC)
                return uid
            if int(time.monotonic()) % 5 == 0:
                log("RFID", "Still waiting for card...")
                time.sleep(1)
            time.sleep(0.1)

    def close(self) -> None:
        if self.mode == "rc522":
            import RPi.GPIO as GPIO

            GPIO.cleanup()
        elif self.mode == "usb_event" and self.reader is not None:
            self.reader.close()


class Vision:
    def __init__(self) -> None:
        log("CAMERA", f"Loading YOLO model: {config.YOLO_MODEL_PATH}")
        self.model = YOLO(config.YOLO_MODEL_PATH)
        self.camera = Picamera2()
        camera_config = self.camera.create_still_configuration(
            main={"format": "RGB888", "size": (1920, 1080)}
        )
        self.camera.configure(camera_config)
        self.camera.start()
        time.sleep(1)

    def inspect_item(self) -> Detection:
        log("VISION", "Capturing camera frame now.")
        frame = self.camera.capture_array()
        started = time.monotonic()
        results = self.model(frame, imgsz=config.YOLO_IMAGE_SIZE, conf=config.YOLO_CONFIDENCE)
        elapsed_ms = (time.monotonic() - started) * 1000
        result = results[0]
        labels: set[str] = set()
        bottle_confidence = 0.0
        for box in result.boxes:
            class_id = int(box.cls[0])
            confidence = float(box.conf[0])
            name = str(self.model.names[class_id])
            labels.add(name)
            if name == config.BOTTLE_LABEL:
                bottle_confidence = max(bottle_confidence, confidence)

        accepted = config.BOTTLE_LABEL in labels
        log("VISION", f"YOLO inference complete in {elapsed_ms:.0f} ms.")

        if config.SAVE_ANNOTATED_IMAGES:
            directory = Path(config.ANNOTATED_IMAGE_DIR)
            directory.mkdir(exist_ok=True)
            filename = directory / f"detection_{int(time.time())}.jpg"
            cv2.imwrite(str(filename), result.plot())
            log("VISION", f"Saved {filename}")
        return Detection(accepted, labels, bottle_confidence)

    def close(self) -> None:
        self.camera.stop()


def points_for_bottle() -> int:
    return int(config.POINTS_PER_BOTTLE)


def loadcell_enabled() -> bool:
    return bool(getattr(config, "LOADCELL_ENABLED", False))


def process_item(session: Session, esp: Esp32Uart | MockEsp32, vision: Vision, display) -> None:
    log("ITEM", "ESP32 reports item detected and hatch closed. Running YOLO.")
    display.update(status="Inspecting", last_event="Item detected. Running YOLO.")
    detection = vision.inspect_item()
    label_text = ", ".join(sorted(detection.labels)) or "none"
    log("VISION", f"Labels: {label_text}; bottle confidence: {detection.bottle_confidence:.2f}")
    display.update(labels=label_text, confidence=detection.bottle_confidence, last_event=f"YOLO labels: {label_text}")

    if not detection.accepted:
        session.rejected_items += 1
        log("REJECT", "YOLO did not detect a bottle.")
        display.update(
            status="Rejected",
            rejected=session.rejected_items,
            last_event="Rejected: YOLO did not detect a bottle.",
        )
        esp.command("reject", timeout_sec=config.UART_ACTION_TIMEOUT_SEC)
        return

    if loadcell_enabled():
        display.update(status="Weighing", last_event="Bottle detected. Reading loadcell.")
        weight_response = esp.command("weight", timeout_sec=config.UART_COMMAND_TIMEOUT_SEC)
        weight_kg = round(float(weight_response["weight_kg"]), 3)
        log("WEIGHT", f"{weight_kg:.3f} kg")
        min_weight = float(getattr(config, "MIN_WEIGHT_KG", 0.0))
        max_weight = float(getattr(config, "MAX_WEIGHT_KG", 999.0))
        if not min_weight <= weight_kg <= max_weight:
            session.rejected_items += 1
            log("REJECT", "Bottle detected, but weight is outside allowed range.")
            display.update(
                status="Rejected",
                rejected=session.rejected_items,
                last_event=f"Rejected: weight {weight_kg:.3f} kg is outside range.",
            )
            esp.command("reject", timeout_sec=config.UART_ACTION_TIMEOUT_SEC)
            return
        session.total_weight_kg = round(session.total_weight_kg + weight_kg, 3)
    else:
        weight_kg = 0.0
        log("WEIGHT", "Loadcell disabled; skipping weight.")

    points = points_for_bottle()
    session.add_bottle(points)
    log("ACCEPT", f"Added bottle: +{points} points")
    log(
        "TOTAL",
        f"{session.accepted_bottles} bottles, {session.total_points} points",
    )
    display.update(
        status="Accepted",
        bottles=session.accepted_bottles,
        rejected=session.rejected_items,
        weight=session.total_weight_kg,
        points=session.total_points,
        last_event=f"Accepted bottle: +{points} points.",
    )
    esp.command("sort", timeout_sec=config.UART_ACTION_TIMEOUT_SEC)


def run_session(uid: str, backend: SupabaseBackend, esp: Esp32Uart | MockEsp32, vision: Vision, display) -> None:
    display.update(status="Checking User", rfid=uid, last_event=f"RFID scanned: {uid}")
    if config.OFFLINE_TEST_MODE:
        name = f"Test user {uid}"
        log("TEST", "Offline test mode enabled; skipping Supabase calls.")
    else:
        name = backend.find_or_register_user(uid)
        if name is None:
            log("SESSION", "Cancelled; waiting for another card.")
            return

    session = Session(uid=uid, user_name=name)
    log("SESSION", f"Started for {name}. Insert bottles; press END button when done.")
    display.update(
        status="Session Active",
        student=name,
        bottles=0,
        rejected=0,
        weight=0.0,
        points=0,
        labels="-",
        confidence=0.0,
        last_event=f"Session started for {name}.",
    )
    esp.command("start_session")

    while True:
        display.update(status="Waiting Item", last_event="Waiting for ultrasonic item event or END.")
        event = esp.wait_for_event()
        if event is None:
            continue
        event_name = event.get("event")
        if event_name == "item_detected":
            try:
                process_item(session, esp, vision, display)
            except (RuntimeError, TimeoutError, KeyError, ValueError) as exc:
                log("ITEM", f"Processing error: {exc}")
                display.update(status="Error", last_event=f"Processing error: {exc}")
                try:
                    esp.command("reject", timeout_sec=config.UART_ACTION_TIMEOUT_SEC)
                except Exception as reject_exc:
                    log("ESP32", f"Reject command failed: {reject_exc}")
                    display.update(status="Error", last_event=f"Reject command failed: {reject_exc}")
        elif event_name == "end_pressed":
            log("SESSION", "END button pressed.")
            display.update(status="Ending", last_event="END pressed. Closing session.")
            esp.command("end_session")
            break
        elif event_name == "ready":
            log("ESP32", "Controller ready.")
            display.update(esp32="Ready", last_event="ESP32 controller ready.")
        elif event_name == "error":
            log("ESP32", str(event))
            display.update(status="ESP32 Error", last_event=str(event))

    log(
        "SESSION",
        f"Ending: {session.accepted_bottles} bottles, {session.total_points} points.",
    )
    if session.accepted_bottles == 0:
        log("SESSION", "Nothing accepted; no Supabase transaction submitted.")
        display.update(status="Waiting RFID", student="-", last_event="Session ended with no accepted bottles.")
    elif config.OFFLINE_TEST_MODE:
        log("TEST", "Offline test mode: totals shown above, Supabase submission skipped.")
        display.update(status="Waiting RFID", student="-", last_event="Offline session ended; Supabase skipped.")
    elif backend.confirm_session(session):
        log("SESSION", "Transaction saved successfully.")
        display.update(status="Saved", student="-", last_event="Session saved to Supabase.")
    else:
        log("SESSION", "Supabase submission failed; keep this terminal output for recovery.")
        display.update(status="Save Failed", last_event="Supabase submission failed.")


def main() -> int:
    log("BOOT", "Smart bottle machine controller starting.")
    if config.OFFLINE_TEST_MODE:
        log("TEST", "OFFLINE_TEST_MODE is enabled. No Supabase data will be sent.")
    backend = SupabaseBackend()
    esp: Esp32Uart | MockEsp32 | None = None
    reader: CardReader | None = None
    vision: Vision | None = None
    display = create_display(
        getattr(config, "DISPLAY_ENABLED", False),
        getattr(config, "DISPLAY_WIDTH", 800),
        getattr(config, "DISPLAY_HEIGHT", 480),
        getattr(config, "DISPLAY_FPS", 20),
    )
    try:
        display.update(status="Booting", last_event="Starting machine controller.")
        if getattr(config, "ESP32_ENABLED", True):
            esp = Esp32Uart()
            log("UART", f"Serial port opened on {config.UART_PORT} at {config.UART_BAUD}.")
            display.update(esp32="UART Open", last_event="UART serial port opened.")
        else:
            esp = MockEsp32(display)
        if not esp.verify_connection():
            display.update(status="ESP32 Offline", esp32="No Ping", last_event="ESP32 did not answer ping.")
            return 1
        display.update(esp32="Connected" if getattr(config, "ESP32_ENABLED", True) else "Mock")
        reader = CardReader()
        vision = Vision()
        while True:
            display.update(status="Waiting RFID", rfid="-", student="-", last_event="Waiting for RFID card.")
            uid = reader.wait_for_uid()
            if uid:
                run_session(uid, backend, esp, vision, display)
    except KeyboardInterrupt:
        log("BOOT", "Stopped.")
        display.update(status="Stopped", last_event="Stopped by keyboard interrupt.")
        return 0
    finally:
        if reader is not None:
            reader.close()
        if vision is not None:
            vision.close()
        if esp is not None:
            esp.close()
        display.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
