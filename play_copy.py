"""
play.py — Xbox controller passthrough to Nintendo Switch via Arduino.
Just run this to play normally with your Xbox controller.

    python play.py
    python play.py COM8   # override port
"""

import sys
import time

import cv2

from switch import (capture, click, hold, launch, play_recording, release,
                    set_left_stick, template_matcher)
from utils.pokemon_sv import *

port = sys.argv[1] if len(sys.argv) > 1 else None

# Hop = ZL (Z-target) + left stick + X (jump).  Same Y sign as the Xbox
# passthrough (pygame): up is -1, down is +1.
_HOP_STICK = {
    "right": (1.0, 0.0),
    "left": (-1.0, 0.0),
    "back": (0.0, 1.0),
    "front": (0.0, -1.0),
}


def hops(*directions: str, lock: float = 0.3, airtime: float = 0.55,
         settle: float = 0.1):
    """Sidehops / backflips / front hops chained under a single ZL press.

    ZL goes down `lock` seconds before the stick: tilt the stick before the
    Z-target settles and Link turns to face that way, so a sidehop comes out
    as a front hop.  ZL stays held across the whole chain, so the lock is
    only paid once; the stick is centred after each hop's `airtime`.
    """
    hold("l")
    time.sleep(lock)
    for direction in directions:
        x, y = _HOP_STICK[direction]
        set_left_stick(x, y)
        time.sleep(0.05)
        click("X", sleep=0, hold_for=0.1)
        time.sleep(airtime)
        set_left_stick(0, 0)
    release("l")
    time.sleep(settle)


def hop(direction: str, **timing):
    """One hop with its own ZL press; see hops()."""
    hops(direction, **timing)


def strike(throw_key: str = "R"):
    """Snowball bowling setup: no camera alignment needed."""
    hop("right")
    click("A", sleep=0.8)  # pick up the snowball
    hops("left", "back", "back", "left", "back", "front")

    click(throw_key, sleep=0.5)

def spam_a(duration: float, interval: float = 0.2):
    """Mash A for `duration` seconds."""
    end = time.monotonic() + duration
    while time.monotonic() < end:
        click("A", sleep=interval)


def snowball_bowling():
    """Talk to the NPC and start a round, then throw.  The A spam lengths are
    the sums of the recorded gaps between presses."""
    spam_a(25)
    strike()
    # After the throw, from recording_20261015_140111.
    time.sleep(20)  # ball rolls, pins fall
    #capture.wait_for_pixel(1922, 1962, "#fbfbe2")

    spam_a(7)


def bowling_loop():
    """Play rounds back to back; Ctrl+C to stop."""
    while True:
        snowball_bowling()


def a():
    breakpoint()
    return
    while True:
        time.sleep(10)
        # input()
        # from pprint import pprint

        # templates = template_matcher.find_all(
        #     capture.get_latest_frame(),  # type: ignore
        #     "reference_image.png",
        #     threshold=0.5
        # )
        # pprint(len(templates))
        # pprint(templates)
        # result = template_matcher.draw_matches(capture.get_latest_frame(), templates) #type: ignore
        # cv2.imshow("Matches", result)


launch(a, focus=True)
