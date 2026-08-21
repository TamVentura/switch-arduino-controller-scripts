"""
calib_drive.py — TEMP calibration harness (DELETE at end of the phase-2 live pass).

A tiny file-queue command server so the assistant can drive the Switch 2 (via the
Arduino on COM6) and grab screenshots ON DEMAND, while owning the Elgato capture
device (calibrate.py / main.py can't share it under DSHOW).

Why a server (not one-shot scripts): opening the Elgato + connecting COM6 costs a
few seconds each time and would flicker the capture window. This process opens them
ONCE, shows the "Capture" window (so the user can see the feed and power on the
Switch), and then executes commands dropped into  calib_frames/q/  as files.

Command protocol
----------------
Drop a text file in  calib_frames/q/  (any name; processed in sorted order). Each
line is one command:

    click TOKEN [sleep] [repeat]   e.g.  click A 0.3      |  click 2 0.1 3
    hold  TOKEN SECONDS            e.g.  hold R 0.5
    sleep SECONDS
    shot  [LABEL]                  save calib_frames/shot_<LABEL>_<time>.png (+ live.png)
    burst N INTERVAL [LABEL]       N shots INTERVAL s apart (for timings/animations)
    stop                           shut the server down

Everything is logged (with timestamps) to  calib_frames/drive.log , and a slow
heartbeat keeps  calib_frames/live.png  fresh (~2 s) so the assistant can glance at
the current screen without a round-trip. Close the Capture window (q) to stop.

Tokens (controller.click): D-pad 1=Right 2=Down 3=Left 4=Up; A B X Y L R + - ...
"""

import os
import sys
import time
import glob
import shutil
import threading
import traceback

_DIR = os.path.dirname(os.path.abspath(__file__))
_CALIB = os.path.join(_DIR, "calib_frames")
_Q = os.path.join(_CALIB, "q")
_DONE = os.path.join(_Q, "done")
_LIVE = os.path.join(_CALIB, "live.png")
_LOG = os.path.join(_CALIB, "drive.log")

os.makedirs(_Q, exist_ok=True)
os.makedirs(_DONE, exist_ok=True)


def _log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print("[DRIVE]", msg)
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


try:
    open(_LOG, "w", encoding="utf-8").close()
except Exception:
    pass

_log("starting calib_drive.py")

sys.path.insert(0, os.path.join(_DIR, ".."))
try:
    from switch import capture, click, controller  # noqa: E402
    from controller import hold_for  # noqa: E402
    import cv2  # noqa: E402
    _log("imports OK")
except Exception:
    _log("IMPORT FAILED:\n" + traceback.format_exc())
    raise


def _load_port():
    import json
    try:
        with open(os.path.join(_DIR, "config.json"), encoding="utf-8") as f:
            return json.load(f).get("com_port")
    except Exception:
        return None


