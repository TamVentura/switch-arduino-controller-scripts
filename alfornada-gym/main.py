import os
import sys
import time

import numpy as np
from colorama import Fore, Style, init

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "utils"))
import capture
import controller

press = controller.press
hold  = controller.hold


_DEBUG_REGIONS = [
    ("emotion",         550, 159,  609, 217,  (0,   255,   0)),
]

item_region= ((500, 798), (533, 818))
text_region = ((478, 853), (518, 931))

colors = [
   "#df4e4a", #Red
   "#ffcf00", #Yellow
   "#2181e0", #Blue
   "#7fcc39", #Green
   "#2a4658"  #Dark Blue
]
item_color = "#12282e"
text_color = "#f5f3f5"

_color_names = ["red", "yellow", "blue", "green", "dark_blue"]


# Preparse colors as BGR arrays for distance comparison
_color_bgr = np.array([
    [int(c[5:7], 16), int(c[3:5], 16), int(c[1:3], 16)]
    for c in colors
], dtype=np.float32)

def _detect_color_in_region(frame, x1, y1, x2, y2):
    """Return the dominant color name in the given region, or 'none'."""
    region = frame[y1:y2, x1:x2].astype(np.float32)
    mask = ~np.all(region > 200, axis=2)
    if not np.any(mask):
        return "none"
    pixels = region[mask]
    diffs = pixels[:, np.newaxis, :] - _color_bgr[np.newaxis, :, :]
    distances = np.sqrt(np.sum(diffs ** 2, axis=2))
    close = distances < 1
    counts = np.sum(close, axis=0)
    best = np.argmax(counts)
    return _color_names[best] if counts[best] > 0 else "none"


def checkColor():
    frame = capture.get_latest_frame()
    if frame is None:
        return "none"
    return _detect_color_in_region(frame, 550, 159, 609, 217)


def _region_has_hex_color(frame, x1, y1, x2, y2, hex_color, tolerance=10):
    """Return True if any pixel in the region is within `tolerance` of `hex_color`."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    region = frame[y1:y2, x1:x2].astype(np.int16)
    diff = np.abs(region - np.array([b, g, r], dtype=np.int16))
    return bool(np.any(np.all(diff <= tolerance, axis=2)))


def wait_for_color(color, x1, y1, x2, y2, timeout, interval=0.05):
    """Poll region (x1,y1,x2,y2) until the color is detected or `timeout` seconds elapse.

    `color` can be a color name (e.g. "red") or a hex string (e.g. "#12282e").
    Returns True if the color appeared, False if the timeout was reached.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        frame = capture.get_latest_frame()
        if frame is not None:
            if color.startswith("#"):
                if _region_has_hex_color(frame, x1, y1, x2, y2, color):
                    return True
            elif _detect_color_in_region(frame, x1, y1, x2, y2) == color:
                return True
        time.sleep(interval)
    return False

def play_game(game_count = 151):
    old_value = "none"
    count = 0
    while count < game_count:
        value = checkColor()
        if value == "dark_blue":
            press("A", 5)
            continue
        #print(f"Detected color: {value}")
        if value != old_value and value != "none":
            _color_fore = {"red": Fore.RED, "yellow": Fore.YELLOW, "blue": Fore.BLUE, "green": Fore.GREEN}
            #if value != "none": print(f"{Fore.LIGHTCYAN_EX}Detected new color:{Style.RESET_ALL} {_color_fore.get(value, '')}{value}{Style.RESET_ALL}")
            print(f"{Fore.LIGHTMAGENTA_EX}Total count:{Style.RESET_ALL} {count+1} - Detected color: {_color_fore.get(value, '')}{value}{Style.RESET_ALL}")
            if(value == "red"):
                press("X")
            elif(value == "yellow"):
                press("A")
            elif(value == "blue"):
                press("B")
            elif(value == "green"):
                press("Y")
            count += 1
            if(count >= game_count):
                while checkColor() != "none":
                    continue
                print(f"{Fore.LIGHTGREEN_EX}Finished!{Style.RESET_ALL} Detected {game_count} colors.")
                break

        old_value = value

        time.sleep(0.05)
    time.sleep(7)

def setup_game():
    press("A", 2.5)
    press("A", 0.7)
    press("A", 1.2)
    press("A", 0.7)
    press("2")
    press("A", 0.7)
    press("A", 1.5)
    press("A", 2)
    press("A", 2)

def end_game():
    complete = False
    while not complete:
        complete = True
        while wait_for_color(text_color, *text_region[0], *text_region[1], timeout=0.5):
            press("A", 0.5)
            complete = False

        while wait_for_color(item_color, *item_region[0], *item_region[1], timeout=0.5):
            press("A", 0.5)
            complete = False

    print(f"{Fore.LIGHTRED_EX}Ready for next game.{Style.RESET_ALL}")

def run_instance():
    print(f"{Fore.LIGHTBLUE_EX}Starting new game...{Style.RESET_ALL}")
    _start = time.time()
    setup_game()
    play_game()
    end_game()
    elapsed = time.time() - _start
    print(f"{Fore.LIGHTBLUE_EX}Instance took:{Style.RESET_ALL} {elapsed:.1f}s")

def main():
    while True:
        games_input = input("Number of game to play (0 to infinite and -1 to quit): ").strip()
        if games_input == "-1":
            print("Exiting...")
            return
        try:
            games = int(games_input)
            if games < 0:
                print("Please enter a non-negative integer or -1 to quit.")
                continue
        except ValueError:
            print("Invalid input. Please enter a non-negative integer or -1 to quit.")
            continue
        if games == 0:
            print("Playing infinite games. Press Ctrl+C to stop.")
            i = 0
            while True:
                run_instance()
                print(f"{Fore.LIGHTBLUE_EX}Game {i+1} completed.{Style.RESET_ALL}")
                i += 1
        else:
            for i in range(games):
                run_instance()
                print(f"{Fore.LIGHTBLUE_EX}Game {i+1} completed.{Style.RESET_ALL}")

if __name__ == "__main__":
    controller.connect("COM6")
    capture.start_capture(
        press_callback=controller.press,
        debug_regions=_DEBUG_REGIONS,
    )
    capture.wait_ready()
    try:
      main()    
    except KeyboardInterrupt:
        pass
    finally:
        capture.stop()
