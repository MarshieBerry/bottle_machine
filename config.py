"""Copy this file to config.py on the Raspberry Pi and edit the values."""

# Raspberry Pi UART connected to ESP32 Serial2.
UART_PORT = "/dev/serial0"
UART_BAUD = 115200
UART_READ_TIMEOUT_SEC = 0.2
UART_EVENT_TIMEOUT_SEC = 1.0
UART_COMMAND_TIMEOUT_SEC = 5.0
UART_ACTION_TIMEOUT_SEC = 20.0

# Set True while testing hardware before Supabase is configured.
# In offline test mode, user lookup and final session upload are skipped.
OFFLINE_TEST_MODE = False

# Required when OFFLINE_TEST_MODE is False. Keep this file private.
SUPABASE_URL = "https://YOUR_PROJECT_REF.supabase.co"
SUPABASE_API_KEY = "YOUR_SUPABASE_SECRET"
KIOSK_ID = "pi-bottle-machine-01"

# YOLO. Change this later to your trained .pt model file.
YOLO_MODEL_PATH = "yolov8n.pt"
YOLO_IMAGE_SIZE = 640
YOLO_CONFIDENCE = 0.60
BOTTLE_LABEL = "bottle"
SAVE_ANNOTATED_IMAGES = True
ANNOTATED_IMAGE_DIR = "detections"

# RFID is read from an RC522 on the Pi.
RFID_DEBOUNCE_SEC = 1.0

# Weight/scoring rules. Replace with your real token calculation later.
MIN_WEIGHT_KG = 0.01
MAX_WEIGHT_KG = 3.00
POINTS_PER_KG = 10.0
SUPABASE_TIMEOUT_SEC = 8
