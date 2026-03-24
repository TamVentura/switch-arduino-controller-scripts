"""
capture.py — screen-capture pseudo-library.

Manages a background capture thread that:
  - displays a resizable preview window with letterbox/pillarbox scaling
  - tracks mouse clicks and reports source-pixel coordinates
  - supports a keyboard-control mode (Tab to toggle) that forwards mapped
    keys to an external press_callback
  - overlays a debug grid + caller-supplied named regions (g to toggle)
  - saves screenshots on 's'
  - calls an optional key_callback(key, frame) for any other key

Public API
----------
start_capture(press_callback, debug_regions, key_callback) -> thread
get_latest_frame() -> ndarray | None
wait_ready()
is_stopped() -> bool
wait_for_pixel(x, y, rgb_hex, tolerance, interval)
"""

import os
import threading
import time

import cv2
import numpy as np
import settings
from colorama import Fore, Style
from ffpyplayer.player import MediaPlayer

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
_latest_frame = None
_frame_lock = threading.Lock()
_stop_event = threading.Event()
_ready_event = threading.Event()
_show_debug = False
_keyboard_mode = True
_fs, _fx_off, _fy_off = 1.0, 0, 0

_press_callback = None   # press_callback(arduino_key, sleep) — set by start_capture
_key_callback = None     # key_callback(key, frame)           — set by start_capture
_debug_regions = []      # list of (name, x1, y1, x2, y2, color_bgr)
_capture_thread = None

# ---------------------------------------------------------------------------
# Arrow key codes for cv2.waitKeyEx on Windows
# ---------------------------------------------------------------------------
_ARROW_UP    = 2490368
_ARROW_DOWN  = 2621440
_ARROW_LEFT  = 2424832
_ARROW_RIGHT = 2555904

