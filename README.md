# Smart Bottle Machine Build Version

This version uses real hardware inputs and UART between the Raspberry Pi and
ESP32.

```text
Pi reads RFID
-> Pi checks/creates user in Supabase
-> Pi sends start_session to ESP32 over UART
-> ESP32 ultrasonic detects inserted item
-> ESP32 closes hatch and sends item_detected to Pi
-> Pi runs YOLO on camera frame
-> invalid: Pi sends reject to ESP32
-> valid bottle: Pi adds fixed bottle points
-> Pi sends sort to ESP32
-> ESP32 runs relay 1, relay 2, and drop servo
-> repeat until ESP32 END button is pressed
-> Pi saves totals to Supabase
```

For now the only accepted YOLO label is `bottle`.

## Files

- `pi/main.py`: Pi controller for USB/RC522 RFID, YOLO, Supabase, and UART protocol.
- `pi/display_status.py`: optional full-screen pygame status dashboard.
- `pi/test.py`: UART command tester for ESP32 hardware.
- `pi/config.example.py`: copy to `config.py` and edit private settings.
- `esp32/smart_bin_esp32/smart_bin_esp32.ino`: ESP32 UART hardware controller.
- `supabase/setup.sql`: Supabase tables and session-saving function.

## UART Wiring

Use 3.3 V UART only and connect grounds together.

| Raspberry Pi | ESP32 |
| --- | --- |
| GPIO14 TXD `/dev/serial0` | GPIO16 RX2 |
| GPIO15 RXD `/dev/serial0` | GPIO17 TX2 |
| GND | GND |

Do not connect Pi 5 V to ESP32 UART pins.

Enable Pi UART:

```bash
sudo raspi-config
```

Choose interface options for serial:

```text
Login shell over serial: No
Serial hardware port: Yes
```

Reboot after changing serial settings.

## ESP32 Hardware Pins

Change these constants at the top of the Arduino sketch if your wiring differs.

| Function | Default ESP32 Pin |
| --- | --- |
| UART RX2 from Pi TX | GPIO16 |
| UART TX2 to Pi RX | GPIO17 |
| Ultrasonic TRIG | GPIO5 |
| Ultrasonic ECHO | GPIO18 |
| Hatch servo | GPIO13 |
| Drop servo | GPIO14 |
| Relay 1 | GPIO26 |
| Relay 2 | GPIO27 |
| END button to GND | GPIO32 |

Important notes:

- Many ultrasonic sensors output 5 V on ECHO; level shift/divide it to 3.3 V.
- Power servos, relays, and actuator hardware from a proper external supply.
- Share ground between ESP32, sensor modules, relay supply, and actuator supply.
- Loadcell/HX711 is disabled for the current accept/reject build.

Arduino IDE libraries:

```text
ArduinoJson
ESP32Servo
```

If you later set `USE_LOADCELL 1` in the ESP32 sketch, install `HX711 Arduino
Library` and wire/calibrate the HX711 pins in the sketch.

Upload `smart_bin_esp32.ino`, open Serial Monitor at `115200`, and check for:

```text
[BOOT] ESP32 UART hardware controller starting
[LOADCELL] Disabled. Accept/reject uses YOLO only.
```

## Pi Setup

Install non-YOLO support packages. For a USB RFID scanner you do not need
RC522/SPI packages, but they are harmless if already installed.

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-opencv python3-venv python3-rpi.gpio python3-spidev
```

Inside your existing virtual environment:

```bash
pip install -r requirements.txt
```

`requirements.txt` includes:

```text
requests
pyserial
mfrc522-python
pygame
evdev
ultralytics==8.3.70
torch==2.5.0
torchvision==0.20
```

If Ultralytics/Torch/Torchvision are already installed correctly on your Pi 4,
you only need missing packages such as `pyserial`, `pygame`, and
`evdev`.

## Pi Config

Copy and edit:

```bash
cp config.example.py config.py
nano config.py
```

Set at minimum:

```python
ESP32_ENABLED = True
UART_PORT = "/dev/serial0"
UART_BAUD = 115200

OFFLINE_TEST_MODE = False
SUPABASE_URL = "https://YOUR_PROJECT_REF.supabase.co"
SUPABASE_API_KEY = "YOUR_PRIVATE_SUPABASE_SECRET_OR_SERVICE_ROLE_KEY"
KIOSK_ID = "pi-bottle-machine-01"

