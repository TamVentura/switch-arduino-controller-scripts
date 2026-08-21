import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import time

from switch import *

_DEBUG_REGIONS = []

def sleep_debug(seconds, print_every_x_seconds=1):
    # For loop for every x seconds to print the time, when there is no more X seconds left, sleep the remaining seconds and print that the sleep is done.
    for i in range(0, int(seconds), print_every_x_seconds):
        print(f"{Fore.MAGENTA}Sleeping...{Style.RESET_ALL} {seconds - i} seconds left")
        time.sleep(print_every_x_seconds)
    remaining = seconds % print_every_x_seconds
    if remaining > 0:
        print(f"{Fore.MAGENTA}Sleeping...{Style.RESET_ALL} {remaining} seconds left")
        time.sleep(remaining)


def skip_chata():
    click("A")
    click("A")
    click("A")
    click("A")
    click("A")
    click("A")
    click("A")
    click("A")
    click("A",2)

def start_dynamax_adventures(hintid=1):
    click("A",1)
    click("A")
    click("A",1)
    click("A")
    click("A", 1)
    click("2", 0.3, repeat=hintid - 1)
    click("A")
    click("A")
    click("A")
    click("A")
    click("A")
    click("A")
    time.sleep(5)
    click("2")
    click("A",2)
    click("A")
    time.sleep(20)

def play_round(catch_pokemon=True, correct_pokeball=False, last_round=False):
    click("A",0.5, 20)
    time.sleep(30)
    hold("r")
    click("A")
    click("A")
    click("A")
    time.sleep(36)
    release("r")
    time.sleep(1)
    if not catch_pokemon:
        click("2")
    click("A",1)
    if catch_pokemon and not correct_pokeball:
        click("1", repeat=5)
    if catch_pokemon:
      click("A",1)
      click("A",1)
      time.sleep(25)
    else:
        time.sleep(1)
    if(not last_round and catch_pokemon):
        click("2")
        click("A")
    time.sleep(5)
    
def reset():
    click("H")
    click("Y")
    click("A")
    time.sleep(5)
    click("A", 20)
    click("A")
    time.sleep(7)


def _start():
    # Read the hint ID from the user
    # hintid = input("Enter the hint ID (1-4): ")
    # if hintid not in ["1", "2", "3", "4"]:
    #     print("Invalid hint ID. Please enter a number between 1 and 4.")
    #     start()
    #     return
    #INSERTED NOTIFIER HERE
    notifier.send_and_wait(
        text="Ready to start Dynamax Adventures with hint ID 2?",
        console_prompt="Press Enter to start Dynamax Adventures with hint ID 2...",
        actions=[TelegramAction("Start", "start")],
    )
    # input("Press Enter to start Dynamax Adventures with hint ID 2...")
    hintid = 3
    print(f"Starting Dynamax Adventures with hint ID {hintid}...")
    # While True here when the code is fully complete and working to allow multiple runs without restarting the script. For now, just run once to test.
    #reset()
    #skip_chata()
    while True:
      start_dynamax_adventures(int(hintid))
      play_round(catch_pokemon=True)
      for i in range(2):
        play_round(catch_pokemon=False)
      play_round(last_round=True)


      time.sleep(10)
      # Check if the last mon is shiny
      click("2")
      #INSERTED NOTIFIER HERE
      _r = notifier.send_and_wait(
          text="Is the last Pokemon shiny?",
          console_prompt="Is the last Pokemon shiny? (y/n): ",
          actions=[TelegramAction("Yes", "y"), TelegramAction("No", "n")],
      )
      shiny = _r.action_value == "y"
      # shiny = input("Is the last Pokemon shiny? (y/n): ") == "y"
      if not shiny:
          print("Not shiny, resetting...")
          click("2")
          click("A")
          click("A")
          click("A")
          click("A")
          click("A",1)
          click("A")
          time.sleep(7)
          click("A", 5)
      else:
          print(f"{Fore.GREEN}Shiny found!{Style.RESET_ALL} Resetting for next run...")
          while True:
              time.sleep(5)
      time.sleep(10)


if __name__ == "__main__":
    launch(_start, debug_regions=_DEBUG_REGIONS)
if __name__ == "__main__":
    launch(_start, debug_regions=_DEBUG_REGIONS)
