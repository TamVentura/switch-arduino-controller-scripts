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
import subprocess
import threading
import time

import cv2
import numpy as np
import settings
from colorama import Fore, Style
from recorder import toggle_recording

# ---------------------------------------------------------------------------
# Resolution scaling
# All public coordinate arguments are expressed in 4K (3840×2160).
# They are scaled down to the actual capture resolution at call time.
# ---------------------------------------------------------------------------
_4K_W = 3840
_4K_H = 2160


def _sx(x: int, frame_w: int) -> int:
    return int(x * frame_w / _4K_W)


def _sy(y: int, frame_h: int) -> int:
    return int(y * frame_h / _4K_H)


def get_scale():
    return _4K_H/get_latest_frame().shape[0]

def get_scaled(value:float):
    return value / get_scale()

def get_scaled_int(value:float|int):
    return int(value / get_scale())


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
_latest_frame: None | cv2.typing.MatLike = None
_frame_lock = threading.Lock()
_stop_event = threading.Event()
_ready_event = threading.Event()
_show_debug = False
_keyboard_mode = False
_fs, _fx_off, _fy_off = 1.0, 0, 0

_press_callback = None  # press_callback(arduino_key, sleep) — set by start_capture
_key_callback = None  # key_callback(key, frame)           — set by start_capture
_debug_regions = []  # list of (name, x1, y1, x2, y2, color_bgr)
_capture_thread = None

_selection_start: tuple[int, int] | None = (
    None  # (fx, fy) source-pixel coords of first right-click
)
_selection_current = None  # (fx, fy) source-pixel coords of current mouse pos
_selection_crop = None  # in-memory BGR crop of the last completed selection
_selection_lock = threading.Lock()

_space_press_callbacks: list = []
_space_callbacks_lock = threading.Lock()
_exit_callbacks: list = []
_exit_callbacks_lock = threading.Lock()
_window_toggle_callbacks: list = []
_window_toggle_callbacks_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Arrow key codes for cv2.waitKeyEx on Windows
# ---------------------------------------------------------------------------
_ARROW_UP = 2490368
_ARROW_DOWN = 2621440
_ARROW_LEFT = 2424832
_ARROW_RIGHT = 2555904