YOLO_MODEL_PATH = "yolov8n.pt"
BOTTLE_LABEL = "bottle"

RFID_MODE = "usb_event"
RFID_INPUT_DEVICE = ""
LOADCELL_ENABLED = False
POINTS_PER_BOTTLE = 1
```

`usb_event` reads the USB RFID scanner directly from Linux input events, so it
continues working even when the pygame dashboard owns the screen. If auto-detect
cannot choose the scanner, find the device with:

```bash
ls -l /dev/input/by-id/
```

Then set something like:

```python
RFID_INPUT_DEVICE = "/dev/input/by-id/usb-YOUR_RFID_READER-event-kbd"
```

If permission is denied, add your user to the input group and log out/in:

```bash
sudo usermod -aG input $USER
```

Optional screen dashboard:

```python
DISPLAY_ENABLED = True
DISPLAY_WIDTH = 800
DISPLAY_HEIGHT = 480
DISPLAY_FPS = 20
```

When enabled, the dashboard shows RFID/session state, ESP32 status, YOLO labels
and confidence, accepted/rejected counts, points, and the latest event.
If no desktop `DISPLAY` exists, it tries the Pi direct display through SDL
`kmsdrm`. If pygame/display startup fails, the main machine still runs in the
terminal.

If you want to test without the ESP32 connected, set:

```python
ESP32_ENABLED = False
```

Then the Pi skips UART ping, lets you press Enter to simulate item detection,
still runs YOLO, and can still save totals to Supabase.

If `DISPLAY_ENABLED = True` too, the pygame screen owns the keyboard focus:
press `Enter` on the display to simulate ultrasonic item detection, or press
`E` to simulate the END button. If the display is disabled or closes, terminal
input is used instead.

This display-key mock is only for testing without the ESP32. When
`ESP32_ENABLED = True`, the Pi ignores the display Enter/E mock controls for
item detection and waits for the real ESP32 UART events instead. In the real
machine flow, the ultrasonic sensor on the ESP32 sends `item_detected` over
UART, and that is what makes the Pi capture a camera frame and run YOLO.

## Supabase

Run `supabase/setup.sql` once in the Supabase SQL Editor.

Tables:

| Table | Purpose |
| --- | --- |
| `recycling_users` | RFID, student ID, accumulated points, and bottle count |
| `recycling_sessions` | Completed session history |

Unknown RFID cards prompt for student ID in the Pi terminal and create a new
`recycling_users` row.

## Run

```bash
python main.py
```

Expected flow:

```text
[UART] Serial port opened on /dev/serial0 at 115200.
[UART->ESP] {"cmd":"ping"}
[UART<-ESP] {"cmd":"ping","ok":true,...}
[UART] ESP32 ping OK: ...
[RFID] Scan USB RFID card now. Reading directly from /dev/input.
[SESSION] Started for ...
```

Without ESP32 connected, expected startup is:

```text
[ESP32] ESP32_ENABLED is False; using terminal mock instead of UART hardware.
[RFID] Scan USB RFID card now. Reading directly from /dev/input.
```

Opening `/dev/serial0` only proves the Pi UART exists. The `ping` reply proves
the ESP32 is powered, flashed with the UART sketch, wired with crossed TX/RX,
and sharing GND.

If the Pi prints `ESP32 did not answer ping`, fix ESP32/UART before testing the
machine. If it reaches the RFID input message and waits, the ESP32 is no longer
the blocker. Check `RFID_INPUT_DEVICE`, USB scanner power, and Linux input
permissions.

Then insert a bottle. ESP32 should detect it, close the hatch, and tell the Pi.
The Pi runs YOLO and tells ESP32 to accept/sort or reject/drop. Points are fixed
per accepted bottle using `POINTS_PER_BOTTLE`.

Press the physical END button on the ESP32 when the user is finished. The Pi
saves totals to Supabase and returns to waiting for the next RFID card.

## UART Test Tool

Use this before running the full machine:

```bash
python test.py
```

Menu:

```text
1. Start/arm session
2. Reject/drop sequence
3. Accept: relay 1, relay 2, drop sequence
4. End/reset session
5. Print ESP32 status
6. Ping ESP32 UART
7. Reset/open mechanism
```

For commands that require an item to be held, first choose `1`, then trigger the
ultrasonic sensor so ESP32 sends `item_detected`.
