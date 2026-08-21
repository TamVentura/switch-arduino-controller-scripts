

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import time


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

def main():
    i = 0
    while True:
        main_loop()
        i+=1
        if(i% 200 == 0):
            notifier.send_message(f"Looped {i}")

def main_loop():
    click("+",1)
    set_left_stick(-0.5,1)
    time.sleep(.1)
    set_left_stick(0.0,0.0)
    time.sleep(1)
    click("A", 1, 2)
    time.sleep(2)

if __name__ == "__main__":
    launch(main)