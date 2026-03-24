"""
settings.py — global configuration for all switch-arduino scripts.

Edit the values here; all utils and main files import from this module.
"""

# ---------------------------------------------------------------------------
# Arduino / serial
# ---------------------------------------------------------------------------
COM_PORT  = "COM6"
BAUDRATE  = 9600

# ---------------------------------------------------------------------------
# Capture device
# ---------------------------------------------------------------------------
CAPTURE_DEVICE = 0          # OpenCV device index (usually 1 for external capture card)
CAPTURE_WIDTH  = 1920
CAPTURE_HEIGHT = 1080
CAPTURE_FPS    = 60

# ---------------------------------------------------------------------------
# Debug / logging
# ---------------------------------------------------------------------------
VERBOSE = True              # Print KEY / Click / Screenshot messages to the console
PRINT_KEY = False
PRINT_CLICK = False
PRINT_SCREENSHOT = False