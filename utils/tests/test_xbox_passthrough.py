"""
Tests for suspending the Xbox passthrough while a macro runs.

Both the passthrough thread and a macro write to the same serial line, so a
macro only does exactly what it says if the pad is muted meanwhile.  What is
exercised here is the muting itself with a fake joystick and a fake controller
module — no pad, no Arduino.  The one thing that matters and cannot be faked
(that the Switch actually sees nothing) is checked live.
"""

import os
import sys

import pytest

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

import xbox_passthrough as X  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────────────


class FakeController:
    """Stands in for controller.py, recording what the passthrough sends."""

    def __init__(self):
        self.calls: list[tuple] = []

    def hold(self, key):
        self.calls.append(("hold", key))

    def release(self, key):
        self.calls.append(("release", key))

    def set_left_stick(self, x, y):
        self.calls.append(("left", x, y))

    def set_right_stick(self, x, y):
        self.calls.append(("right", x, y))

    def mark_as_bypass(self):
        pass


class FakeJoystick:
    """A pad with `buttons` pressed; everything else idle."""

    def __init__(self, buttons=()):
        self.buttons = set(buttons)

    def get_numbuttons(self):
        return 11

    def get_numaxes(self):
        return 6

    def get_numhats(self):
        return 1

    def get_button(self, idx):
        return idx in self.buttons

    def get_axis(self, idx):
        return -1.0 if idx in (X.AXIS_LT, X.AXIS_RT) else 0.0

    def get_hat(self, _idx):
        return (0, 0)


@pytest.fixture
def pad(monkeypatch):
    """A passthrough input thread wired to a fake pad and a fake controller."""
    fake = FakeController()
    monkeypatch.setattr(X, "sw", fake)
    X.resume()  # never leak suspension between tests

    thread = X._InputThread(on_disconnect=lambda _iid: None)
    joystick = FakeJoystick()
    thread._joy = joystick
    thread._iid = X._get_selected()
    yield thread, joystick, fake
    X.resume()


# ─────────────────────────────────────────────────────────────────────────────
# Suspending
# ─────────────────────────────────────────────────────────────────────────────


def test_suspending_releases_everything_the_pad_was_holding(pad):
    thread, joystick, fake = pad
    joystick.buttons = {0}  # A held down
    thread._tick()
    assert ("hold", "B") in fake.calls  # pad button 0 maps to Switch B

    fake.calls.clear()
    X.suspend()
    thread._tick()

    assert ("release", "B") in fake.calls


def test_suspending_centres_both_sticks(pad):
    thread, _joystick, fake = pad
    X.suspend()
    thread._tick()

    assert ("left", 0.0, 0.0) in fake.calls
    assert ("right", 0.0, 0.0) in fake.calls


def test_a_suspended_pad_sends_nothing_however_hard_you_press(pad):
    thread, joystick, fake = pad
    X.suspend()
    thread._tick()

    fake.calls.clear()
    joystick.buttons = {0, 1, 2}
    thread._tick()
    thread._tick()

    assert fake.calls == []


def test_releasing_happens_once_not_on_every_tick(pad):
    thread, _joystick, fake = pad
    X.suspend()
    thread._tick()
    fake.calls.clear()

    thread._tick()

    assert fake.calls == []


def test_resuming_resends_a_button_that_is_still_held(pad):
    thread, joystick, fake = pad
    joystick.buttons = {0}
    thread._tick()

    X.suspend()
    thread._tick()
    fake.calls.clear()

    X.resume()
    thread._tick()

    # The pad never let go of A, so the Switch has to be told again.
    assert ("hold", "B") in fake.calls


def test_is_suspended_reports_the_state(pad):
    assert X.is_suspended() is False
    X.suspend()
    assert X.is_suspended() is True
    X.resume()
    assert X.is_suspended() is False


def test_resuming_resends_a_stick_that_is_still_pushed(pad):
    thread, joystick, fake = pad
    joystick.get_axis = lambda idx: (
        -1.0 if idx in (X.AXIS_LT, X.AXIS_RT) else (0.5 if idx == X.AXIS_LEFT_X else 0.0)
    )
    thread._tick()
    assert ("left", 0.5, 0.0) in fake.calls

    X.suspend()
    thread._tick()
    fake.calls.clear()

    X.resume()
    thread._tick()

    # The stick never came back to centre, so its position has to be resent —
    # otherwise the Switch stays centred while the pad is still pushed over.
    assert ("left", 0.5, 0.0) in fake.calls
