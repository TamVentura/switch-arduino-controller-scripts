import calendar
import os
import sys
import threading
import time

from colorama import Fore, Style, init

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "utils"))
import capture
import controller
import ocr

init()

controller.connect("COM6")
# controller.connect(None)  # disable serial

press = controller.press
hold = controller.hold

_COUNT_SEQUENCE = [1, 5, 10]

# ---------------------------------------------------------------------------
# Crop regions (x1, y1, x2, y2) for each date field on a 1920x1080 frame
# ---------------------------------------------------------------------------
_DATE_REGIONS = {
    "day": (260, 466, 370, 570),
    "month": (469, 466, 580, 570),
    "year": (681, 466, 853, 570),
    "hour": (1023, 466, 1117, 570),
    "minute": (1230, 466, 1332, 570),
}

_COUNT_REGION = (1668, 370, 1716, 406)
_COUNT_REGION_LOTTO = (1641, 458, 1743, 515)

# Regions shown in the debug overlay — date fields + both count boxes
_DEBUG_REGIONS = [
    ("day", 260, 466, 370, 570, (0, 255, 0)),
    ("month", 469, 466, 580, 570, (0, 200, 255)),
    ("year", 681, 466, 853, 570, (255, 100, 0)),
    ("hour", 1023, 466, 1117, 570, (255, 0, 200)),
    ("minute", 1230, 466, 1332, 570, (0, 100, 255)),
    ("count", 1668, 370, 1716, 406, (255, 255, 0)),
    ("count_lotto", 1641, 458, 1743, 515, (0, 255, 255)),
]

_DATE_OCR_CONFIGS = [
    "--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789",
    "--oem 1 --psm 8 -c tessedit_char_whitelist=0123456789",
    "--oem 1 --psm 13 -c tessedit_char_whitelist=0123456789",
    "--oem 1 --psm 6 -c tessedit_char_whitelist=0123456789",
]
_DATE_RANGES = {
    "day": (1, 31),
    "month": (1, 12),
    "year": (2000, 2099),
    "hour": (0, 23),
    "minute": (0, 59),
}

_COUNT_OCR_CONFIGS = [
    "--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789",
    "--oem 1 --psm 8 -c tessedit_char_whitelist=0123456789",
    "--oem 1 --psm 10 -c tessedit_char_whitelist=0123456789",
    "--oem 1 --psm 13 -c tessedit_char_whitelist=0123456789",
    "--oem 1 --psm 6 -c tessedit_char_whitelist=0123456789",
]


def read_count():
    """Read the item count from the count region. Returns 1, 5, or 10, or None."""
    frame = capture.get_latest_frame()
    if frame is None:
        return None
    for region in (_COUNT_REGION, _COUNT_REGION_LOTTO):
        value = ocr.read_int(
            frame, *region, invert=True, configs=_COUNT_OCR_CONFIGS, debug_name="count"
        )
        if value in (1, 5, 10):
            return value
    return None


def read_current_date():
    """Read the date/time shown on the Switch Date and Time settings screen.
    Returns (day, month, year, hour, minute) as ints, or None if no frame."""
    frame = capture.get_latest_frame()
    if frame is None:
        return None
    result = []
    for name, coords in _DATE_REGIONS.items():
        value = ocr.read_int(
            frame,
            *coords,
            configs=_DATE_OCR_CONFIGS,
            valid_range=_DATE_RANGES[name],
            debug_name=name,
        )
        result.append(value)
    return tuple(result)


def _shortest_path(current, target, size):
    """Returns (key, count) for the shortest circular path.
    '4' = up (increment), '2' = down (decrement)."""
    if current == target:
        return None, 0
    fwd = (target - current) % size
    bwd = (current - target) % size
    return ("4", fwd) if fwd <= bwd else ("2", bwd)


def set_date(target_day, target_month, target_year, target_hour, target_minute):

    change_time_delay = 0.15
    change_input_delay = 0.15

    current = read_current_date()
    print(current)
    if current is None:
        print(f"{Fore.RED}Error:{Style.RESET_ALL} No frame available")
        return
    cur_day, cur_month, cur_year, cur_hour, cur_minute = current

    # Navigate Day → Year (skip month)
    press("1", change_input_delay)  # Day → Month
    press("1", change_input_delay)  # Month → Year

    # Year (2000–2099)
    key, count = _shortest_path(cur_year, target_year, 10000)
    if count:
        press(key, change_time_delay, count)
    press("3", change_input_delay)  # Year → Month

    # Month (1–12)
    key, count = _shortest_path(cur_month, target_month, 12)
    if count:
        press(key, change_time_delay, count)
    press("3", change_input_delay)  # Month → Day

    # Day — re-read since changing month may have clamped it
    current = read_current_date()
    print(current)
    if current is not None:
        cur_day = current[0]
    max_days = calendar.monthrange(target_year, target_month)[1]
    key, count = _shortest_path(cur_day, target_day, max_days)
    if count:
        press(key, change_time_delay, count)

    # Navigate Day → Hour (skip month and year)
    press("1", change_input_delay)  # Day → Month
    press("1", change_input_delay)  # Month → Year
    press("1", change_input_delay)  # Year → Hour

    # Hour (0–23)
    key, count = _shortest_path(cur_hour, target_hour, 24)
    if count:
        press(key, change_time_delay, count)
    press("1", change_input_delay)  # Hour → Minute

    # Minute (0–59)
    key, count = _shortest_path(cur_minute, target_minute, 60)
    if count:
        press(key, change_time_delay, count)


