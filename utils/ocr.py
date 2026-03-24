"""
ocr.py — image-region OCR pseudo-library.

Crops a rectangular region from a frame, upscales it, thresholds it, and
runs Tesseract to extract an integer value.

Public API
----------
read_int(frame, x1, y1, x2, y2, invert, configs, valid_range, debug_name)
    Try each config in order; return the first integer that falls within
    valid_range, or None if none succeeded.

read_field(frame, x1, y1, x2, y2, invert, config, debug_name)
    Run a single Tesseract config; return the integer or None.
"""

import os

import cv2
import pytesseract


def read_field(frame, x1, y1, x2, y2, invert=False, config="--oem 1 --psm 8", debug_name=None):
    """Crop, upscale, threshold, and OCR one region of `frame`.

    invert      -- use THRESH_BINARY_INV (light text on dark background)
    config      -- Tesseract config string
    debug_name  -- if set, saves the thresholded crop to screenshots/ocr_<name>.png

    Returns the extracted integer, or None if the text was empty or non-numeric.
    """
    crop = frame[y1:y2, x1:x2]
    crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    mode = (cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY) | cv2.THRESH_OTSU
    _, thresh = cv2.threshold(gray, 0, 255, mode)
    if debug_name:
        os.makedirs("screenshots", exist_ok=True)
        cv2.imwrite(f"screenshots/ocr_{debug_name}.png", thresh)
    text = pytesseract.image_to_string(thresh, config=config).strip()
    return text


def read_int(frame, x1, y1, x2, y2, invert=False, configs=None, valid_range=None, debug_name=None):
    """Try each Tesseract config in order and return the first integer that
    falls within valid_range (inclusive), or None if none succeeded.

    configs      -- list of Tesseract config strings; defaults to a single
                    "--oem 1 --psm 8" pass if omitted
    valid_range  -- (lo, hi) inclusive bounds check; omit to accept any integer
    """
    if configs is None:
        configs = ["--oem 1 --psm 8"]
    lo, hi = valid_range if valid_range else (None, None)
    for config in configs:
        temp_value = read_field(frame, x1, y1, x2, y2, invert=invert, config=config, debug_name=debug_name)
        value = int(temp_value) if temp_value else None
        if value is None:
            continue
        if valid_range is None or valid_range[0] <= value <= valid_range[1]:
            return value
    return None
