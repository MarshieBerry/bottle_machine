from __future__ import annotations

import select
import time

from evdev import InputDevice, ecodes, list_devices


def key_to_char(code: int) -> str | None:
    mapping = {
        ecodes.KEY_0: "0",
        ecodes.KEY_1: "1",
        ecodes.KEY_2: "2",
        ecodes.KEY_3: "3",
        ecodes.KEY_4: "4",
        ecodes.KEY_5: "5",
        ecodes.KEY_6: "6",
        ecodes.KEY_7: "7",
        ecodes.KEY_8: "8",
        ecodes.KEY_9: "9",
        ecodes.KEY_KP0: "0",
        ecodes.KEY_KP1: "1",
        ecodes.KEY_KP2: "2",
        ecodes.KEY_KP3: "3",
        ecodes.KEY_KP4: "4",
        ecodes.KEY_KP5: "5",
        ecodes.KEY_KP6: "6",
        ecodes.KEY_KP7: "7",
        ecodes.KEY_KP8: "8",
        ecodes.KEY_KP9: "9",
        ecodes.KEY_MINUS: "-",
    }
    if code in mapping:
        return mapping[code]
    if ecodes.KEY_A <= code <= ecodes.KEY_Z:
        return chr(ord("A") + code - ecodes.KEY_A)
    return None


def main() -> None:
    devices = [InputDevice(path) for path in list_devices()]
    if not devices:
        print("No /dev/input devices found.")
        return

    print("Input devices:")
    for index, device in enumerate(devices, start=1):
        print(f"{index}. {device.path} | {device.name} | {device.phys}")

    print("\nScan the RFID card now. Waiting 20 seconds...")
    buffers = {device.fd: "" for device in devices}
    fd_to_device = {device.fd: device for device in devices}
    deadline = time.monotonic() + 20

    while time.monotonic() < deadline:
        readable, _, _ = select.select(devices, [], [], 0.5)
        for device in readable:
            for event in device.read():
                if event.type != ecodes.EV_KEY or event.value != 1:
                    continue
                if event.code in (ecodes.KEY_ENTER, ecodes.KEY_KPENTER):
                    value = buffers[device.fd].strip()
                    buffers[device.fd] = ""
                    if value:
                        print("\nRFID-like input detected:")
                        print(f"Device path: {fd_to_device[device.fd].path}")
                        print(f"Device name: {fd_to_device[device.fd].name}")
                        print(f"Scanned UID:  {value}")
                        print("\nPut this in config.py:")
                        print(f'RFID_INPUT_DEVICE = "{fd_to_device[device.fd].path}"')
                        return
                    continue
                char = key_to_char(event.code)
                if char is not None:
                    buffers[device.fd] += char

    print("\nNo RFID-like input detected.")
    print("Try running with sudo if permission was denied, or check the scanner is plugged into the Pi.")


if __name__ == "__main__":
    main()
