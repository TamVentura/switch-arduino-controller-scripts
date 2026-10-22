from pynput import keyboard
import pygetwindow as gw

TARGET_WINDOW_TITLE = "Notepad"  # Change to your target window title/name

pressed_keys = set()

def is_target_focused():
    try:
        active_window = gw.getActiveWindow()
        if active_window and TARGET_WINDOW_TITLE in active_window.title:
            return True
    except Exception:
        pass
    return False

def on_press(key):
    if not is_target_focused():
        return  # Ignore key if window is not focused
    
    pressed_keys.add(key)

def on_release(key):
    if not is_target_focused():
        return
    
    pressed_keys.discard(key)

# Start the global listener
with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    listener.join()