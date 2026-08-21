import os
import sys

from eggs_auto.full_auto import (
    get_iterations,
    get_number_of_sandwich_per_iteration,
    stop_at_shiny,
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))
import codecs

print(os.environ.get("PYTHONIOENCODING", "").lower())
import eggs_auto.hatch_eggs as hatch_eggs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import time

from pokemon_sv import *
from recorder import play_recording

from switch import (
    Fore,
    NotificationResult,
    Style,
    TelegramAction,
    TelegramNotifier,
    _run,
    capture,
    click,
    connect,
    controller,
    hold,
    hold_for,
    launch,
    notifier,
    ocr,
    release,
    restart_game,
    set_left_stick,
    set_right_stick,
    settings,
)

# GET MAP (FULL ZL)
# IS PALDEA MAP
# print(capture._region_has_hex_color(capture.get_latest_frame(), 586, 775, 587, 776, "#0d2b54"))
# IS KITIKAMI MAP
# print(capture._region_has_hex_color(capture.get_latest_frame(), 586, 775, 587, 776, "#0e6c4d"))
# IS BLUEBERRY MAP
# print(capture._region_has_hex_color(capture.get_latest_frame(), 586, 775, 587, 776, "#003f60"))


def get_start_mode():
    # print(capture.region_is_mostly_color(1528, 694, 1716, 756, "#feffff"))
    # input()
    # return "stop"
    result = notifier.send_and_wait(
        text="Como começar?",
        frame=capture.get_latest_frame(),
        console_prompt="""
O que fazer?
  
  0. Reiniciar o jogo e proceder sem localização
  1. Sem localização
  2. Com localização
  3. Manualmente colocado
  4. Full Auto
  5. Hatching
  6. Parar

Instrucao: """,
        actions=[
            TelegramAction("Reiniciar o jogo e proceder sem localização", "restart"),
            TelegramAction("Sem localização", "no_location"),
            TelegramAction("Com localização", "with_location"),
            TelegramAction("Manualmente colocado", "manual"),
            TelegramAction("Full Auto", "full_auto"),
            TelegramAction("Hatching", "hatching"),
            TelegramAction("Parar", "stop"),
        ],
        wait_for_text=False,
    )

    match result.action_value:
        case "restart" | "0":
            print("Procedendo reiniciando o jogo")
            return "restart"
        case "no_location" | "1":
            print("Procedendo sem localização")
            return "no_location"
        case "with_location" | "2":
            print("Procedendo com localização, mas sem estar em modo manual")
            return "with_location"
        case "manual" | "3":
            print("Posicionamento manual")
            return "manual"
        case "full_auto" | "4":
            print("Full Auto")
            return "full_auto"
        case "hatching" | "5":
            print("Hatching")
            return "hatching"
        case "stop" | "6":
            print("A parar o programa")
            return "stop"
        case _:
            print("Unknown result")
            return "error"


def start(mode: str = ""):
    global eggs_bred_total, eggs_bred_batch
    # input()
    # _goto_picnic_location()
    # input()
    # get_picnic_ready()
    # input()
    # _setup_box_for_eggs()
    # input()
    # goto_hatch_egg_location()
    # return
    notifier.register_command(
        "status",
        send_cmd_status,
        "Show the time left to breed this batch of eggs and how many eggs have been bred",
    )
    if mode == "":
        mode = get_start_mode()
    while mode == "error" or mode == None:
        mode = get_start_mode()

    if mode == "full_auto":
        iterations = get_iterations()
        shiny_mode = stop_at_shiny()
        number_sandwiches_iteration = get_number_of_sandwich_per_iteration()
        while True:
            for _ in range(iterations if iterations != -1 else 9999):
                restart_game()
                _goto_picnic_location()
                get_picnic_ready()
                for _ in range(number_sandwiches_iteration):
                    make_sandwich()
                _setup_box_for_eggs()
                goto_hatch_egg_location()
                result = hatch_eggs.run(eggs_bred_total)
                eggs_bred_total = 0
                if len(result) > 0:
                    print(result)
                    if shiny_mode == "any":
                        notifier.send_message(f"Found {len(result)} shinies")
                        sys.exit()
                    elif shiny_mode == "male":
                        if any([shiny.gender == "male" for shiny in result]):
                            notifier.send_message(
                                f"Found {len([shiny.gender == 'male' for shiny in result])} male shiny among {len(result)} shinies"
                            )
                            sys.exit()
                    elif shiny_mode == "female":
                        if any([shiny.gender == "female" for shiny in result]):
                            notifier.send_message(
                                f"Found {len([shiny.gender == "female" for shiny in result])} female shiny among {len(result)} shinies"
                            )
                            sys.exit()

    if mode == "restart":
        restart_game()
        mode = "no_location"

    if mode == "stop":
        return

    if mode == "no_location":
        _goto_picnic_location()

    if mode == "hatching":
        hatch_eggs_mode(mode)

    get_picnic_ready()

    while True:

        make_sandwich()

        after_sandwich(mode)


def after_sandwich(mode):
    global eggs_bred_total
    result = notifier.send_and_wait(
        text="Batch of eggs completed, what do you want to do?",
        console_prompt="1 to make another, 2 to hatch eggs or 0 to stop.",
        actions=[
            TelegramAction("More eggs", "start"),
            TelegramAction("Hatch eggs", "eggs"),
            TelegramAction("Stop", "stop"),
        ],
    )
    if result.action_value == "start" or result.action_value == "1":
        return "1"
    elif result.action_value == "stop" or result.action_value == "0":
        return "0"
    elif result.action_value == "eggs" or result.action_value == "2":
        hatch_eggs_mode(mode)
    else:
        return after_sandwich(mode)