def _navigate_count(target):
    """Navigate the count selector to target (1, 5, or 10) using L/R triggers."""
    current = read_count()
    print(current)
    if current is None or current == target:
        return
    idx_cur = _COUNT_SEQUENCE.index(current)
    idx_tgt = _COUNT_SEQUENCE.index(target)
    size = len(_COUNT_SEQUENCE)
    fwd = (idx_tgt - idx_cur) % size
    bwd = (idx_cur - idx_tgt) % size
    if fwd <= bwd:
        press("R", 0.2, fwd)
    else:
        press("L", 0.2, bwd)


def run_regular(target_count):
    """Set count to target then run the item printer."""
    _navigate_count(target_count)
    press("X", 2)
    press("A", 5)
    press("A", 15)
    press("A", 2)
    press("A")
    capture.wait_for_pixel(39, 1050, "#0254a0")
    time.sleep(0.5)
    press("B", 5)


def parse_recipes(content):
    """Parse recipes — one per line: YYYY-MM-DD HH:MM:SS count1 [count2 ...]
    Returns list of ((year, month, day, hour, minute, second), [counts]) tuples."""
    recipes = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        t = time.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M:%S")
        date_tuple = (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)
        counts = [int(p) for p in parts[2:]]
        recipes.append((date_tuple, counts))
    return recipes


def run_combo(counts):
    """Run multiple sequential prints from one date seed. B only on the last."""
    for i, count in enumerate(counts):
        _navigate_count(count)
        press("X", 2)
        press("A", 5)
        press("A", 15)
        press("A", 2)
        press("A", 5)
        capture.wait_for_pixel(39, 1050, "#0254a0")
        time.sleep(0.5)
        if i == len(counts) - 1:
            press("B", 3)


def manip():
    while not capture.is_stopped():
        line = input("Recipe: ").strip()
        if not line:
            continue

        if line.lower() == "loop":
            repeat = int(input("Repeat: ").strip())
            recipe_lines = []
            while True:
                l = input("Recipe / EndLoop: ").strip()
                if l.lower() == "endloop":
                    break
                if l:
                    recipe_lines.append(l)
            recipes = parse_recipes("\n".join(recipe_lines))
        else:
            recipes = parse_recipes(line)
            repeat = 1

        if not recipes:
            print(f"{Fore.RED}Invalid recipe:{Style.RESET_ALL} {line}")
            continue

        for _ in range(repeat):
            for (year, month, day, hour, minute, second), counts in recipes:
                press("A", 2)
                press("A", 1)
                if capture.is_stopped():
                    break
                gotoDateSettings()
                set_date(day, month, year, hour, minute)
                afterDateSettings(second)
                time.sleep(3)
                if len(counts) == 1:
                    run_regular(counts[0])
                else:
                    run_combo(counts)
            if capture.is_stopped():
                break


def gotoDateSettings():
    press("H", 1)
    press("2")
    hold("1", 0.65, 0.1)
    press("A", 1)
    hold("2", 1.5, 0.1)
    press("1")
    hold("2", 0.85, 0.1)
    press("A")
    hold("2", 0.4, 0.1)
    press("A")


def afterDateSettings(seconds):
    t = threading.Timer(
        seconds - 1.3,
        press,
        args=("A", 2),
    )
    press("1", 0.5)
    press("A", 0)
    t.start()
    time.sleep(0.5)
    press("H", 0.5, repeat=2)
    t.join()


def _key_handler(key, frame):
    """Handle project-specific keys not consumed by the capture lib."""
    if key == ord("d"):
        print(f"{Fore.CYAN}Date:{Style.RESET_ALL} {read_current_date()}")
    elif key == ord("c"):
        print(f"{Fore.CYAN}Count:{Style.RESET_ALL} {read_count()}")


if __name__ == "__main__":
    capture.start_capture(
        press_callback=press,
        debug_regions=_DEBUG_REGIONS,
        key_callback=_key_handler,
    )
    capture.wait_ready()
    try:
        manip()
    except KeyboardInterrupt:
        pass
    finally:
        capture.stop()
