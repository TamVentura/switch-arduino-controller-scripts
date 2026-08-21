"""
settings.py — global configuration for all switch-arduino scripts.

Edit the values here; all utils and main files import from this module.
"""

# ---------------------------------------------------------------------------
# Arduino / serial
# ---------------------------------------------------------------------------
COM_PORT  = "COM6"
BAUDRATE  = 115200

# ---------------------------------------------------------------------------
# Capture device
# ---------------------------------------------------------------------------
CAPTURE_DEVICE = 0          # OpenCV device index (usually 1 for external capture card)
CAPTURE_WIDTH  = 1920
CAPTURE_HEIGHT = 1080
CAPTURE_FPS    = 60

# ---------------------------------------------------------------------------
# Tesseract
# ---------------------------------------------------------------------------
TESSDATA_PATH = r"C:\Program Files\Tesseract-OCR\tessdata"

# ---------------------------------------------------------------------------
# Debug / logging
# ---------------------------------------------------------------------------
VERBOSE = True              # Print KEY / Click / Screenshot messages to the console
PRINT_KEY = False
PRINT_CLICK = True
PRINT_SCREENSHOT = False