# Keyboard → Arduino key map used in keyboard-control mode.
_KB_MAP = {
    _ARROW_UP: "4",  # d-pad up
    _ARROW_DOWN: "2",  # d-pad down
    _ARROW_LEFT: "3",  # d-pad left
    _ARROW_RIGHT: "1",  # d-pad right
    ord("a"): "A",  # A button
    13: "A",  # Enter → A button
    ord("b"): "B",  # B button
    8: "B",  # Backspace → B button
    ord("x"): "X",  # X button
    ord("y"): "Y",  # Y button
    ord("l"): "L",  # L trigger
    ord("r"): "R",  # R trigger
    ord("C"): "C",  # Capture
    ord("h"): "H",  # Home
    ord("+"): "+",
    ord("-"): "-",
    ord("L"): "l",  # ZL
    ord("R"): "r",  # ZR
    # Left stick
    ord("w"): "w",
    ord("e"): "a",
    ord("S"): "s",
    ord("d"): "d",
    ord("z"): "z",
    ord("c"): "c",
    # Right stick
    ord("u"): "u",
    ord("i"): "i",
    ord("Y"): "y",
    ord("H"): "h",
    ord("k"): "k",
    ord("j"): "j",
    ord("n"): "n",
    ord("m"): "m",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _finish_selection(corner2_fx, corner2_fy):
    """Finalise the active selection: print coords, copy to clipboard, hold crop in memory."""
    global _selection_start, _selection_current, _selection_crop
    with _selection_lock:
        start = _selection_start
    if start is None:
        return
    x1, y1 = start
    x2, y2 = corner2_fx, corner2_fy
    # Normalise so top-left < bottom-right
    lx1, lx2 = min(x1, x2), max(x1, x2)
    ly1, ly2 = min(y1, y2), max(y1, y2)
    with _selection_lock:
        _selection_start = None
        _selection_current = None
    text = f"{lx1}, {ly1}, {lx2}, {ly2}"
    print(f"{Fore.GREEN}Selection:{Style.RESET_ALL} {text}  (press f to save crop)")
    try:
        subprocess.run("clip", input=text.encode(), check=True, shell=True)
    except Exception as e:
        print(f"{Fore.RED}Clipboard error:{Style.RESET_ALL} {e}")
    # Capture crop into memory — written to disk only when user presses 's'
    with _frame_lock:
        frame_snap = _latest_frame.copy() if _latest_frame is not None else None
    if frame_snap is not None:
        sx1 = int(lx1 * settings.CAPTURE_WIDTH / _4K_W)
        sy1 = int(ly1 * settings.CAPTURE_HEIGHT / _4K_H)
        sx2 = int(lx2 * settings.CAPTURE_WIDTH / _4K_W)
        sy2 = int(ly2 * settings.CAPTURE_HEIGHT / _4K_H)
        crop = frame_snap[sy1:sy2, sx1:sx2]
        _selection_crop = crop if crop.size > 0 else None


def _on_mouse(event, x, y, flags, param):
    global _selection_start, _selection_current

    # Source-pixel coords (actual frame resolution, for direct frame access)
    src_x = int((x - _fx_off) / _fs)
    src_y = int((y - _fy_off) / _fs)
    # 4K coords (printed and stored so copy-pasted values work in all callers)
    fx = int(src_x * _4K_W / settings.CAPTURE_WIDTH)
    fy = int(src_y * _4K_H / settings.CAPTURE_HEIGHT)

    if event == cv2.EVENT_LBUTTONDOWN:
        with _selection_lock:
            in_selection = _selection_start is not None
        if in_selection:
            _finish_selection(fx, fy)
        else:
            color = ""
            with _frame_lock:
                if _latest_frame is not None:
                    b, g, r = _latest_frame[src_y, src_x]
                    color = f"  {Fore.MAGENTA}#{r:02x}{g:02x}{b:02x}{Style.RESET_ALL}"
            if settings.PRINT_CLICK:
                print(f"{Fore.MAGENTA}Click:{Style.RESET_ALL} ({fx}, {fy}){color}")

    elif event == cv2.EVENT_MOUSEMOVE:
        with _selection_lock:
            if _selection_start is not None:
                _selection_current = (fx, fy)

    elif event == cv2.EVENT_RBUTTONDOWN:
        with _selection_lock:
            in_selection = _selection_start is not None
        if in_selection:
            _finish_selection(fx, fy)
        else:
            with _selection_lock:
                _selection_start = (fx, fy)
                _selection_current = (fx, fy)
            print(
                f"{Fore.CYAN}Selection:{Style.RESET_ALL} first corner ({fx}, {fy}) — right- or left-click to finish"
            )


def _grabber_loop(cap):
    """Dedicated thread: pull frames from the capture device as fast as it
    delivers them and store the latest one.  Decoupled from the display loop
    so a slow imshow / waitKeyEx never causes cap.read() to fall behind."""
    global _latest_frame
    while not _stop_event.is_set():
        try:
            ret, frame = cap.read()
            if not ret:
                _stop_event.set()
                break
            with _frame_lock:
                _latest_frame = frame
        except:
            pass


def _capture_loop():
    global _show_debug, _keyboard_mode, _fs, _fx_off, _fy_off, _selection_start, _selection_current

    import controller as _ctrl

    _ctrl.mark_as_bypass()

    # Pass all format parameters atomically so DSHOW sees them together and
    # doesn't reset the codec when width/height are applied separately.
    cap = cv2.VideoCapture(
        settings.CAPTURE_DEVICE,
        cv2.CAP_DSHOW,
        [
            cv2.CAP_PROP_FOURCC,
            cv2.VideoWriter.fourcc("M", "J", "P", "G"),
            cv2.CAP_PROP_FRAME_WIDTH,
            settings.CAPTURE_WIDTH,
            cv2.CAP_PROP_FRAME_HEIGHT,
            settings.CAPTURE_HEIGHT,
            cv2.CAP_PROP_FPS,
            settings.CAPTURE_FPS,
        ],
    )

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

            win_w, win_h = rect[2], rect[3]
            if win_w <= 0 or win_h <= 0:
                win_w, win_h = 1280, 720

            frame_h, frame_w = frame.shape[:2]
            frame_aspect = frame_w / frame_h
            win_aspect = win_w / win_h

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
                cv2.resize(
                    frame, (new_w, new_h), dst=canvas[fy_off : fy_off + new_h, :]
                )
                fs, fx_off = win_w / frame_w, 0
            else:
                new_h = win_h
                new_w = int(win_h * frame_aspect)
                fx_off = (win_w - new_w) // 2
                canvas[:] = 0
                cv2.resize(
                    frame, (new_w, new_h), dst=canvas[:, fx_off : fx_off + new_w]
                )
                fs, fy_off = win_h / frame_h, 0

            _fs, _fx_off, _fy_off = fs, fx_off, fy_off

            # --- debug overlay ---
            # --- selection overlay ---
            with _selection_lock:
                sel_start = _selection_start
                sel_current = _selection_current

            if sel_start is not None and sel_current is not None:
                # 4K → source pixel → display pixel
                x_scale = fs * settings.CAPTURE_WIDTH / _4K_W
                y_scale = fs * settings.CAPTURE_HEIGHT / _4K_H
                sx1 = int(sel_start[0] * x_scale + fx_off)
                sy1 = int(sel_start[1] * y_scale + fy_off)
                sx2 = int(sel_current[0] * x_scale + fx_off)
                sy2 = int(sel_current[1] * y_scale + fy_off)
                cv2.rectangle(canvas, (sx1, sy1), (sx2, sy2), (0, 255, 255), 2)
                cv2.circle(canvas, (sx1, sy1), 4, (0, 255, 255), -1)

            if _show_debug:
                overlay = canvas.copy()

                # Grid every 100 source pixels
                for x in range(0, frame_w + 1, 100):
                    sx = int(x * fs + fx_off)
                    cv2.line(overlay, (sx, 0), (sx, win_h), (70, 70, 70), 1)
                    cv2.putText(
                        overlay,
                        str(x),
                        (sx + 2, 11),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.3,
                        (160, 160, 160),
                        1,
                    )
                for y in range(0, frame_h + 1, 100):
                    sy = int(y * fs + fy_off)
                    cv2.line(overlay, (0, sy), (win_w, sy), (70, 70, 70), 1)
                    cv2.putText(
                        overlay,
                        str(y),
                        (2, sy + 10),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.3,
                        (160, 160, 160),
                        1,
                    )

                # Caller-supplied named regions (coords in 4K → source → display)
                _dr_xs = fs * settings.CAPTURE_WIDTH / _4K_W
                _dr_ys = fs * settings.CAPTURE_HEIGHT / _4K_H
                for name, x1, y1, x2, y2, color in _debug_regions:
                    rx1 = int(x1 * _dr_xs + fx_off)
                    ry1 = int(y1 * _dr_ys + fy_off)
                    rx2 = int(x2 * _dr_xs + fx_off)
                    ry2 = int(y2 * _dr_ys + fy_off)
                    cv2.rectangle(overlay, (rx1, ry1), (rx2, ry2), color, 2)
                    cv2.putText(
                        overlay,
                        name,
                        (rx1, ry1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        color,
                        1,
                    )

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
            elif key == ord("p"):
                settings.PRINT_SCREENSHOT = not settings.PRINT_SCREENSHOT
            elif key == ord("t"):
                toggle_recording()
            elif key == ord(" "):  # Space — notify registered callbacks
                with _space_callbacks_lock:
                    cbs = list(_space_press_callbacks)
                for cb in cbs:
                    try:
                        cb()
                    except Exception:
                        pass
            elif key == ord("v"):  # v — toggle controller UI window
                with _window_toggle_callbacks_lock:
                    cbs = list(_window_toggle_callbacks)
                for cb in cbs:
                    try:
                        cb()
                    except Exception:
                        pass
            elif key == 9:  # Tab — toggle keyboard-control mode
                _keyboard_mode = not _keyboard_mode
                if settings.VERBOSE:
                    print(
                        f"{Fore.CYAN}Keyboard mode:{Style.RESET_ALL} "
                        f"{'ON  (Tab to exit)' if _keyboard_mode else 'OFF'}"
                    )
            elif _keyboard_mode:
                if key in _KB_MAP and _press_callback is not None:
                    _press_callback(_KB_MAP[key], 0.15)
            elif key == ord("s"):
                os.makedirs("screenshots", exist_ok=True)
                filename = f"screenshots/{time.strftime('%Y%m%d_%H%M%S')}.png"
                cv2.imwrite(filename, frame)
                if settings.PRINT_SCREENSHOT:
                    print(f"{Fore.GREEN}Screenshot:{Style.RESET_ALL} {filename}")
            elif key == ord("f"):
                print("HERE")
                if _selection_crop is not None:
                    os.makedirs("screenshots", exist_ok=True)
                    filename = f"screenshots/crop_{time.strftime('%Y%m%d_%H%M%S')}.png"
                    cv2.imwrite(filename, _selection_crop)
                    print(f"{Fore.GREEN}Crop saved:{Style.RESET_ALL} {filename}")
                else:
                    print(f"{Fore.YELLOW}No selection crop to save.{Style.RESET_ALL}")
            elif key == ord("g"):
                _show_debug = not _show_debug
            elif _key_callback and key != -1:
                _key_callback(key, frame)
        except Exception as e:
            print(f"{Fore.RED}Error in capture loop:{Style.RESET_ALL} {e}")
            _stop_event.set()
        _ready_event.set()

    _stop_event.set()
    # grabber.join(timeout=1)
    cap.release()
    cv2.destroyAllWindows()

    with _exit_callbacks_lock:
        cbs = list(_exit_callbacks)
    for cb in cbs:
        try:
            cb()
        except Exception:
            pass


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
    _debug_regions = debug_regions or []
    _key_callback = key_callback

    _stop_event.clear()
    _capture_thread = threading.Thread(target=_capture_loop, daemon=True)
    _capture_thread.start()
    return _capture_thread


def stop():
    """Signal the capture loop to stop and block until it has cleaned up
    (cap.release, destroyAllWindows). Safe to call from any thread."""
    _stop_event.set()
    if _capture_thread is not None:
        _capture_thread.join(timeout=0)


def register_space_callback(cb) -> None:
    """Register a zero-argument callable to be called when Space is pressed."""
    with _space_callbacks_lock:
        _space_press_callbacks.append(cb)


def unregister_space_callback(cb) -> None:
    """Remove a previously registered space callback (no-op if not found)."""
    with _space_callbacks_lock:
        try:
            _space_press_callbacks.remove(cb)
        except ValueError:
            pass


def register_exit_callback(cb) -> None:
    """Register a zero-argument callable to be called when the capture loop exits."""
    with _exit_callbacks_lock:
        _exit_callbacks.append(cb)


def unregister_exit_callback(cb) -> None:
    """Remove a previously registered exit callback (no-op if not found)."""
    with _exit_callbacks_lock:
        try:
            _exit_callbacks.remove(cb)
        except ValueError:
            pass


def register_window_toggle_callback(cb) -> None:
    """Register a zero-argument callable to be called when 'v' is pressed (toggle UI)."""
    with _window_toggle_callbacks_lock:
        _window_toggle_callbacks.append(cb)


def unregister_window_toggle_callback(cb) -> None:
    """Remove a previously registered window-toggle callback (no-op if not found)."""
    with _window_toggle_callbacks_lock:
        try:
            _window_toggle_callbacks.remove(cb)
        except ValueError:
            pass


def get_latest_frame():
    """Return a copy of the most recent captured frame, or None."""
    with _frame_lock:
        if _latest_frame is None:
            raise Exception("No frame available yet.")
        return _latest_frame.copy()


def wait_ready():
    """Block until the first frame has been captured and displayed."""
    _ready_event.wait()


def is_stopped():
    """Return True if the capture loop has exited (e.g. user pressed 'q')."""
    return _stop_event.is_set()


def wait_for_pixel(x, y, rgb_hex, tolerance=10, interval=0.1):
    """Block until the pixel at (x, y) matches rgb_hex (e.g. '#0254a0').
    Checks every `interval` seconds. Returns early if the capture stops."""
    frame_h, frame_w = get_latest_frame().shape[:2]
    x, y = _sx(x, frame_w), _sy(y, frame_h)
    r = int(rgb_hex[1:3], 16)
    g = int(rgb_hex[3:5], 16)
    b = int(rgb_hex[5:7], 16)
    while not _stop_event.is_set():
        frame = get_latest_frame()
        if frame is not None:
            pb, pg, pr = frame[y, x]
            if (
                abs(int(pr) - r) <= tolerance
                and abs(int(pg) - g) <= tolerance
                and abs(int(pb) - b) <= tolerance
            ):
                return True
        time.sleep(interval)
    return False


def get_frame_region(x1, y1, x2, y2, frame=None):
    if frame is None:
        frame = get_latest_frame()
    frame_h, frame_w = frame.shape[:2]
    return frame[
        _sy(y1, frame_h) : _sy(y2, frame_h), _sx(x1, frame_w) : _sx(x2, frame_w)
    ]


def get_pixel_color(x, y, frame=None):
    """Return the color of the pixel at (x, y) as a hex string (e.g. '#0254a0')."""
    if frame is None:
        frame = get_latest_frame()
    frame_h, frame_w = frame.shape[:2]
    if frame is not None:
        b, g, r = frame[_sy(y, frame_h), _sx(x, frame_w)]
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def is_pixel_color(x, y, hex_color, tolerance=10, *, frame=None):
    """Return True if the pixel at (x, y) is within `tolerance` of `hex_color` on each channel.

    If `frame` is omitted the latest captured frame is used automatically.
    """
    if frame is None:
        frame = get_latest_frame()
    frame_h, frame_w = frame.shape[:2]
    b, g, r = frame[_sy(y, frame_h), _sx(x, frame_w)]
    tr = int(hex_color[1:3], 16)
    tg = int(hex_color[3:5], 16)
    tb = int(hex_color[5:7], 16)
    return (
        abs(int(r) - tr) <= tolerance
        and abs(int(g) - tg) <= tolerance
        and abs(int(b) - tb) <= tolerance
    )


def is_pixel_tolerance(pixel_hex_color, hex_color, tolerance=10):
    r = int(pixel_hex_color[1:3], 16)
    g = int(pixel_hex_color[3:5], 16)
    b = int(pixel_hex_color[5:7], 16)
    tr = int(hex_color[1:3], 16)
    tg = int(hex_color[3:5], 16)
    tb = int(hex_color[5:7], 16)
    return (
        abs(int(r) - tr) <= tolerance
        and abs(int(g) - tg) <= tolerance
        and abs(int(b) - tb) <= tolerance
    )


def _region_has_hex_color(x1, y1, x2, y2, hex_color, tolerance=10, frame=None):
    if frame is None:
        frame = get_latest_frame()
    """Return True if any pixel in the region is within `tolerance` of `hex_color`."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    frame_h, frame_w = frame.shape[:2]
    region = frame[
        _sy(y1, frame_h) : _sy(y2, frame_h), _sx(x1, frame_w) : _sx(x2, frame_w)
    ].astype(np.int16)
    diff = np.abs(region - np.array([b, g, r], dtype=np.int16))
    return bool(np.any(np.all(diff <= tolerance, axis=2)))


def region_is_mostly_color(
    x1, y1, x2, y2, hex_color, threshold=0.8, tolerance=10.0, frame=None
):
    if frame is None:
        frame = get_latest_frame()
    """Return True if most pixels in the region are within `tolerance` of `hex_color`."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    region = get_frame_region(x1, y1, x2, y2)
    region_color = region.astype(np.int16)
    diff = np.abs(region_color - np.array([b, g, r], dtype=np.int16))
    pixels_meeting_threshold = np.sum(np.all(diff <= tolerance, axis=2), axis=(0, 1))
    return pixels_meeting_threshold / (region.shape[0] * region.shape[1]) >= threshold


def region_color_ratio(x1, y1, x2, y2, hex_color, tolerance=10.0, frame=None) -> float:
    """Return the fraction of pixels in the region within `tolerance` of `hex_color`."""
    if frame is None:
        frame = get_latest_frame()
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    frame_h, frame_w = frame.shape[:2]
    sx1, sy1, sx2, sy2 = (
        _sx(x1, frame_w),
        _sy(y1, frame_h),
        _sx(x2, frame_w),
        _sy(y2, frame_h),
    )
    region = frame[sy1:sy2, sx1:sx2].astype(np.int16)
    diff = np.abs(region - np.array([b, g, r], dtype=np.int16))
    return float(np.sum(np.all(diff <= tolerance, axis=2))) / ((sx2 - sx1) * (sy2 - sy1))


def tolerance_for_region(x1, y1, x2, y2, hex_color, threshold=0.8):
    """Return the minimum tolerance so that `threshold` fraction of pixels in the
    region are within that tolerance of `hex_color` (Chebyshev distance per pixel)."""
    frame = get_latest_frame()
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    frame_h, frame_w = frame.shape[:2]
    sx1, sy1, sx2, sy2 = (
        _sx(x1, frame_w),
        _sy(y1, frame_h),
        _sx(x2, frame_w),
        _sy(y2, frame_h),
    )
    region = frame[sy1:sy2, sx1:sx2].astype(np.int16)
    diff = np.abs(region - np.array([b, g, r], dtype=np.int16))
    per_pixel_dist = np.max(diff, axis=2).flatten()
    return float(np.quantile(per_pixel_dist, threshold))




def wait_for_pixel_color(color, x, y, timeout, interval=0.05):
    """Poll pixel (x, y) until the color is detected or `timeout` seconds elapse.

    `color` can be a color name (e.g. "red") or a hex string (e.g. "#12282e").
    Returns True if the color appeared, False if the timeout was reached.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        frame = get_latest_frame()
        if frame is not None:
            if color.startswith("#"):
                if is_pixel_color(x, y, color, frame=frame):
                    return True
            else:
                raise NotImplementedError(
                    "Only hex colors are supported in wait_for_pixel_color"
                )
        time.sleep(interval)
    return False


def wait_for_color(color, x1, y1, x2, y2, timeout, interval=0.05):
    """Poll region (x1,y1,x2,y2) until the color is detected or `timeout` seconds elapse.

    `color` can be a color name (e.g. "red") or a hex string (e.g. "#12282e").
    Returns True if the color appeared, False if the timeout was reached.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        frame = get_latest_frame()
        if frame is not None:
            if color.startswith("#"):
                if _region_has_hex_color(x1, y1, x2, y2, color):
                    return True
            else:
                raise NotImplementedError(
                    "Only hex colors are supported in wait_for_color"
                )
        time.sleep(interval)
    return False
