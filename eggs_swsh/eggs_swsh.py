import sys
import os
import time

import cv2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from switch import *

last = 0
count = 0

def send_cmd_status(_:str):
    notifier.send_message(f"Eggs hatched: {count}")

def main():
    set_left_stick(0, 0)
    set_right_stick(0, 0)
    input("Start")
    notifier.register_command("status", send_cmd_status, "Show the status of the hatching process")
    i = 0
    while True:
        goto_nursery()
        goto_hatch()
        hatch()
        i+=1
        print(i)


def goto_nursery():
    global last
    click("H",1,2)
    click("X", 1)
    click("A", 4)
    click("A", 2, 2)
    time.sleep(2)
    set_left_stick(0, 1)
    time.sleep(3)
    set_left_stick(-0.5, 0)
    time.sleep(0.2)
    set_left_stick(0, 0)
    time.sleep(1)
    click("A", 1, 2)
    time.sleep(2)
    click("A", 3)
    click("A", 2)
    click("A", 2)
    click("2", repeat=last + 1)
    click("A", 3, 3)
    last += 1
    if last >= 5:
        last = 0


def goto_hatch():
    set_left_stick(1, 0)
    time.sleep(5)
    set_left_stick(0, 0)
    click("+")


def hatch():
    global count
    set_left_stick(1, 0)
    set_right_stick(1, 0)
    while not capture.region_is_mostly_color(
        1034, 1728, 2822, 2088, "#3b393b", 1.0, 10
    ):
        time.sleep(1)
        set_left_stick(1,0)
        set_right_stick(1,0)

    os.makedirs("screenshots", exist_ok=True)
    filename = f"screenshots/{time.strftime('%Y%m%d_%H%M%S')}.png"
    cv2.imwrite(filename, capture.get_latest_frame())
    time.sleep(1)
    # input("BBB")
    click("A", 1)
    time.sleep(20)
    # while not capture.region_is_mostly_color(3036, 2042, 3066, 2082,"#feffff", 0.9, 2):
    #     time.sleep(1)

    time.sleep(1)
    click("A", 4)
    click("+", 3)
    set_left_stick(0, 0)
    set_right_stick(0, 0)

    count += 1

if __name__ == "__main__":
    launch(main)
