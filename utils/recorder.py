"""
recorder.py — Programmatic command recorder for controller.py.

Intercepts calls to controller functions via monkey-patching, records each
command with the idle time (sleep) that preceded it, and saves the sequence
as JSON for later replay.

Usage
-----
    import recorder
    import controller

    controller.connect()
    recorder.start_recording()

    controller.click("A", sleep=0.5)
    controller.hold_for("w", duration=1.2)

    path = recorder.end_recording("my_sequence")
    # → saved_outputs/my_sequence_20260413_153000.json

    recorder.play_recording(path)

Functions recorded
------------------
    click, hold, release, hold_for, set_left_stick, set_right_stick

    Internal hold/release calls made by click() or hold_for() are suppressed
    to avoid double-recording.
"""

import json
import math
import os
import time
from datetime import datetime

import controller

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_recording      = False
_commands: list = []
_start_time     = 0.0
_last_cmd_end   = 0.0   # perf_counter() value when the last command finished
_in_outer_call  = False  # True while click/hold_for is executing (suppresses inner hold/release)
_originals: dict = {}

_SAVE_DIR = os.path.join(os.path.dirname(__file__), "..", "saved_outputs")


def _sleep_before() -> float:
    """Seconds of idle time since the previous command ended."""
    return round(time.perf_counter() - _last_cmd_end, 4)


# ---------------------------------------------------------------------------
# Wrapper functions
# ---------------------------------------------------------------------------

def _wrap_click(key, sleep=0.5, repeat=1):
    global _in_outer_call, _last_cmd_end
    sb = _sleep_before()
    _commands.append({"sleep_before": sb, "cmd": "click", "args": {"key": key, "sleep": sleep, "repeat": repeat}})
    _in_outer_call = True
    try:
        _originals["click"](key, sleep=sleep, repeat=repeat)
    finally:
        _in_outer_call = False
        _last_cmd_end = time.perf_counter()


def _wrap_hold(key):
    global _last_cmd_end
    if _in_outer_call:
        # Called internally by click() or hold_for() — don't double-record
        _originals["hold"](key)
        return
    sb = _sleep_before()
    _commands.append({"sleep_before": sb, "cmd": "hold", "args": {"key": key}})
    _originals["hold"](key)
    _last_cmd_end = time.perf_counter()


def _wrap_release(key):
    global _last_cmd_end
    if _in_outer_call:
        # Called internally by click() or hold_for() — don't double-record
        _originals["release"](key)
        return
    sb = _sleep_before()
    _commands.append({"sleep_before": sb, "cmd": "release", "args": {"key": key}})
    _originals["release"](key)
    _last_cmd_end = time.perf_counter()


def _wrap_hold_for(key, duration, sleep=0.1):
    global _in_outer_call, _last_cmd_end
    sb = _sleep_before()
    _commands.append({"sleep_before": sb, "cmd": "hold_for", "args": {"key": key, "duration": duration, "sleep": sleep}})
    _in_outer_call = True
    try:
        _originals["hold_for"](key, duration=duration, sleep=sleep)
    finally:
        _in_outer_call = False
        _last_cmd_end = time.perf_counter()


def _wrap_set_left_stick(x: float, y: float):
    global _last_cmd_end
    sb = _sleep_before()
    if _originals["set_left_stick"](x, y) is not False:
        _commands.append({"sleep_before": sb, "cmd": "set_left_stick", "args": {"x": x, "y": y}})
        _last_cmd_end = time.perf_counter()


def _wrap_set_right_stick(x: float, y: float):
    global _last_cmd_end
    sb = _sleep_before()
    if _originals["set_right_stick"](x, y) is not False:
        _commands.append({"sleep_before": sb, "cmd": "set_right_stick", "args": {"x": x, "y": y}})
        _last_cmd_end = time.perf_counter()


_WRAPPERS = {
    "click":           _wrap_click,
    "hold":            _wrap_hold,
    "release":         _wrap_release,
    "hold_for":        _wrap_hold_for,
    "set_left_stick":  _wrap_set_left_stick,
    "set_right_stick": _wrap_set_right_stick,
}

_STICK_COMMANDS = {"set_left_stick", "set_right_stick"}


