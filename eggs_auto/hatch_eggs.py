from __future__ import annotations

import os
import random
import sys
from typing import Literal

import cv2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import time
from dataclasses import dataclass

from switch import (Fore, NotificationResult, Style, TelegramAction,
                    TelegramNotifier, capture, click, connect, controller,
                    hold, hold_for, launch, notifier, ocr, release,
                    set_left_stick, set_right_stick, settings,
                    template_matcher)
from utils.pokemon_sv import MenuOption, Mode, get_mode, get_text_box, goto_menu_option


@dataclass(frozen=True)
class ShinyLocation:
    box: int
    col: int
    row: int
    gender: Literal["Male", "Female", "Genderless"]


first = True


def start():
    result = notifier.send_and_wait(
        text="Começar?",
        actions=[
            TelegramAction("Sim", "approve"),
            TelegramAction("Não", "reject"),
        ],
        console_prompt="Press Enter locally to continue...\n",
        wait_for_text=False,
    )
    print(result)

    run()


cur_box = 1
number_eggs = 0

shinies: list[ShinyLocation] = []

def run(_number_eggs=0):
    global number_eggs
    global shinies
    number_eggs = _number_eggs
    notifier.register_command(
        "status",
        send_cmd_status,
        "Show the status of the hatching process",
    )
    global cur_box
    cur_box = 1
    shinies = []
    open_box()

    while has_eggs():
        cur_box = ocr.read_int(capture.get_latest_frame(), 1742, 252, 1826, 338)
        cur_col = 1
        while has_eggs() and cur_col <= 6:
            shinies.extend(run_collumn(cur_col))
            cur_col += 1

        print(f"Finished box {cur_box}")
        click("R")
        click("1")
        # click("3", repeat=6)

    notifier.send_message("Finalizado")
    return shinies


def send_cmd_status(_: str):
    notifier.send_notification(
        text=f"Eggs hatched: {hatched_eggs_count}/{number_eggs} with {len(shinies)} shinies encountered",
        closer=True,
    )


def has_eggs(col=None):
    frame = (
        capture.get_frame_region(
            (900 + (col - 1) * 252),
            402,
            (1140 + (col - 1) * 252),
            1644,
        )
        if col is not None
        else capture.get_latest_frame()
    )

    templates = template_matcher.find_all(
        frame, "reference_image.png", threshold=0.4 if col is None else 0.4, # type: ignore
    )

    new_frame = template_matcher.draw_matches(capture.get_latest_frame(), templates)

    cv2.imwrite( os.path.join(os.path.dirname(__file__), ".", "debug", f"has_eggs_{time.time():.2f}.png"), new_frame)
    print(len(templates))
    return len(templates) if col is None else min(len(templates), 5)


def open_box():
    goto_menu_option(MenuOption.BOXES)
    click("A", 4)


def close_box():
    click("B", 1, repeat=2)
    time.sleep(2)


def run_collumn(cur_col):
    shinies: list[ShinyLocation] = []
    count_eggs = has_eggs(cur_col)
    if not count_eggs:
        click("1", 1)
        return []
    # click("1")
    click("-")
    click("2", repeat=4)
    click("A")

    click("3", repeat=cur_col)
    click("2")
    click("A")
    close_box()

    hatch_eggs(count_eggs)

    set_left_stick(0, 0)
    set_right_stick(0, 0)
    time.sleep(5)

    open_box()
    click("3")
    click("2")
    click("-")
    for i in range(0, count_eggs):
        shiny = capture.is_pixel_color(3414, 230, "#feffff", 100)
        if shiny:
            # Get gender
            gender = "Genderless"
            gender_color = capture.get_pixel_color(3762, 86)
            if capture.is_pixel_tolerance(gender_color, "#3e73ff"):
                gender = "Male"
            elif capture.is_pixel_tolerance(gender_color, "#ee444b"):
                gender = "Female"

            shinies.append(ShinyLocation(1, cur_col, i + 1, gender))
            frame = capture.get_frame_region(2499, 0, capture._4K_W, capture._4K_H)  # type: ignore
            notifier.send_photo(
                frame=frame,
                caption=f"Found shiny on {cur_box}, {cur_col}, {i + 1}",
                disable_notification=False,
            )
            print("shiny")
        click("2")
    click("A")
    click("4")
    click("1", repeat=cur_col)
    click("A")
    click("1")

    print(f"Finished box {cur_box}, col {cur_col}")

    if cur_col == 6:
        notifier.send_message(f"Finished box {cur_box}")
    return shinies


def hatch_eggs(nEggs):
    for _ in range(0, nEggs):
        hatch_egg()

hatched_eggs_count = 0

def hatch_egg():
    global hatched_eggs_count
    set_right_stick(1, 0)
    set_left_stick(1, 0)
    time.sleep(0.8)
    hold("{")

    while get_mode() != Mode.TEXT_BOX:
        if random.random() < 0.01:
            click("H", 1, repeat=2)
        set_right_stick(1 - random.random() * 0.1, random.random() * 0.2 - 0.1)
        set_left_stick(1 - random.random() * 0.1, random.random() * 0.2 - 0.1)
        time.sleep(2)
        continue
    release("{")
    while get_mode() == Mode.TEXT_BOX:
        click("A", 0.3)
    temp = 0
    time.sleep(5)
    while get_mode() != Mode.TEXT_BOX:
        if temp > 10:
            break
        temp += 1
        continue
    time.sleep(5)
    click("A", 1, repeat=2)
    time.sleep(1)
    hatched_eggs_count +=1
    while get_mode() != Mode.IN_GAME:
        click("A", 5)
        continue
    


if __name__ == "__main__":
    launch(start)
