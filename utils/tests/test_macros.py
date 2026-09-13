"""
Unit tests for the macro DSL parser (utils/macros.py).

Pure text-in / commands-out: no Arduino, no serial, no pygame.  The parser is
the guard that stops a typo from reaching the controller, so everything it
rejects is tested here as well as everything it accepts.
"""

import os
import sys

import pytest

# Make utils/macros.py importable without the switch bootstrap.
_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

import macros as M  # noqa: E402
from macros import Command, MacroError  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Core grammar
# ─────────────────────────────────────────────────────────────────────────────


def test_click_alone_uses_controller_defaults():
    assert M.parse("click A") == [
        Command("click", {"key": "A", "sleep": 0.5, "repeat": 1, "hold": 0.1})
    ]


def test_hold_and_release_carry_only_the_key():
    assert M.parse("hold B\nrelease B") == [
        Command("hold", {"key": "B"}),
        Command("release", {"key": "B"}),
    ]


def test_hold_for_takes_a_duration():
    assert M.parse("hold_for R 2") == [
        Command("hold_for", {"key": "R", "duration": 2.0})
    ]


def test_wait_takes_seconds():
    assert M.parse("wait 1.5") == [Command("wait", {"seconds": 1.5})]


def test_stick_takes_a_side_and_two_axes():
    assert M.parse("stick left 0 -1") == [
        Command("stick", {"side": "left", "x": 0.0, "y": -1.0})
    ]


def test_blank_lines_and_comments_are_dropped():
    text = "# macro: sair-e-voltar\n\nclick A\n   # outro comentario\n\nwait 1\n"
    assert M.parse(text) == [
        Command("click", {"key": "A", "sleep": 0.5, "repeat": 1, "hold": 0.1}),
        Command("wait", {"seconds": 1.0}),
    ]


def test_empty_text_parses_to_no_commands():
    assert M.parse("") == []


def test_extra_whitespace_between_tokens_is_tolerated():
    assert M.parse("   click   A   ") == [
        Command("click", {"key": "A", "sleep": 0.5, "repeat": 1, "hold": 0.1})
    ]


# ─────────────────────────────────────────────────────────────────────────────
# click parameters
# ─────────────────────────────────────────────────────────────────────────────


def test_click_repeat_uses_the_xN_form():
    assert M.parse("click A x3")[0].args["repeat"] == 3


def test_click_accepts_sleep_and_hold_overrides():
    args = M.parse("click A sleep=0.3 hold=0.05")[0].args
    assert args["sleep"] == 0.3
    assert args["hold"] == 0.05


def test_click_parameters_are_order_independent():
    assert M.parse("click A sleep=0.3 x2") == M.parse("click A x2 sleep=0.3")


# ─────────────────────────────────────────────────────────────────────────────
# Key names
# ─────────────────────────────────────────────────────────────────────────────


def test_single_character_keys_are_case_sensitive():
    # 'l'/'r' are ZL/ZR on the Arduino, 'L'/'R' are the bumpers — never fold case.
    assert M.parse("hold l")[0].args["key"] == "l"
    assert M.parse("hold L")[0].args["key"] == "L"


def test_stick_direction_letters_are_valid_keys():
    assert M.parse("hold_for w 1.2")[0].args["key"] == "w"


def test_named_aliases_map_to_arduino_characters():
    assert M.parse("click up")[0].args["key"] == "4"
    assert M.parse("click down")[0].args["key"] == "2"
    assert M.parse("hold zl")[0].args["key"] == "l"
    assert M.parse("click home")[0].args["key"] == "H"
    assert M.parse("click capture")[0].args["key"] == "C"


def test_named_aliases_ignore_case():
    assert M.parse("click UP")[0].args["key"] == "4"


# ─────────────────────────────────────────────────────────────────────────────
# Validation — nothing reaches the Arduino unless the whole text parses
# ─────────────────────────────────────────────────────────────────────────────


def test_unknown_command_is_rejected():
    with pytest.raises(MacroError, match="clique"):
        M.parse("clique A")


def test_unknown_key_is_rejected():
    with pytest.raises(MacroError, match="Z"):
        M.parse("click Z")


def test_error_reports_the_line_number_of_the_original_text():
    # Comments and blank lines must not shift the count: the bad line is 4.
    with pytest.raises(MacroError, match=r"linha 4"):
        M.parse("# comentario\n\nclick A\nclick Z\n")


def test_missing_argument_is_rejected():
    with pytest.raises(MacroError, match=r"linha 1"):
        M.parse("hold_for R")


def test_trailing_junk_is_rejected():
    with pytest.raises(MacroError, match=r"linha 1"):
        M.parse("wait 1 2")


def test_non_numeric_duration_is_rejected():
    with pytest.raises(MacroError, match="abc"):
        M.parse("wait abc")


def test_negative_wait_is_rejected():
    with pytest.raises(MacroError, match=r"linha 1"):
        M.parse("wait -1")


def test_repeat_must_be_at_least_one():
    with pytest.raises(MacroError, match=r"linha 1"):
        M.parse("click A x0")


def test_unknown_click_parameter_is_rejected():
    with pytest.raises(MacroError, match="speed"):
        M.parse("click A speed=2")


def test_stick_side_must_be_left_or_right():
    with pytest.raises(MacroError, match="middle"):
        M.parse("stick middle 0 0")


def test_stick_axis_outside_the_unit_range_is_rejected():
    with pytest.raises(MacroError, match=r"linha 1"):
        M.parse("stick left 0 -2")


def test_every_bad_line_is_reported_not_just_the_first():
    with pytest.raises(MacroError) as excinfo:
        M.parse("click Z\nwait abc\nclick A\n")
    message = str(excinfo.value)
    assert "linha 1" in message
    assert "linha 2" in message
