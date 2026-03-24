from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime

import cv2
import numpy as np

try:
    raise ImportError
    import pytesseract
    PYTESSERACT_AVAILABLE = True
except ImportError:
    PYTESSERACT_AVAILABLE = False
    print("pytesseract not installed — box number detection disabled. Run: pip install pytesseract")

SCREENSHOTS_DIR = "screenshots"
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

CAPTURE_DEVICE_INDEX = 1

cap = cv2.VideoCapture(CAPTURE_DEVICE_INDEX)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

if not cap.isOpened():
    print(f"Could not open capture device at index {CAPTURE_DEVICE_INDEX}")
    exit(1)

DISPLAY_WIDTH = 1280
DISPLAY_HEIGHT = 720

# "Box N" label region — adjust if the text isn't being read correctly
BOX_LABEL_X1 = 360
BOX_LABEL_X2 = 500
BOX_LABEL_Y1 = 58
BOX_LABEL_Y2 = 88

# Box grid region — adjust these if the overlay doesn't align with the grid
GRID_X1 = 300
GRID_X2 = 800
GRID_Y1 = 133
GRID_Y2 = 550
GRID_ROWS = 5
GRID_COLS = 6

# Yellow selection cursor in HSV
YELLOW_LOWER = np.array([20, 150, 180])
YELLOW_UPPER = np.array([40, 255, 255])

# White outline detection — raise WHITE_MIN_PIXELS if you get false positives
WHITE_SAT_MAX = 100   # max saturation to be considered "white"
WHITE_VAL_MIN = 200  # min brightness to be considered "white"

# Pulse detection — raise PULSE_VAR_THRESHOLD if static white Pokemon trigger false positives
PULSE_DURATION = 3.0
PULSE_VAR_THRESHOLD = 130

# Toggle grid overlay with 'g'
show_grid = False

pulse_monitoring = False
pulse_start_time = 0.0
pulse_samples: dict[tuple[int, int], list[int]] = {}


def detect_box_number(frame):
    return None
    if not PYTESSERACT_AVAILABLE:
        return None
    roi = frame[BOX_LABEL_Y1:BOX_LABEL_Y2, BOX_LABEL_X1:BOX_LABEL_X2]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)
    text = pytesseract.image_to_string(thresh, config="--psm 7 digits")
    match = re.search(r"\d+", text)
    return int(match.group()) if match else "None"


def detect_selection(frame):
    cell_w = (GRID_X2 - GRID_X1) / GRID_COLS
    cell_h = (GRID_Y2 - GRID_Y1) / GRID_ROWS

    roi = frame[GRID_Y1:GRID_Y2, GRID_X1:GRID_X2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, YELLOW_LOWER, YELLOW_UPPER)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Keep only contours whose bounding rect fits within one cell — excludes tooltip popup
    clean_mask = np.zeros_like(mask)
    for c in contours:
        if cv2.contourArea(c) < 50:
            continue
        _, _, w, h = cv2.boundingRect(c)
        if w < cell_w * 1.5 and h < cell_h * 1.5:
            cv2.drawContours(clean_mask, [c], -1, 255, -1)

    if np.count_nonzero(clean_mask) < 100:
        return None

    M = cv2.moments(clean_mask)
    if M["m00"] == 0:
        return None

    cx = M["m10"] / M["m00"]
    cy = M["m01"] / M["m00"]

    col = int(cx / cell_w) + 1  # 1-indexed
    row = int(cy / cell_h) + 1  # 1-indexed

    return row, col


def sample_cell_brightness(frame):
    """Returns white-ish pixel count per cell for pulse tracking."""
    cell_w = (GRID_X2 - GRID_X1) / GRID_COLS
    cell_h = (GRID_Y2 - GRID_Y1) / GRID_ROWS
    counts = {}
    for row in range(1, GRID_ROWS + 1):
        for col in range(1, GRID_COLS + 1):
            x1 = int(GRID_X1 + (col - 1) * cell_w)
            y1 = int(GRID_Y1 + (row - 1) * cell_h)
            cell = frame[y1:int(y1 + cell_h), x1:int(x1 + cell_w)]
            hsv = cv2.cvtColor(cell, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv,
                               np.array([0, 0, WHITE_VAL_MIN]),
                               np.array([180, WHITE_SAT_MAX, 255]))
            counts[(row, col)] = int(np.count_nonzero(mask))
    return counts


