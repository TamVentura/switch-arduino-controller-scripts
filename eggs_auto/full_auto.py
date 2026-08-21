import os
import sys
from typing import Literal


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))
import codecs

import eggs_auto.hatch_eggs as hatch_eggs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import time

from pokemon_sv import *
from recorder import play_recording

from switch import (Fore, NotificationResult, Style, TelegramAction,
                    TelegramNotifier, _run, capture, click,
                    connect, controller, hold, hold_for, launch, notifier, ocr,
                    release, restart_game, set_left_stick, set_right_stick,
                    settings)



def get_iterations():
    iterations = -2
    error = False
    while iterations < -1:
        result = notifier.send_and_wait(
            text=(
                "How many iterations?"
                if not error
                else "Please enter a valid number of iterations or press the button below."
            ),
            wait_for_text=True,
            actions=[TelegramAction("Infinite", "infinite")],
            console_prompt=(
                "Number of iterations (-1 for infinite): "
                if not error
                else "Please enter a valid digit. Number of iterations (-1 for infinite): "
            ),
        )
        if result.source == "console":
            if not result.action_value or not (
                result.action_value.isdigit()
                or (
                    result.action_value.startswith("-")
                    and result.action_value[1:].isdigit()
                )
            ):
                error = True
                continue
            temp_iterations = int(result.action_value)
            if temp_iterations > 0 or temp_iterations == -1:
                iterations = temp_iterations
            else:
                error = True
        elif result.source == "telegram":
            if result.action_value == "infinite":
                iterations = -1
            else:
                if result.action_value and result.action_value.isdigit():
                    iterations = int(result.action_value)
                else:
                    error = True
    return iterations


def stop_at_shiny() -> Literal["any", "male", "female"]:
    shiny_mode = "error"
    while shiny_mode == "error":
        result = notifier.send_and_wait(
            text="Stop at what shiny?",
            actions=[
                TelegramAction("Any", "any"),
                TelegramAction("Male", "male"),
                TelegramAction("Female", "female"),
            ],
            console_prompt="Stop at what shiny? (a/m/f): ",
            catch_space=False,
        )
        if result.source == "console":
            if not result.action_value:
                continue
            if result.action_value.strip("\n ") == "a":
                shiny_mode = "any"
            elif result.action_value.strip("\n ") == "m":
                shiny_mode = "male"
            elif result.action_value.strip("\n ") == "f":
                shiny_mode = "female"
        elif result.source == "telegram":
            if result.action_value:
                if (
                    result.action_value == "any"
                    or result.action_value == "male"
                    or result.action_value == "female"
                ):
                    shiny_mode = result.action_value

    return shiny_mode


def get_number_of_sandwich_per_iteration():
    number_sandwiches_per_iteration = -2
    error = False
    while number_sandwiches_per_iteration < -1:
        result = notifier.send_and_wait(
            text=(
                "How many sandwiches per iteration?"
                if not error
                else "Please enter a valid number of sandwiches per iteration"
            ),
            wait_for_text=True,
            actions=[],
            console_prompt=(
                "Number of sandwiches per iteration: "
                if not error
                else "Please enter a valid digit. Number of sandwiches per iteration: "
            ),
        )
        if result.source == "console":
            if not result.action_value or not result.action_value.isdigit():
                error = True
                continue
            temp_iterations = int(result.action_value)
            if temp_iterations > 0:
                number_sandwiches_per_iteration = temp_iterations
            else:
                error = True
        elif result.source == "telegram":
            if result.action_value and result.action_value.isdigit():
                number_sandwiches_per_iteration = int(result.action_value)
            else:
                error = True

    return number_sandwiches_per_iteration
