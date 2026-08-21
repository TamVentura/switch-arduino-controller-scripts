"""
ocr.py — image-region OCR pseudo-library.

Crops a rectangular region from a frame, upscales it, thresholds it, and
runs Tesseract to extract an integer value.

Uses tesserocr (in-process Tesseract C++ bindings) instead of pytesseract
to avoid per-call subprocess overhead. A single PyTessBaseAPI instance is
created at module load and reused across all calls.

Public API
----------
read_int(frame, x1, y1, x2, y2, invert, configs, valid_range, debug_name)
    Try each config in order; return the first integer that falls within
    valid_range, or None if none succeeded.

read_field(frame, x1, y1, x2, y2, invert, config, debug_name)
    Run a single Tesseract config; return the raw text string.
"""

import os
import time

import cv2
import settings
import tesserocr
from capture import _sx, _sy, get_frame_region
from PIL import Image

# ---------------------------------------------------------------------------
# PSM integer → enum lookup (tesserocr's Cython enum can't be constructed
# from an int directly, so we build a mapping at import time).
# ---------------------------------------------------------------------------
_PSM_BY_INT = {
    int(getattr(tesserocr.PSM, name)): getattr(tesserocr.PSM, name)
    for name in dir(tesserocr.PSM)
    if not name.startswith("_")
}

# ---------------------------------------------------------------------------
# Single API instance — created once at import time, reused for every call.
# Not thread-safe; fine for single-threaded use.
# ---------------------------------------------------------------------------
_api = tesserocr.PyTessBaseAPI(
    path=settings.TESSDATA_PATH,
    psm=tesserocr.PSM.SINGLE_WORD,
    oem=tesserocr.OEM.LSTM_ONLY,
)


def _apply_config(config_str):
    """Parse a Tesseract CLI config string and apply it to the module-level API.

    Handles: --psm N, --oem N (ignored, fixed at init), -c key=value
    Resets the character whitelist before each call so configs don't bleed
    into one another.
    """
    _api.SetVariable("tessedit_char_whitelist", "")
    parts = config_str.split()
    i = 0
    while i < len(parts):
        if parts[i] == "--psm" and i + 1 < len(parts):
            _api.SetPageSegMode(_PSM_BY_INT[int(parts[i + 1])])
            i += 2
        elif parts[i] == "--oem" and i + 1 < len(parts):
            i += 2  # oem is fixed at init, skip
        elif parts[i] == "-c" and i + 1 < len(parts):
            key, val = parts[i + 1].split("=", 1)
            _api.SetVariable(key, val)
            i += 2
        elif parts[i].startswith("-c") and "=" in parts[i]:
            key, val = parts[i][2:].split("=", 1)
            _api.SetVariable(key, val)
            i += 1
        else:
            i += 1


def _preprocess(frame, x1, y1, x2, y2, invert, debug_name):
    """Crop, upscale, and threshold one region. Returns a grayscale PIL Image."""
    crop = get_frame_region(x1, y1, x2, y2, frame=frame)
    # INTER_LINEAR is ~2× faster than INTER_CUBIC and sufficient for digit OCR
    crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_LINEAR)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    mode = (cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY) | cv2.THRESH_OTSU
    _, thresh = cv2.threshold(gray, 0, 255, mode)
    if debug_name:
        os.makedirs("screenshots", exist_ok=True)
        cv2.imwrite(f"screenshots/ocr_{debug_name}.png", thresh)
    return Image.fromarray(thresh)


def read_field(
    frame, x1, y1, x2, y2, invert=False, config="--oem 1 --psm 8", debug_name=None
):
    """Crop, upscale, threshold, and OCR one region of `frame`.

    invert      -- use THRESH_BINARY_INV (light text on dark background)
    config      -- Tesseract config string
    debug_name  -- if set, saves the thresholded crop to screenshots/ocr_<name>.png

    Returns the raw text string from Tesseract.
    """
    start_time = time.time()
    pil_img = _preprocess(frame, x1, y1, x2, y2, invert, debug_name)
    _apply_config(config)
    _api.SetImage(pil_img)
    return _api.GetUTF8Text() or ""


def read_int(
    frame, x1, y1, x2, y2, invert=False, configs=None, valid_range=None, debug_name=None
):
    """Try each Tesseract config in order and return the first integer that
    falls within valid_range (inclusive), or None if none succeeded.

    configs      -- list of Tesseract config strings; defaults to a single
                    "--oem 1 --psm 8" pass if omitted
    valid_range  -- (lo, hi) inclusive bounds check; omit to accept any integer
    """
    if configs is None:
        configs = ["--oem 1 --psm 8 -c tessedit_char_whitelist=0123456789"]

    # Preprocess once and reuse the same image across all config attempts
    pil_img = _preprocess(frame, x1, y1, x2, y2, invert, debug_name)

    for config in configs:
        _apply_config(config)
        _api.SetImage(pil_img)
        text = _api.GetUTF8Text() or ""
        # Strip non-numeric garbage that Tesseract might include, like "O" or "I"
        digits = "".join(c for c in text if c.isdigit())
        if not digits:
            continue
        value = int(digits)
        if valid_range is None or valid_range[0] <= value <= valid_range[1]:
            return value
    return None
