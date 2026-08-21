import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import time

import numpy as np

from switch import *

_DEBUG_REGIONS = [
    ("emotion",         550, 159,  609, 217,  (0,   255,   0)),
]

item_region= ((1000, 1596), (1066, 1636))
text_region = ((956, 1706), (1036, 1862))

colors = [
   "#df4e4a", #Red
   "#ffcf00", #Yellow
   "#2181e0", #Blue
   "#7fcc39", #Green
   "#2a4658"  #Dark Blue
]
item_color = "#12282e"
text_color = "#f5f3f5"

colors = [
    ("#df4e4a", "red"), #Red
    ("#ffcf00", "yellow"), #Yellow
    ("#2181e0", "blue"), #Blue
    ("#7fcc39", "green"), #Green
    ("#2a4658", "dark_blue")  #Dark Blue
]




def checkColor():
    best_color = (("", "none"), 0)
    for (color, _) in colors:
        tolerance = capture.tolerance_for_region(550, 159, 609, 217, color, 0.5)
        if(tolerance > best_color[1]):
            best_color = (color, tolerance)
    return best_color[0]


def play_game(game_count = 151):
    old_value = "none"
    count = 0
    while count < game_count:
        value = checkColor()[1]
        if value == "dark_blue":
            click("A", 5)
            continue
        #print(f"Detected color: {value}")
        if value != old_value and value != "none":
            _color_fore = {"red": Fore.RED, "yellow": Fore.YELLOW, "blue": Fore.BLUE, "green": Fore.GREEN}
            #if value != "none": print(f"{Fore.LIGHTCYAN_EX}Detected new color:{Style.RESET_ALL} {_color_fore.get(value, '')}{value}{Style.RESET_ALL}")
            print(f"{Fore.LIGHTMAGENTA_EX}Total count:{Style.RESET_ALL} {count+1} - Detected color: {_color_fore.get(value, '')}{value}{Style.RESET_ALL}")
            if(value == "red"):
                click("X")
            elif(value == "yellow"):
                click("A")
            elif(value == "blue"):
                click("B")
            elif(value == "green"):
                click("Y")
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
    click("A", 2.5)
    click("A", 0.7)
    click("A", 1.2)
    click("A", 0.7)
    click("2")
    click("A", 0.7)
    click("A", 1.5)
    click("A", 2)
    click("A", 2)

def end_game():
    complete = False
    while not complete:
        complete = True
        while pokemon_sv.get_mode() != Mode.IN_GAME:
            click("A", 0.5)
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
        #INSERTED NOTIFIER HERE
        _result = notifier.send_and_wait(
            text="Number of games to play (0 = infinite, -1 = quit):",
            console_prompt="Number of game to play (0 to infinite and -1 to quit): ",
            wait_for_text=True,
        )
        games_input = (_result.action_value or "").strip()
        # games_input = input("Number of game to play (0 to infinite and -1 to quit): ").strip()
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
    launch(main, debug_regions=_DEBUG_REGIONS)

if __name__ == "__main__":
    launch(main, debug_regions=_DEBUG_REGIONS)