def _stick_to_byte(value: float) -> int:
    return max(0, min(255, round((value + 1.0) * 127.5)))


def _byte_to_stick(value: int) -> float:
    return (value / 127.5) - 1.0


def _entry_duration(commands: list[dict], index: int) -> float:
    if index + 1 >= len(commands):
        return 0.0
    return float(commands[index + 1].get("sleep_before", 0.0))


def _build_merged_stick_entry(commands: list[dict], start: int, end: int) -> dict | None:
    total_duration = 0.0
    weighted_bx = 0.0
    weighted_by = 0.0

    for index in range(start, end + 1):
        duration = _entry_duration(commands, index)
        if duration <= 0:
            continue
        args = commands[index]["args"]
        bx = _stick_to_byte(float(args["x"]))
        by = _stick_to_byte(float(args["y"]))
        weighted_bx += bx * duration
        weighted_by += by * duration
        total_duration += duration

    if total_duration <= 0:
        return None

    merged_bx = round(weighted_bx / total_duration)
    merged_by = round(weighted_by / total_duration)
    return {
        "sleep_before": commands[start].get("sleep_before", 0.0),
        "cmd": commands[start]["cmd"],
        "args": {
            "x": round(_byte_to_stick(merged_bx), 6),
            "y": round(_byte_to_stick(merged_by), 6),
        },
    }



def _segment_within_tolerance(
    commands: list[dict],
    start: int,
    end: int,
    merged_entry: dict,
    tolerance: float,
) -> bool:
    merged_x = float(merged_entry["args"]["x"])
    merged_y = float(merged_entry["args"]["y"])

    for index in range(start, end + 1):
        args = commands[index]["args"]
        if abs(float(args["x"]) - merged_x) > tolerance:
            return False
        if abs(float(args["y"]) - merged_y) > tolerance:
            return False
    return True


def merge_stick_jitter(
    data: dict,
    *,
    tolerance: float = 0.2,
    min_group_size: int = 2,
) -> dict:
    """Return a copy of a recording JSON with jittery stick runs merged.

    Consecutive `set_left_stick` / `set_right_stick` entries are grouped when
    their values stay within `tolerance` of a single duration-weighted average.
    The replacement entry preserves the same pre-command sleep and reproduces
    the same final integrated stick effect under the recording timing model.
    """
    commands = list(data.get("commands", []))
    merged_commands: list[dict] = []

    index = 0
    while index < len(commands):
        current = commands[index]
        cmd = current.get("cmd")

        if cmd not in _STICK_COMMANDS:
            merged_commands.append(current)
            index += 1
            continue

        end = index
        best_end = index
        best_entry = None

        while end + 1 < len(commands) and commands[end + 1].get("cmd") == cmd:
            candidate_end = end + 1
            merged_entry = _build_merged_stick_entry(commands, index, candidate_end)
            if merged_entry is None:
                break
            if(commands[index]["args"]["x"] == 0 and commands[index]["args"]["y"] == 0):
                break
            if not _segment_within_tolerance(commands, index, candidate_end, merged_entry, tolerance):
                break
            best_end = candidate_end
            best_entry = merged_entry
            end = candidate_end

        group_size = best_end - index + 1
        if best_entry is not None and group_size >= min_group_size:
            merged_commands.append(best_entry)
            if best_end + 1 < len(commands):
                next_command = dict(commands[best_end + 1])

                # Jitter path is a zigzag; the merged movement is a straight line.
                # A straight movement covers less distance at the same speed, so it
                # takes proportionally less time.  Use the Pythagorean theorem to
                # compute both distances and scale the movement time accordingly.
                jitter_time = sum(
                    float(commands[i].get("sleep_before", 0.0))
                    for i in range(index + 1, best_end + 1)
                )
                jitter_distance = sum(
                    math.sqrt(
                        (float(commands[i + 1]["args"]["x"]) - float(commands[i]["args"]["x"])) ** 2 +
                        (float(commands[i + 1]["args"]["y"]) - float(commands[i]["args"]["y"])) ** 2
                    )
                    for i in range(index, best_end)
                )
                straight_distance = math.sqrt(
                    (float(commands[best_end]["args"]["x"]) - float(commands[index]["args"]["x"])) ** 2 +
                    (float(commands[best_end]["args"]["y"]) - float(commands[index]["args"]["y"])) ** 2
                )
                movement_time = (
                    jitter_time * (straight_distance / jitter_distance)
                    if jitter_distance > 0 else jitter_time
                )

                original_sn = float(commands[best_end + 1].get("sleep_before", 0.0))
                next_command["sleep_before"] = round(original_sn + movement_time, 4)
                commands[best_end + 1] = next_command
            index = best_end + 1
            continue

        merged_commands.append(current)
        index += 1

    result = dict(data)
    result["commands"] = merged_commands
    result["merged_commands_removed"] = len(commands) - len(merged_commands)
    result["original_command_count"] = len(commands)
    result["optimized_command_count"] = len(merged_commands)
    return result

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_recording():
    """Begin recording controller commands.

    Patches controller.py functions so every call is logged with the idle time
    (sleep) that preceded it.
    Raises RuntimeError if already recording.
    """
    global _recording, _commands, _start_time, _last_cmd_end, _originals

    if _recording:
        raise RuntimeError("Already recording; call end_recording() first")

    _commands      = []
    _originals     = {}
    _recording     = True
    _start_time    = time.perf_counter()
    _last_cmd_end  = time.perf_counter()  # first command's sleep_before counts from here

    for name, wrapper in _WRAPPERS.items():
        _originals[name] = getattr(controller, name)
        setattr(controller, name, wrapper)

    print("Recording started.")


