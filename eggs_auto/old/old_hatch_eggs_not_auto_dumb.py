from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import time
from dataclasses import dataclass

sys.path.insert(1, os.path.join(os.path.dirname(__file__), "..", "utils"))
import template_matcher

from switch import (Fore, NotificationResult, Style, TelegramAction,
                    TelegramNotifier, capture, click, connect, controller,
                    hold, hold_for, launch, notifier, ocr, release,
                    set_left_stick, set_right_stick, settings)

shinies: list[ShinyLocation] = []


cur_box = 1
cur_col = 1


@dataclass(frozen=True)
class ShinyLocation:
    box: int
    col: int
    row: int


first = True


def start():
    result = notifier.send_and_wait(
        text="Começar?",
        actions=[
            TelegramAction("Sim", "approve"),
            TelegramAction("Não", "reject"),
        ],
        console_prompt="Press Enter locally to continue...\n",
    )
    print(result)
    run(0, 0)


def run(max_box, max_col) -> list[ShinyLocation]:
    notifier.register_command("status", get_status(max_box, max_col))
    shiny_locations: list[ShinyLocation] = []
    # Detect if there are any eggs on this box
    eggs = _get_eggs_locations()
    while _box_has_eggs():
        # Detect if the party has eggs
        eggs = []

        # Detect what columns have eggs
        for cur_col in _get_cols_with_eggs():
            shiny_locations.extend(run_collumn(cur_col))
        print(f"Finished box {cur_box}")
        start_col = 1
        click("R")
        click("3", repeat=6)
        _get_eggs_locations()

    notifier.send_message("Finalizado")
    return shiny_locations


def get_status(max_box, max_col):
    notifier.send_message(
        f"Shinies: {len(shinies)}, Box: {cur_box}/{max_box}, Col: {cur_col}/{max_col}"
    )


def _get_eggs_locations():
    return template_matcher.find_all(
        capture.get_latest_frame(),  # type: ignore
        "reference_image.png",
    )


def _box_has_eggs(eggs):
    return len(eggs) > 0


def _get_cols_with_eggs():
    return [
        i
        for i in range(1, 7)
        if capture._region_has_hex_color(1707, 115, 1707, 116, "#feffff")
    ]


def run_collumn(cur_col) -> list[ShinyLocation]:
    global first
    if not first:
        click("1")
        click("-")
        click("2", repeat=4)
        click("A")

        click("3", repeat=cur_col)
        click("2")
        click("A")
        click("B", 2, repeat=2)

    hatch_eggs(last_row if cur_box == last_box and cur_col == last_col else 5)

    set_left_stick(0, 0)
    set_right_stick(0, 0)
    time.sleep(5)
    click("X", 1)
    click("A", 1)
    click("3")
    click("2")
    click("-")
    for i in range(0, last_row if cur_box == last_box and cur_col == last_col else 5):
        shiny = capture._region_has_hex_color(
            capture.get_latest_frame(), 1707, 115, 1707, 116, "#feffff"
        )
        if shiny:
            shinies.append(ShinyLocation(cur_box, cur_col, i + 1))
            frame = capture.get_latest_frame()[1249:0, 1918:996]  # type: ignore
            notifier.send_photo(
                frame=frame,
                caption=f"Found shiny on {cur_box}, {cur_col}, {i + 1}",
            )
            print("shiny")
        click("2")
    click("A")
    click("4")
    click("1", repeat=cur_col)
    click("A")
    first = False
    print(f"Finished box {cur_box}, col {cur_col}")
    if (cur_col == 6 and cur_box != last_box) or (
        cur_box == last_box and cur_col == last_col
    ):
        notifier.send_message(f"Finished box {cur_box}")
    return shinies


def hatch_eggs(nEggs):
    for i in range(0, nEggs):
        hatch_egg()


def hatch_egg():
    set_right_stick(1, 0)
    set_left_stick(1, 0)
    time.sleep(0.8)
    hold("{")

    while not capture.wait_for_color("#031b24", 975, 897, 976, 899, 5):
        if random.random() < 0.01:
            click("H", 1, repeat=2)
        set_right_stick(1 - random.random() * 0.1, random.random() * 0.2 - 0.1)
        set_left_stick(1 - random.random() * 0.1, random.random() * 0.2 - 0.1)
        continue
    release("{")
    while capture.wait_for_color("#031b24", 975, 897, 976, 899, 1):
        click("A", 0.3)
    while not capture.wait_for_color("#031b24", 975, 897, 976, 899, 1):
        continue
    time.sleep(2)
    click("A", 2, repeat=2)
    time.sleep(3)


if __name__ == "__main__":
    launch(start)
    launch(start)
