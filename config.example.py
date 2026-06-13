"""Copy this file to config.py on the Raspberry Pi and edit the values."""

# ESP32 UART. Set ESP32_ENABLED = False to test without ESP32 connected.
ESP32_ENABLED = True
UART_PORT = "/dev/serial0"
UART_BAUD = 115200
UART_READ_TIMEOUT_SEC = 0.2
UART_EVENT_TIMEOUT_SEC = 1.0
UART_COMMAND_TIMEOUT_SEC = 5.0
UART_ACTION_TIMEOUT_SEC = 20.0

# Supabase.
OFFLINE_TEST_MODE = False
SUPABASE_URL = "https://YOUR_PROJECT_REF.supabase.co"
SUPABASE_API_KEY = "YOUR_SUPABASE_SECRET"
KIOSK_ID = "pi-bottle-machine-01"

# YOLO.
YOLO_MODEL_PATH = "yolov8n.pt"
YOLO_IMAGE_SIZE = 640
YOLO_CONFIDENCE = 0.60
BOTTLE_LABEL = "bottle"
SAVE_ANNOTATED_IMAGES = True
ANNOTATED_IMAGE_DIR = "detections"

# Optional full-screen pygame dashboard.
DISPLAY_ENABLED = False
DISPLAY_WIDTH = 800
DISPLAY_HEIGHT = 480
DISPLAY_FPS = 20

# RFID.
RFID_MODE = "usb_event"  # "usb_event", "usb_keyboard", or "rc522"
RFID_INPUT_DEVICE = ""  # Example: "/dev/input/by-id/usb-...-event-kbd"
RFID_DEBOUNCE_SEC = 1.0

# Scoring.
LOADCELL_ENABLED = False
MIN_WEIGHT_KG = 0.01
MAX_WEIGHT_KG = 3.00
POINTS_PER_BOTTLE = 1
SUPABASE_TIMEOUT_SEC = 8