# Keyboard → Arduino key map used in keyboard-control mode.
_KB_MAP = {
    _ARROW_UP:    "4",   # d-pad up
    _ARROW_DOWN:  "2",   # d-pad down
    _ARROW_LEFT:  "3",   # d-pad left
    _ARROW_RIGHT: "1",   # d-pad right
    ord("a"):     "A",   # A button
    13:           "A",   # Enter → A button
    ord("b"):     "B",   # B button
    8:            "B",   # Backspace → B button
    ord("x"):     "X",   # X button
    ord("y"):     "Y",   # Y button
    ord("l"):     "L",   # L trigger
    ord("r"):     "R",   # R trigger
    ord("C"):     "C",   # Capture
    ord("h"):     "H",   # Home
    ord("+"):     "+",
    ord("-"):     "-",
    ord("L"):     "l",   # ZL
    ord("R"):     "r",   # ZR
    # Left stick
    ord("w"):     "w",
    ord("e"):     "e",
    ord("S"):     "s",
    ord("d"):     "d",
    ord("z"):     "z",
    ord("c"):     "c",
    # Right stick
    ord("u"):     "u",
    ord("i"):     "i",
    ord("Y"):     "y",
    ord("H"):     "h",
    ord("k"):     "k",
    ord("j"):     "j",
    ord("n"):     "n",
    ord("m"):     "m",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _on_mouse(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        fx = int((x - _fx_off) / _fs)
        fy = int((y - _fy_off) / _fs)
        color = ""
        with _frame_lock:
            if _latest_frame is not None:
                b, g, r = _latest_frame[fy, fx]
                color = f"  {Fore.MAGENTA}#{r:02x}{g:02x}{b:02x}{Style.RESET_ALL}"
        if settings.PRINT_CLICK:
            print(f"{Fore.MAGENTA}Click:{Style.RESET_ALL} ({fx}, {fy}){color}")


def _grabber_loop(cap):
    """Dedicated thread: pull frames from the capture device as fast as it
    delivers them and store the latest one.  Decoupled from the display loop
    so a slow imshow / waitKeyEx never causes cap.read() to fall behind."""
    global _latest_frame
    while not _stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            _stop_event.set()
            break
        with _frame_lock:
            _latest_frame = frame


def _capture_loop():
    global _show_debug, _keyboard_mode, _fs, _fx_off, _fy_off

    # Pass all format parameters atomically so DSHOW sees them together and
    # doesn't reset the codec when width/height are applied separately.
    cap = cv2.VideoCapture(settings.CAPTURE_DEVICE, cv2.CAP_DSHOW, [
        cv2.CAP_PROP_FOURCC,       cv2.VideoWriter.fourcc('M', 'J', 'P', 'G'),
        cv2.CAP_PROP_FRAME_WIDTH,  settings.CAPTURE_WIDTH,
        cv2.CAP_PROP_FRAME_HEIGHT, settings.CAPTURE_HEIGHT,
        cv2.CAP_PROP_FPS,          settings.CAPTURE_FPS,
    ])

    player = MediaPlayer(':audio="USB Video"')


    # Report what the driver actually negotiated
    fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
    fourcc_str = "".join(chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4))
    print(
        f"[capture] negotiated: "
        f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} "
        f"@ {cap.get(cv2.CAP_PROP_FPS):.0f} fps  fourcc={fourcc_str!r}"
    )

    grabber = threading.Thread(target=_grabber_loop, args=(cap,), daemon=True)
    grabber.start()

    window_name = "Capture"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 1280, 720)
    cv2.setMouseCallback(window_name, _on_mouse)

    canvas = None

    while not _stop_event.is_set():
        try:
            with _frame_lock:
                frame = _latest_frame
            if frame is None:
                cv2.waitKeyEx(1)
                continue

            # --- compute letterbox / pillarbox layout ---
            rect = cv2.getWindowImageRect(window_name)
            audio_frame, val = player.get_frame()
            
            win_w, win_h = rect[2], rect[3]
            if win_w <= 0 or win_h <= 0:
                win_w, win_h = 1280, 720

            frame_h, frame_w = frame.shape[:2]
            frame_aspect = frame_w / frame_h
            win_aspect   = win_w  / win_h

            # Re-use the canvas buffer when the window size hasn't changed
            if canvas is None or canvas.shape[:2] != (win_h, win_w):
                canvas = np.zeros((win_h, win_w, 3), dtype=np.uint8)

            if abs(frame_aspect - win_aspect) < 0.001:
                cv2.resize(frame, (win_w, win_h), dst=canvas)
                fs, fx_off, fy_off = win_w / frame_w, 0, 0
            elif frame_aspect > win_aspect:
                new_w = win_w
                new_h = int(win_w / frame_aspect)
                fy_off = (win_h - new_h) // 2
                canvas[:] = 0
                cv2.resize(frame, (new_w, new_h), dst=canvas[fy_off : fy_off + new_h, :])
                fs, fx_off = win_w / frame_w, 0
            else:
                new_h = win_h
                new_w = int(win_h * frame_aspect)
                fx_off = (win_w - new_w) // 2
                canvas[:] = 0
                cv2.resize(frame, (new_w, new_h), dst=canvas[:, fx_off : fx_off + new_w])
                fs, fy_off = win_h / frame_h, 0

            _fs, _fx_off, _fy_off = fs, fx_off, fy_off

            # --- debug overlay ---
            if _show_debug:
                overlay = canvas.copy()

                # Grid every 100 source pixels
                for x in range(0, frame_w + 1, 100):
                    sx = int(x * fs + fx_off)
                    cv2.line(overlay, (sx, 0), (sx, win_h), (70, 70, 70), 1)
                    cv2.putText(overlay, str(x), (sx + 2, 11),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.3, (160, 160, 160), 1)
                for y in range(0, frame_h + 1, 100):
                    sy = int(y * fs + fy_off)
                    cv2.line(overlay, (0, sy), (win_w, sy), (70, 70, 70), 1)
                    cv2.putText(overlay, str(y), (2, sy + 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.3, (160, 160, 160), 1)

                # Caller-supplied named regions
                for name, x1, y1, x2, y2, color in _debug_regions:
                    rx1 = int(x1 * fs + fx_off)
                    ry1 = int(y1 * fs + fy_off)
                    rx2 = int(x2 * fs + fx_off)
                    ry2 = int(y2 * fs + fy_off)
                    cv2.rectangle(overlay, (rx1, ry1), (rx2, ry2), color, 2)
                    cv2.putText(overlay, name, (rx1, ry1 - 4),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

                cv2.imshow(window_name, overlay)
            else:
                cv2.imshow(window_name, canvas)
            key = cv2.waitKeyEx(10)  # ~60 fps cap
            if key == ord("q"):
                _stop_event.set()
            elif key == ord("k"):
                settings.PRINT_KEY = not settings.PRINT_KEY
            elif key == ord("c"):
                settings.PRINT_CLICK = not settings.PRINT_CLICK
            elif key == ord("s"):
                settings.PRINT_SCREENSHOT = not settings.PRINT_SCREENSHOT
            elif key == 9:  # Tab — toggle keyboard-control mode
                _keyboard_mode = not _keyboard_mode
                if settings.VERBOSE:
                    print(f"{Fore.CYAN}Keyboard mode:{Style.RESET_ALL} "
                        f"{'ON  (Tab to exit)' if _keyboard_mode else 'OFF'}")
            elif _keyboard_mode:
                if key in _KB_MAP and _press_callback:
                    threading.Thread(
                        target=_press_callback, args=(_KB_MAP[key], 0.15), daemon=True
                    ).start()
            elif key == ord("s"):
                os.makedirs("screenshots", exist_ok=True)
                filename = f"screenshots/{time.strftime('%Y%m%d_%H%M%S')}.png"
                cv2.imwrite(filename, frame)
                if settings.PRINT_SCREENSHOT:
                    print(f"{Fore.GREEN}Screenshot:{Style.RESET_ALL} {filename}")
            elif key == ord("g"):
                _show_debug = not _show_debug
            elif _key_callback and key != -1:
                _key_callback(key, frame)
        except Exception as e:
            print(f"{Fore.RED}Error in capture loop:{Style.RESET_ALL} {e}")
            _stop_event.set()
        _ready_event.set()
    
    _stop_event.set()
    #grabber.join(timeout=1)
    cap.release()
    cv2.destroyAllWindows()

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_capture(press_callback=None, debug_regions=None, key_callback=None):
    """Start the background capture thread.

    press_callback  -- called as press_callback(arduino_key, sleep) when a
                       mapped key is pressed in keyboard-control mode.
    debug_regions   -- list of (name, x1, y1, x2, y2, color_bgr) tuples
                       drawn as labelled rectangles when the debug overlay
                       is active (toggle with 'g').
    key_callback    -- called as key_callback(key, frame) for any key not
                       handled by the capture lib itself.

    Returns the background thread.
    """
    global _press_callback, _debug_regions, _key_callback, _capture_thread
    _press_callback = press_callback
    _debug_regions  = debug_regions or []
    _key_callback   = key_callback

    _stop_event.clear()
    _capture_thread = threading.Thread(target=_capture_loop, daemon=True)
    _capture_thread.start()
    return _capture_thread


def stop():
    """Signal the capture loop to stop and block until it has cleaned up
    (cap.release, destroyAllWindows). Safe to call from any thread."""
    _stop_event.set()
    if _capture_thread is not None:
        _capture_thread.join()


def get_latest_frame():
    """Return a copy of the most recent captured frame, or None."""
    with _frame_lock:
        return _latest_frame.copy() if _latest_frame is not None else None


def wait_ready():
    """Block until the first frame has been captured and displayed."""
    _ready_event.wait()


def is_stopped():
    """Return True if the capture loop has exited (e.g. user pressed 'q')."""
    return _stop_event.is_set()


def wait_for_pixel(x, y, rgb_hex, tolerance=10, interval=0.1):
    """Block until the pixel at (x, y) matches rgb_hex (e.g. '#0254a0').
    Checks every `interval` seconds. Returns early if the capture stops."""
    r = int(rgb_hex[1:3], 16)
    g = int(rgb_hex[3:5], 16)
    b = int(rgb_hex[5:7], 16)
    while not _stop_event.is_set():
        frame = get_latest_frame()
        if frame is not None:
            pb, pg, pr = frame[y, x]
            if (abs(int(pr) - r) <= tolerance
                    and abs(int(pg) - g) <= tolerance
                    and abs(int(pb) - b) <= tolerance):
                return
        time.sleep(interval)
