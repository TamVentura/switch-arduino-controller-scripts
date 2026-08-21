import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import time

import numpy as np

from switch import *

_DEBUG_REGIONS = [
    ("emotion", 550, 159, 609, 217, (0, 255, 0)),
]

item_text_region = ((2148, 456), (2678, 632))

text_box_region = ((956, 1706), (1036, 1862))


def main():
    time.sleep(2)
    while True:
        result = checkItem()
        if result is None:
            save_game()
            close_game()
            change_date()
            open_game()
        elif result == True:
            participate_in_auction()
            save_game()
            close_game()
            change_date()
            open_game()
        else:
            restart_game()


def save_game():
    click("X", 1)
    click("R", 3)
    click("A", 8)


def change_date():
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
    click("4")
    hold_for("1", 2, 0.1)
    click("A")
    click("H")
    click("A")


def close_game():
    click("H")
    click("X")
    click("A")


def open_game():
    click("H")
    click("A", 1)
    click("A", 20)
    click("A", 13)


def participate_in_auction():
    start_auction()
    auction_ended = False
    while capture.wait_for_color(
        "#f5f3f5", *text_box_region[0], *text_box_region[1], timeout=10
    ):
        click("A", 1, repeat=3)


def start_auction():
    click("A", 1, repeat=3)


def checkItem():
    frame = capture.get_latest_frame()
    if frame is None:
        return False
    text = ocr.read_field(
        frame,
        item_text_region[0][0],
        item_text_region[0][1],
        item_text_region[1][0],
        item_text_region[1][1],
        config="--oem 1 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 ",
        debug_name="teste",
    )
    if text is None or text.lower().find("price") == -1:
        return None

    print(f"{Fore.MAGENTA}Detected item text:{Style.RESET_ALL} '{text}'")
    return text.lower().find("feather") != -1


if __name__ == "__main__":
    launch(main, debug_regions=_DEBUG_REGIONS)

