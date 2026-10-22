"""
play.py — Xbox controller passthrough to Nintendo Switch via Arduino.
Just run this to play normally with your Xbox controller.

    python play.py
    python play.py COM8   # override port
"""

import sys
import time

import cv2

from switch import (capture, launch, play_recording,
                    template_matcher)
from utils.pokemon_sv import *

port = sys.argv[1] if len(sys.argv) > 1 else None


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