def hatch_eggs_mode(mode):
    global eggs_bred_total
    _setup_box_for_eggs()
    if mode == "with_location" or mode == "no_location":
        goto_hatch_egg_location()
    elif mode == "manual":
        input("Clique enter quando estiver numa posição para andar")

    hatch_eggs.run(eggs_bred_total)
    eggs_bred_total = 0
    if(mode == "full_auto"):
        return "2"
    result = notifier.send_and_wait(
        text="Eggs hatched, what do you want to do?",
        console_prompt="1 to restart, or press Enter to stop.",
        actions=[
            TelegramAction("Restart", "restart"),
            TelegramAction("Stop", "stop"),
        ],
    )
    if result.action_value == "restart" or result.action_value == "1":
        setup("restart")
        return "2"
    else:
        return "0"


def setup(mode):
    if mode == "no_location":
        _goto_picnic_location()
        mode = "with_location"

    get_picnic_ready()


def goto_hatch_egg_location():
    play_recording(
        os.path.join(
            os.path.dirname(__file__),
            ".",
            "saved_outputs",
            "goto_hatching_location.json",
        )
    )


def get_picnic_ready():
    time.sleep(1)
    get_box_ready()
    goto_menu_option(MenuOption.PICNIC)
    click("A")
    click("2", 0.2)
    click("A", 5)
    click("+", 1)
    play_recording(
        os.path.join(
            os.path.dirname(__file__),
            ".",
            "saved_outputs",
            "setup_picnic.json",
        )
    )


def send_cmd_status(_: str):
    """Handler for the /screenshot bot command."""
    elapsed_time = 1800 - (time.time() - start_time)
    minutes, seconds = divmod(elapsed_time, 60)
    notifier.send_notification(
        text=f"{int(minutes):01d}:{int(seconds):02d} left. \n Eggs bred this batch: {eggs_bred_batch}\n Eggs bred total: {eggs_bred_total}",
        closer=True,
    )


def open_box():
    goto_menu_option(MenuOption.BOXES)
    click("A", 1)


def get_box_ready():
    open_box()
    click("R", 1)
    click("B", 1, 2)


def _setup_box_for_eggs():
    click("Y", 1)
    click("A", 2)
    open_box()
    click("L")
    click("Y")
    click("3")
    click("A")
    click("2")
    click("Y")
    click("4")
    click("1", repeat=2)
    click("A")
    click("R")
    click("B", 1, repeat=2)


def _goto_picnic_location():
    while get_mode() != Mode.IN_GAME and get_mode() != Mode.MAP:
        click("B")
    _open_paldea_map()
    click("r", 1)
    hold_for("z", 11, 0)

    set_left_stick(1, -0.86)
    time.sleep(1.2)
    set_left_stick(0, 0)
    time.sleep(1)
    click("A", 1, repeat=3)
    while get_mode() != Mode.IN_GAME:
        time.sleep(0.1)
    time.sleep(3)
    _goto_picknick_spot()


def _goto_picknick_spot():
    play_recording(
        os.path.join(
            os.path.dirname(__file__),
            ".",
            "saved_outputs",
            "goto_picnic.json",
        )
    )


def _open_paldea_map():
    _open_map()
    while not capture.is_pixel_color(1172, 1550, "#1f3762", 20):
        click("L", 1)


def _open_map():
    if get_mode() != Mode.MAP:
        while get_mode() != Mode.IN_GAME:
            click("B", 1)
        while get_mode() != Mode.MAP:
            click("Y", 1)
    click("l", repeat=4)


def get_eggs_boxes():
    click("X", 1)
    click("A", 1)


start_time = 0


def make_sandwich():
    global start_time
    global eggs_bred_batch

    set_left_stick(1, 0)
    time.sleep(0.2)
    set_left_stick(0, 0)
    time.sleep(0.2)
    play_recording(
        os.path.join(
            os.path.dirname(__file__),
            ".",
            "saved_outputs",
            "make_sandwich_egg_power.json",
        )
    )
    start_time = time.time()
    while not capture.is_pixel_tolerance(capture.get_pixel_color(159, 2037), "#fffdff"):
        click("A")
        time.sleep(1)
        if time.time() - start_time > 60:
            response = notifier.send_and_wait(
                text="Erro ao fazer a sandes. Por favor verifique o erro",
                frame=capture.get_latest_frame(),
                console_prompt="Erro ao fazer a sandes. Por favor verifique o erro. Press enter to continue",
                actions=[
                    TelegramAction("Continue", "continue"),
                    TelegramAction("Restart", "restart"),
                ],
                wait_for_text=False,
            )

            if response.action_value == "restart":
                start("restart")
                sys.exit(0)

    time.sleep(4)
    set_left_stick(-1, 0)
    time.sleep(0.1)
    set_left_stick(0, 0.5)
    time.sleep(0.15)
    set_left_stick(0, -0.5)
    time.sleep(0.1)
    set_left_stick(0, 0)
    time.sleep(0.1)

    # while for 30 minutes
    start_time = time.time()
    eggs_bred_batch = 0
    while time.time() - start_time < 1800:
        get_egg()


eggs_bred_total = 0
eggs_bred_batch = 0


def get_egg():
    global eggs_bred_total, eggs_bred_batch
    time.sleep(90)
    click("A", 0.4, 1)
    while get_mode() == Mode.QUESTION_INPUT or get_mode() == Mode.TEXT_BOX:
        click("A", 0.2)

        if get_mode() == Mode.QUESTION_INPUT:
            eggs_bred_batch += 1
            eggs_bred_total += 1


if __name__ == "__main__":
    launch(start)