def _save_shot(label: str) -> None:
    try:
        frame = capture.get_latest_frame()
    except Exception:
        _log(f"shot '{label}': NO FRAME available")
        return
    stamp = time.strftime("%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
    safe = "".join(c for c in label if c.isalnum() or c in "-_") or "shot"
    fn = os.path.join(_CALIB, f"shot_{safe}_{stamp}.png")
    try:
        cv2.imwrite(fn, frame)
        tmp = os.path.join(_CALIB, "live.tmp.png")
        cv2.imwrite(tmp, frame)
        os.replace(tmp, _LIVE)
        _log(f"shot '{label}' -> {os.path.basename(fn)} ({frame.shape[1]}x{frame.shape[0]})")
    except Exception:
        _log("shot write failed:\n" + traceback.format_exc())


def _heartbeat(interval: float = 2.0) -> None:
    while not capture.is_stopped():
        try:
            frame = capture.get_latest_frame()
            tmp = os.path.join(_CALIB, "live.tmp.png")
            cv2.imwrite(tmp, frame)
            os.replace(tmp, _LIVE)
        except Exception:
            pass
        time.sleep(interval)


def _run_command(line: str) -> bool:
    """Execute one command line. Return False to stop the server."""
    parts = line.split()
    if not parts:
        return True
    cmd = parts[0].lower()
    try:
        if cmd == "click":
            token = parts[1]
            sleep = float(parts[2]) if len(parts) > 2 else 0.2
            repeat = int(parts[3]) if len(parts) > 3 else 1
            _log(f"click {token} sleep={sleep} repeat={repeat}")
            click(token, sleep=sleep, repeat=repeat)
        elif cmd == "hold":
            token = parts[1]
            secs = float(parts[2])
            _log(f"hold {token} {secs}s")
            hold_for(token, secs)
        elif cmd == "sleep":
            secs = float(parts[1])
            _log(f"sleep {secs}s")
            time.sleep(secs)
        elif cmd == "shot":
            label = parts[1] if len(parts) > 1 else "shot"
            _save_shot(label)
        elif cmd == "burst":
            n = int(parts[1])
            interval = float(parts[2])
            label = parts[3] if len(parts) > 3 else "burst"
            _log(f"burst n={n} interval={interval} label={label}")
            for i in range(n):
                _save_shot(f"{label}{i:02d}")
                if i < n - 1:
                    time.sleep(interval)
        elif cmd == "enter":
            # Run the REAL lobby-entry flow from main.py (dynamic menu nav + numeric
            # keypad). Runs in this process, which owns capture + COM6, so the
            # vision detectors (get_mode/get_portal_option) work. Launch this server
            # with PYTHONBREAKPOINT=0 so goto_menu_option's breakpoint() is a no-op.
            code = parts[1] if len(parts) > 1 else "0000"
            _log(f"enter_tera_raid_lobby({code!r}) — running the real main.py flow")
            import main as farmer_main
            farmer_main.enter_tera_raid_lobby(code)
            _log("enter_tera_raid_lobby returned")
        elif cmd == "stop":
            _log("stop requested")
            return False
        else:
            _log(f"UNKNOWN command: {line!r}")
    except Exception:
        _log(f"command failed ({line!r}):\n" + traceback.format_exc())
    return True


def _queue_loop() -> None:
    while not capture.is_stopped():
        files = sorted(f for f in glob.glob(os.path.join(_Q, "*.txt")))
        for path in files:
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue
            _log(f"--- processing {os.path.basename(path)} ---")
            keep_going = True
            for raw in content.splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                keep_going = _run_command(line)
                if not keep_going:
                    break
            try:
                shutil.move(path, os.path.join(_DONE, os.path.basename(path)))
            except Exception:
                try:
                    os.remove(path)
                except Exception:
                    pass
            if not keep_going:
                capture.stop()
                return
        time.sleep(0.15)


def main() -> None:
    port = _load_port()
    _log(f"config com_port = {port!r}")
    try:
        controller.connect(port)
        _log("controller connected")
    except Exception:
        _log("controller.connect FAILED:\n" + traceback.format_exc())

    try:
        capture.start_capture(press_callback=click, debug_regions=[])
        capture.wait_ready()
        _log("capture started and ready")
    except Exception:
        _log("capture FAILED to start:\n" + traceback.format_exc())
        raise

    # Optional Xbox passthrough so the user can also grab manual control.
    try:
        import xbox_passthrough
        xbox_passthrough.run(arduino_port=port)
        _log("xbox passthrough started (user can drive too)")
    except Exception:
        _log("passthrough unavailable (continuing):\n" + traceback.format_exc())

    threading.Thread(target=_heartbeat, daemon=True, name="Heartbeat").start()
    _log("=" * 60)
    _log(f"Drop command files in: {_Q}")
    _log(f"Live frame: {_LIVE}   Log: {_LOG}")
    _log("Ready. Close the 'Capture' window (q) or send 'stop' to exit.")
    _log("=" * 60)

    _queue_loop()
    _log("queue loop ended — exiting")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _log("FATAL:\n" + traceback.format_exc())
        raise
