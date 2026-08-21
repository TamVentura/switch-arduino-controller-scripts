"""
calibrate.py — live capture + manual drive, for phase-2 vision calibration.

Run this INSTEAD of main.py while calibrating the raid-battle detectors. It:
  - opens the capture-card feed (the OpenCV "Capture" window),
  - tries to open the Xbox passthrough so you drive the Switch by hand (Arduino) —
    OPTIONAL: if it can't (no Arduino / no Xbox pad), it just keeps capturing so
    you can drive the Switch by other means and the assistant can still watch,
  - every `interval` seconds writes the current frame to  calib_frames/live.png
    (the assistant reads this to "watch" the screen), and
  - archives each DISTINCT screen you pass through to  calib_frames/<time>.png
    (deduplicated with template_matcher.fast_equals), so we can pick crop regions
    and cut reference PNGs from real frames.

Everything (including tracebacks) is also written to  calib_frames/calibrate.log
so the assistant can diagnose problems by reading that file.

Close the "Capture" window (press q) to stop.

Coordinates note: the feed is 1080p; the codebase expresses crops in 4K, so a
pixel (x, y) measured in a frame maps to 4K as (2*x, 2*y).
"""

import json
import os
import sys
import threading
import time
import traceback

_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_DIR = os.path.join(_DIR, "calib_frames")
LIVE_PATH = os.path.join(ARCHIVE_DIR, "live.png")
LOG_PATH = os.path.join(ARCHIVE_DIR, "calibrate.log")

os.makedirs(ARCHIVE_DIR, exist_ok=True)


def _log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print("[CALIB]", msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# Fresh log each run.
try:
    open(LOG_PATH, "w", encoding="utf-8").close()
except Exception:
    pass

_log("starting calibrate.py")

# switch-arduino bootstrap (adds utils/ to sys.path, wires modules).
sys.path.insert(0, os.path.join(_DIR, ".."))
try:
    from switch import capture, click, controller  # noqa: E402
    import cv2  # noqa: E402
    import template_matcher  # noqa: E402
    _log("imports OK (switch bootstrap, cv2, template_matcher)")
except Exception:
    _log("IMPORT FAILED:\n" + traceback.format_exc())
    raise


def _load_port():
    try:
        with open(os.path.join(_DIR, "config.json"), encoding="utf-8") as f:
            return json.load(f).get("com_port")
    except Exception:
        return None


def _frame_dumper(interval: float = 0.7, dedup_max_diff: float = 8.0) -> None:
    last_archived = None
    dumped = 0
    while not capture.is_stopped():
        try:
            frame = capture.get_latest_frame()
        except Exception:
            time.sleep(interval)
            continue

        # NOTE: cv2.imwrite picks the encoder from the extension, so the temp
        # file must keep a .png extension (…live.tmp.png), not ".png.tmp".
        tmp = os.path.join(ARCHIVE_DIR, "live.tmp.png")
        try:
            cv2.imwrite(tmp, frame)
            os.replace(tmp, LIVE_PATH)
            dumped += 1
            if dumped == 1:
                _log(f"first live frame written ({frame.shape[1]}x{frame.shape[0]}) -> {LIVE_PATH}")
        except Exception:
            _log("live write failed:\n" + traceback.format_exc())

        try:
            is_new = last_archived is None or not template_matcher.fast_equals(
                frame, last_archived, max_diff=dedup_max_diff)
        except Exception:
            _log("fast_equals failed:\n" + traceback.format_exc())
            is_new = False

        if is_new:
            fn = os.path.join(ARCHIVE_DIR, time.strftime("%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}.png")
            try:
                cv2.imwrite(fn, frame)
                last_archived = frame.copy()
                _log(f"new distinct screen -> {os.path.basename(fn)}")
            except Exception:
                _log("archive write failed:\n" + traceback.format_exc())

        time.sleep(interval)
    _log("frame dumper stopped (capture stopped)")


def main_calib() -> None:
    port = _load_port()
    _log(f"config com_port = {port!r}")

    # Controller is OPTIONAL for calibration — only needed to drive via Arduino.
    try:
        controller.connect(port)
        _log("controller connected")
    except Exception:
        _log("controller.connect failed (continuing capture-only):\n" + traceback.format_exc())

    try:
        capture.start_capture(press_callback=click, debug_regions=[])
        capture.wait_ready()
        _log("capture started and ready")
    except Exception:
        _log("capture FAILED to start:\n" + traceback.format_exc())
        raise

    threading.Thread(target=_frame_dumper, daemon=True, name="CalibFrameDumper").start()

    _log("=" * 60)
    _log(f"Live frame  -> {LIVE_PATH}")
    _log(f"Distinct screens archived in: {ARCHIVE_DIR}")
    _log("Drive the Switch (Xbox passthrough if available). Close 'Capture' (q) to stop.")
    _log("=" * 60)

    # Passthrough is OPTIONAL. If it can't run, keep capturing so the assistant
    # can still watch while you drive by other means.
    try:
        import xbox_passthrough  # noqa: E402
        xbox_passthrough.run(arduino_port=port)
        _log("passthrough window closed")
    except Exception:
        _log("passthrough unavailable (continuing capture-only):\n" + traceback.format_exc())

    # Keep the process (and capture + dumper) alive until the Capture window is
    # closed, even if the passthrough returned/failed.
    while not capture.is_stopped():
        time.sleep(0.5)
    _log("capture stopped — exiting")


if __name__ == "__main__":
    try:
        main_calib()
    except Exception:
        _log("FATAL:\n" + traceback.format_exc())
        raise
