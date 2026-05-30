from __future__ import annotations

import dataclasses
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import cv2
import requests
import serial
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

    def add_bottle(self, weight_kg: float, points: int) -> None:
        self.accepted_bottles += 1
        self.total_weight_kg = round(self.total_weight_kg + weight_kg, 3)
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


class CardReader:
    def __init__(self) -> None:
        from mfrc522 import SimpleMFRC522

        self.reader = SimpleMFRC522()

    def wait_for_uid(self) -> str:
        log("RFID", "Tap RFID card to start.")
        while True:
            card_id, _ = self.reader.read_no_block()
            if card_id is not None:
                uid = str(card_id)
                log("RFID", f"Card read: {uid}")
                time.sleep(config.RFID_DEBOUNCE_SEC)
                return uid
            time.sleep(0.1)

    def close(self) -> None:
        import RPi.GPIO as GPIO

        GPIO.cleanup()


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


def points_for(weight_kg: float) -> int:
    return round(weight_kg * config.POINTS_PER_KG)


def process_item(session: Session, esp: Esp32Uart, vision: Vision) -> None:
    log("ITEM", "ESP32 reports item detected and hatch closed. Running YOLO.")
    detection = vision.inspect_item()
    label_text = ", ".join(sorted(detection.labels)) or "none"
    log("VISION", f"Labels: {label_text}; bottle confidence: {detection.bottle_confidence:.2f}")

    if not detection.accepted:
        session.rejected_items += 1
        log("REJECT", "YOLO did not detect a bottle.")
        esp.command("reject", timeout_sec=config.UART_ACTION_TIMEOUT_SEC)
        return

    weight_response = esp.command("weight", timeout_sec=config.UART_COMMAND_TIMEOUT_SEC)
    weight_kg = round(float(weight_response["weight_kg"]), 3)
    log("WEIGHT", f"{weight_kg:.3f} kg")
    if not config.MIN_WEIGHT_KG <= weight_kg <= config.MAX_WEIGHT_KG:
        session.rejected_items += 1
        log("REJECT", "Bottle detected, but weight is outside allowed range.")
        esp.command("reject", timeout_sec=config.UART_ACTION_TIMEOUT_SEC)
        return

    points = points_for(weight_kg)
    session.add_bottle(weight_kg, points)
    log("ACCEPT", f"Added bottle: +{points} points")
    log(
        "TOTAL",
        f"{session.accepted_bottles} bottles, {session.total_weight_kg:.3f} kg, {session.total_points} points",
    )
    esp.command("sort", timeout_sec=config.UART_ACTION_TIMEOUT_SEC)


def run_session(uid: str, backend: SupabaseBackend, esp: Esp32Uart, vision: Vision) -> None:
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
    esp.command("start_session")

    while True:
        event = esp.wait_for_event()
        if event is None:
            continue
        event_name = event.get("event")
        if event_name == "item_detected":
            try:
                process_item(session, esp, vision)
            except (RuntimeError, TimeoutError, KeyError, ValueError) as exc:
                log("ITEM", f"Processing error: {exc}")
                try:
                    esp.command("reject", timeout_sec=config.UART_ACTION_TIMEOUT_SEC)
                except Exception as reject_exc:
                    log("ESP32", f"Reject command failed: {reject_exc}")
        elif event_name == "end_pressed":
            log("SESSION", "END button pressed.")
            esp.command("end_session")
            break
        elif event_name == "ready":
            log("ESP32", "Controller ready.")
        elif event_name == "error":
            log("ESP32", str(event))

    log(
        "SESSION",
        f"Ending: {session.accepted_bottles} bottles, {session.total_weight_kg:.3f} kg, {session.total_points} points.",
    )
    if session.accepted_bottles == 0:
        log("SESSION", "Nothing accepted; no Supabase transaction submitted.")
    elif config.OFFLINE_TEST_MODE:
        log("TEST", "Offline test mode: totals shown above, Supabase submission skipped.")
    elif backend.confirm_session(session):
        log("SESSION", "Transaction saved successfully.")
    else:
        log("SESSION", "Supabase submission failed; keep this terminal output for recovery.")


def main() -> int:
    log("BOOT", "Smart bottle machine controller starting.")
    if config.OFFLINE_TEST_MODE:
        log("TEST", "OFFLINE_TEST_MODE is enabled. No Supabase data will be sent.")
    backend = SupabaseBackend()
    esp: Esp32Uart | None = None
    reader: CardReader | None = None
    vision: Vision | None = None
    try:
        esp = Esp32Uart()
        log("UART", f"Connected on {config.UART_PORT} at {config.UART_BAUD}.")
        reader = CardReader()
        vision = Vision()
        while True:
            uid = reader.wait_for_uid()
            if uid:
                run_session(uid, backend, esp, vision)
    except KeyboardInterrupt:
        log("BOOT", "Stopped.")
        return 0
    finally:
        if reader is not None:
            reader.close()
        if vision is not None:
            vision.close()
        if esp is not None:
            esp.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
