"""Send UART test commands to the ESP32 hardware controller."""

from __future__ import annotations

import json
import time

import serial

import config


COMMANDS = {
    "1": ("start_session", "Start/arm session"),
    "2": ("reject", "Reject: sorter servo to reject side"),
    "3": ("sort", "Accept: sorter servo to accept side"),
    "4": ("end_session", "End/reset session"),
    "5": ("status", "Print ESP32 status"),
    "6": ("ping", "Ping ESP32 UART"),
    "7": ("reset", "Reset/open mechanism"),
}


def read_message(port: serial.Serial, timeout_sec: float = 5.0) -> dict | None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        line = port.readline()
        if not line:
            continue
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            print(f"ESP log: {text}")
    return None


def send(port: serial.Serial, action: str) -> None:
    payload = json.dumps({"cmd": action}, separators=(",", ":")) + "\n"
    port.write(payload.encode("utf-8"))
    port.flush()
    print(f"Sent: {payload.strip()}")
    while True:
        message = read_message(port)
        if message is None:
            print("No response.")
            return
        print(f"ESP32: {message}")
        if message.get("cmd") == action:
            return


def main() -> None:
    with serial.Serial(config.UART_PORT, config.UART_BAUD, timeout=config.UART_READ_TIMEOUT_SEC) as port:
        time.sleep(2)
        port.reset_input_buffer()
        print("ESP32 UART hardware test menu")
        for number, (_, label) in COMMANDS.items():
            print(f"{number}. {label}")
        print("q. Quit")

        while True:
            choice = input("\nChoose test: ").strip().lower()
            if choice == "q":
                return
            command = COMMANDS.get(choice)
            if command is None:
                print("Unknown choice.")
                continue
            send(port, command[0])


if __name__ == "__main__":
    main()