def draw_grid_overlay(frame):
    cell_w = (GRID_X2 - GRID_X1) / GRID_COLS
    cell_h = (GRID_Y2 - GRID_Y1) / GRID_ROWS
    for r in range(GRID_ROWS + 1):
        y = int(GRID_Y1 + r * cell_h)
        cv2.line(frame, (GRID_X1, y), (GRID_X2, y), (255, 0, 255), 1)
    for c in range(GRID_COLS + 1):
        x = int(GRID_X1 + c * cell_w)
        cv2.line(frame, (x, GRID_Y1), (x, GRID_Y2), (255, 0, 255), 1)


def draw_pixel_ruler(frame, step=50, minor_step=10):
    h, w = frame.shape[:2]
    for x in range(0, w, minor_step):
        if x % step != 0:
            cv2.line(frame, (x, 0), (x, h), (40, 40, 40, 50), 1)
    for y in range(0, h, minor_step):
        if y % step != 0:
            cv2.line(frame, (0, y), (w, y), (40, 40, 40, 50), 1)
    for x in range(0, w, step):
        cv2.line(frame, (x, 0), (x, h), (80, 80, 80), 1)
        cv2.putText(frame, str(x), (x + 2, 12), cv2.FONT_HERSHEY_PLAIN, 0.8, (200, 200, 200), 1)
    for y in range(0, h, step):
        cv2.line(frame, (0, y), (w, y), (80, 80, 80), 1)
        cv2.putText(frame, str(y), (2, y + 10), cv2.FONT_HERSHEY_PLAIN, 0.8, (200, 200, 200), 1)


show_ruler = False

print("Live preview running.")
print("  q — quit  |  s — screenshot  |  g — toggle grid overlay  |  r — toggle pixel ruler")

while True:
    ret, frame = cap.read()
    if not ret:
        print("Failed to capture frame")
        break

    frame = cv2.resize(frame, (DISPLAY_WIDTH, DISPLAY_HEIGHT))

    box = detect_box_number(frame)
    result = detect_selection(frame)

    if pulse_monitoring:
        elapsed = time.time() - pulse_start_time
        remaining = PULSE_DURATION - elapsed
        cv2.putText(frame, f"Pulse scan: {remaining:.1f}s", (10, DISPLAY_HEIGHT - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
        for cell, count in sample_cell_brightness(frame).items():
            pulse_samples.setdefault(cell, []).append(count)
        if elapsed >= PULSE_DURATION:
            pulse_monitoring = False
            pulsating = [cell for cell, vals in pulse_samples.items()
                         if np.var(vals) >= PULSE_VAR_THRESHOLD]
            pulse_samples.clear()
            if pulsating:
                print("Pulsating cells:")
                for r, c in sorted(pulsating):
                    print(f"  Row {r}  Col {c}")
            else:
                print("No pulses found")

    if show_ruler:
        draw_pixel_ruler(frame)
    if show_grid:
        draw_grid_overlay(frame)

    cell_w = (GRID_X2 - GRID_X1) / GRID_COLS
    cell_h = (GRID_Y2 - GRID_Y1) / GRID_ROWS

    if result:
        row, col = result
        cell_w = (GRID_X2 - GRID_X1) / GRID_COLS
        cell_h = (GRID_Y2 - GRID_Y1) / GRID_ROWS
        rx1 = int(GRID_X1 + (col - 1) * cell_w)
        ry1 = int(GRID_Y1 + (row - 1) * cell_h)
        rx2 = int(rx1 + cell_w)
        ry2 = int(ry1 + cell_h)
        cv2.rectangle(frame, (rx1, ry1), (rx2, ry2), (0, 255, 0), 2)
        label = f"Box {box}  Row {row}  Col {col}" if box else f"Row {row}  Col {col}"
        cv2.putText(frame, label, (GRID_X1, GRID_Y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

    cv2.imshow("Live Preview", frame)

    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break
    elif key == ord('s'):
        filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"
        path = os.path.join(SCREENSHOTS_DIR, filename)
        cv2.imwrite(path, frame)
        print(f"Screenshot saved: {path}")
    elif key == ord('g'):
        show_grid = not show_grid
    elif key == ord('r'):
        show_ruler = not show_ruler
    elif key == ord('p') and not pulse_monitoring:
        pulse_monitoring = True
        pulse_start_time = time.time()
        pulse_samples.clear()
        print(f"Pulse scan started ({PULSE_DURATION:.0f}s)...")

cap.release()
cv2.destroyAllWindows()
