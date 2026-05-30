# bottle_machine

# Smart Bottle Machine Build Version

This version uses real hardware inputs and UART between the Raspberry Pi and
ESP32.

```text
Pi RC522 reads RFID
-> Pi checks/creates user in Supabase
-> Pi sends start_session to ESP32 over UART
-> ESP32 ultrasonic detects inserted item
-> ESP32 closes hatch and sends item_detected to Pi
-> Pi runs YOLO on camera frame
-> invalid: Pi sends reject to ESP32
-> valid bottle: Pi requests HX711 weight from ESP32
-> Pi calculates points
-> Pi sends sort to ESP32
-> ESP32 runs relay 1, relay 2, and drop servo
-> repeat until ESP32 END button is pressed
-> Pi saves totals to Supabase
```

For now the only accepted YOLO label is `bottle`.

## Files

- `pi/main.py`: Pi controller for RFID, YOLO, Supabase, and UART protocol.
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
| HX711 DOUT | GPIO4 |
| HX711 SCK | GPIO15 |

Important notes:

- Many ultrasonic sensors output 5 V on ECHO; level shift/divide it to 3.3 V.
- Power servos, relays, and actuator hardware from a proper external supply.
- Share ground between ESP32, sensor modules, relay supply, and actuator supply.
- Calibrate `LOADCELL_CALIBRATION_FACTOR` before trusting weight.

Arduino IDE libraries:

```text
ArduinoJson
ESP32Servo
HX711 Arduino Library
```

Upload `smart_bin_esp32.ino`, open Serial Monitor at `115200`, and check for:

```text
[BOOT] ESP32 UART hardware controller starting
[LOADCELL] Ready.
```

## Pi Setup

Install non-YOLO support packages:

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
ultralytics==8.3.70
torch==2.5.0
torchvision==0.20
```

If Ultralytics/Torch/Torchvision are already installed correctly on your Pi 4,
you only need missing packages such as `pyserial` and `mfrc522-python`.

## Pi Config

Copy and edit:

```bash
cp config.example.py config.py
nano config.py
```

Set at minimum:

```python
UART_PORT = "/dev/serial0"
UART_BAUD = 115200

OFFLINE_TEST_MODE = False
SUPABASE_URL = "https://YOUR_PROJECT_REF.supabase.co"
SUPABASE_API_KEY = "YOUR_PRIVATE_SUPABASE_SECRET_OR_SERVICE_ROLE_KEY"
KIOSK_ID = "pi-bottle-machine-01"

YOLO_MODEL_PATH = "yolov8n.pt"
BOTTLE_LABEL = "bottle"
```

## Supabase

Run `supabase/setup.sql` once in the Supabase SQL Editor.

Tables:

| Table | Purpose |
| --- | --- |
| `recycling_users` | RFID, student ID, accumulated points, weight, and bottle count |
| `recycling_sessions` | Completed session history |

Unknown RFID cards prompt for student ID in the Pi terminal and create a new
`recycling_users` row.

## Run

```bash
python main.py
```

Expected flow:

```text
[RFID] Tap RFID card to start.
[SESSION] Started for ...
```

Then insert a bottle. ESP32 should detect it, close the hatch, and tell the Pi.
The Pi runs YOLO, asks ESP32 for load-cell weight, calculates points, and tells
ESP32 to sort/drop.

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
2. Read load-cell weight
3. Reject/drop sequence
4. Relay 1, relay 2, drop sequence
5. End/reset session
6. Print ESP32 status
7. Ping ESP32 UART
8. Reset/open mechanism
```

For commands that require an item to be held, first choose `1`, then trigger the
ultrasonic sensor so ESP32 sends `item_detected`.