def end_recording(name: str | None = None) -> str:
    """Stop recording and save the sequence to saved_outputs/.

    Parameters
    ----------
    name : str, optional
        Base name for the output file. Defaults to "recording".
        File is saved as  saved_outputs/<name>_<timestamp>.json

    Returns
    -------
    str
        Absolute path to the saved JSON file.
    """
    global _recording, _originals

    if not _recording:
        raise RuntimeError("Not currently recording; call start_recording() first")

    _recording = False
    duration   = time.perf_counter() - _start_time

    # Restore original controller functions
    for fname, orig in _originals.items():
        setattr(controller, fname, orig)
    _originals.clear()

    # Build and write JSON
    os.makedirs(_SAVE_DIR, exist_ok=True)
    stamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    base     = name or "recording"
    filename = f"{base}_{stamp}.json"
    path     = os.path.abspath(os.path.join(_SAVE_DIR, filename))

    data = {
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "duration":    round(duration, 3),
        "commands":    _commands,
    }

    with open(path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Recording saved → {path}  ({len(_commands)} commands, {duration:.1f}s)")
    return path


def toggle_recording(name: str | None = None) -> str | None:
    """Toggle recording on/off.

    If not recording, starts a new recording.
    If recording, stops and saves, returning the path to the saved file.

    Parameters
    ----------
    name : str, optional
        Passed to end_recording() when stopping. Ignored when starting.

    Returns
    -------
    str or None
        Path to the saved file when stopping, None when starting.
    """
    if _recording:
        return end_recording(name)
    else:
        start_recording()
        return None


def play_recording(path: str):
    """Replay a saved recording.

    Parameters
    ----------
    path : str
        Path to the JSON file produced by end_recording().

    Each command is preceded by a time.sleep() matching the idle gap that was
    recorded between commands, preserving the original timing faithfully.
    """
    with open(path) as f:
        data = json.load(f)

    commands = data.get("commands", [])
    print(f"Playing {len(commands)} commands from {os.path.basename(path)}  "
          f"(recorded {data.get('recorded_at', '?')}, {data.get('duration', '?')}s)")

    _dispatch = {
        "click":           lambda a: controller.click(**a),
        "hold":            lambda a: controller.hold(**a),
        "release":         lambda a: controller.release(**a),
        "hold_for":        lambda a: controller.hold_for(**a),
        "set_left_stick":  lambda a: controller.set_left_stick(**a),
        "set_right_stick": lambda a: controller.set_right_stick(**a),
    }

    for entry in commands:
        sb = entry.get("sleep_before", 0)
        if sb > 0:
            time.sleep(sb)

        cmd = entry["cmd"]
        fn  = _dispatch.get(cmd)
        if fn:
            fn(entry["args"])
        else:
            print(f"  [recorder] Unknown command skipped: {cmd!r}")

    print("Playback complete.")
