import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from switch import *

import calendar
import threading
import time

_COUNT_SEQUENCE = [1, 5, 10]

# ---------------------------------------------------------------------------
# Crop regions (x1, y1, x2, y2) expressed in 4K (3840×2160)
# ---------------------------------------------------------------------------
_DATE_REGIONS = {
    "day": (520, 932, 740, 1140),
    "month": (938, 932, 1160, 1140),
    "year": (1362, 932, 1706, 1140),
    "hour": (2046, 932, 2234, 1140),
    "minute": (2460, 932, 2664, 1140),
}

_COUNT_REGION = (3336, 740, 3432, 812)
_COUNT_REGION_LOTTO = (3282, 916, 3486, 1030)

# Regions shown in the debug overlay — date fields + both count boxes
_DEBUG_REGIONS = [
    ("day", 520, 932, 740, 1140, (0, 255, 0)),
    ("month", 938, 932, 1160, 1140, (0, 200, 255)),
    ("year", 1362, 932, 1706, 1140, (255, 100, 0)),
    ("hour", 2046, 932, 2234, 1140, (255, 0, 200)),
    ("minute", 2460, 932, 2664, 1140, (0, 100, 255)),
    ("count", 3336, 740, 3432, 812, (255, 255, 0)),
    ("count_lotto", 3282, 916, 3486, 1030, (0, 255, 255)),
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
    click("1", change_input_delay)  # Day → Month
    click("1", change_input_delay)  # Month → Year

    # Year (2000–2099)
    key, count = _shortest_path(cur_year, target_year, 10000)
    if count:
        click(key, change_time_delay, count)
    click("3", change_input_delay)  # Year → Month

    # Month (1–12)
    key, count = _shortest_path(cur_month, target_month, 12)
    if count:
        click(key, change_time_delay, count)
    click("3", change_input_delay)  # Month → Day

    # Day — re-read since changing month may have clamped it
    current = read_current_date()
    print(current)
    if current is not None:
        cur_day = current[0]
    max_days = calendar.monthrange(target_year, target_month)[1]
    key, count = _shortest_path(cur_day, target_day, max_days)
    if count:
        click(key, change_time_delay, count)

    # Navigate Day → Hour (skip month and year)
    click("1", change_input_delay)  # Day → Month
    click("1", change_input_delay)  # Month → Year
    click("1", change_input_delay)  # Year → Hour

    # Hour (0–23)
    key, count = _shortest_path(cur_hour, target_hour, 24)
    if count:
        click(key, change_time_delay, count)
    click("1", change_input_delay)  # Hour → Minute

    # Minute (0–59)
    key, count = _shortest_path(cur_minute, target_minute, 60)
    if count:
        click(key, change_time_delay, count)


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
        click("R", 0.2, fwd)
    else:
        click("L", 0.2, bwd)


def run_regular(target_count):
    """Set count to target then run the item printer."""
    _navigate_count(target_count)
    click("X", 2)
    click("A", 5)
    click("A", 15)
    click("A", 2)
    click("A")
    capture.wait_for_pixel(78, 2100, "#0f4f93")
    time.sleep(0.5)
    click("B", 5)


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
        click("X", 2)
        click("A", 5)
        click("A", 15)
        click("A", 2)
        click("A", 5)
        capture.wait_for_pixel(78, 2100, "#0254a0")
        time.sleep(0.5)
        if i == len(counts) - 1:
            click("B", 3)


def parse_script(content):
    """Parse a full recipe script into ordered (repeat, recipes) segments.

    Understands one or more `loop <n> ... endloop` blocks and loose recipe
    lines (run once), in any order — so a whole block (or several, like the
    recipes/*.txt files) can be pasted at once. Reuses parse_recipes for the
    per-line date+count parsing.
    """
    segments = []
    lines = content.splitlines()
    loose = []

    def flush_loose():
        if loose:
            recs = parse_recipes("\n".join(loose))
            if recs:
                segments.append((1, recs))
            loose.clear()

    i, n = 0, len(lines)
    while i < n:
        raw = lines[i].strip()
        i += 1
        if not raw:
            continue
        low = raw.lower()
        if low == "loop":
            flush_loose()
            # Next non-empty line is the repeat count.
            repeat = None
            while i < n:
                cand = lines[i].strip()
                i += 1
                if not cand:
                    continue
                try:
                    repeat = int(cand)
                except ValueError:
                    print(f"{Fore.RED}Invalid loop count:{Style.RESET_ALL} {cand}")
                break
            # Body runs until endloop (or end of input, if it was omitted).
            body = []
            while i < n:
                cand = lines[i].strip()
                i += 1
                if cand.lower() == "endloop":
                    break
                if cand:
                    body.append(cand)
            if repeat is not None:
                recs = parse_recipes("\n".join(body))
                if recs:
                    segments.append((repeat, recs))
        elif low == "endloop":
            continue  # stray endloop — ignore
        else:
            loose.append(raw)

    flush_loose()
    return segments


def _drain_console_lines(initial_wait=0.2, settle=0.06):
    """Read any console lines already buffered from a multi-line paste.

    input() only returns the first pasted line; the rest sit in the Windows
    console buffer. This consumes them (so they aren't discarded by the
    notifier's flush_input on the next prompt) and returns them as a list of
    strings. Empty when nothing is pending (e.g. a single line typed by hand).
    """
    try:
        import msvcrt
    except ImportError:
        return []
    collected = []
    buf = ""
    prev = ""
    deadline = time.time() + initial_wait
    while True:
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                if ch == "\n" and prev == "\r":
                    prev = ch
                    continue
                collected.append(buf)
                buf = ""
            elif ch == "\x03":  # Ctrl-C
                raise KeyboardInterrupt
            elif ch in ("\b", "\x7f"):
                buf = buf[:-1]
            else:
                buf += ch
            prev = ch
            deadline = time.time() + settle  # keep draining while chars arrive
        elif time.time() >= deadline:
            break
        else:
            time.sleep(0.005)
    if buf:
        collected.append(buf)
    return collected


def run_recipes(recipes):
    """Run one pass over a list of parsed recipes. Stops early if requested."""
    for (year, month, day, hour, minute, second), counts in recipes:
        click("A", 2)
        click("A", 1)
        if capture.is_stopped():
            return
        gotoDateSettings()
        set_date(day, month, year, hour, minute)
        afterDateSettings(second)
        time.sleep(3)
        if len(counts) == 1:
            run_regular(counts[0])
        else:
            run_combo(counts)


def manip():
    while not capture.is_stopped():
        #INSERTED NOTIFIER HERE
        _r = notifier.send_and_wait(
            text="Recipe:",
            console_prompt="Recipe: ",
            wait_for_text=True,
        )
        first = (_r.action_value or "")
        # first = input("Recipe: ")
        # A console paste only hands us its first line here; the rest waits in
        # the terminal buffer. Drain it now so the whole block is parsed as one.
        extra = _drain_console_lines()

        stripped = first.strip()
        if not stripped and not extra:
            continue

        if not extra and stripped.lower() == "loop":
            # Bare `loop` typed by hand → keep the interactive gathering flow.
            #INSERTED NOTIFIER HERE
            _r = notifier.send_and_wait(
                text="Repeat:",
                console_prompt="Repeat: ",
                wait_for_text=True,
            )
            repeat = int((_r.action_value or "1").strip())
            # repeat = int(input("Repeat: ").strip())
            recipe_lines = []
            while True:
                #INSERTED NOTIFIER HERE
                _r = notifier.send_and_wait(
                    text="Recipe / EndLoop:",
                    console_prompt="Recipe / EndLoop: ",
                    wait_for_text=True,
                )
                l = (_r.action_value or "").strip()
                # l = input("Recipe / EndLoop: ").strip()
                if l.lower() == "endloop":
                    break
                if l:
                    recipe_lines.append(l)
            segments = [(repeat, parse_recipes("\n".join(recipe_lines)))]
        else:
            # Pasted block (loop…endloop, several blocks, or loose recipes),
            # from the console buffer or a multi-line Telegram message.
            full = "\n".join([first] + extra)
            segments = parse_script(full)

        segments = [(r, recs) for r, recs in segments if recs]
        if not segments:
            print(f"{Fore.RED}Invalid recipe:{Style.RESET_ALL} {stripped}")
            continue

        for repeat, recipes in segments:
            for _ in range(repeat):
                run_recipes(recipes)
                if capture.is_stopped():
                    break
            if capture.is_stopped():
                break


def gotoDateSettings():
    click("H", 1)
    click("2")
    hold_for("1", 0.65, 0.1)
    click("A", 1)
    hold_for("2", 1.5, 0.1)
    click("1")
    hold_for("2", 0.85, 0.1)
    click("A")
    hold_for("2", 0.4, 0.1)
    click("A")


def afterDateSettings(seconds):
    t = threading.Timer(
        seconds - 1.3,
        click,
        args=("A", 2),
    )
    click("1", 0.5)
    click("A", 0)
    t.start()
    time.sleep(0.5)
    click("H", 0.5, repeat=2)
    t.join()


def _key_handler(key, frame):
    """Handle project-specific keys not consumed by the capture lib."""
    if key == ord("d"):
        print(f"{Fore.CYAN}Date:{Style.RESET_ALL} {read_current_date()}")
    elif key == ord("c"):
        print(f"{Fore.CYAN}Count:{Style.RESET_ALL} {read_count()}")


if __name__ == "__main__":
    launch(manip, debug_regions=_DEBUG_REGIONS, key_callback=_key_handler)
