"""
Tests for the terminal menu's decisions (utils/macro_menu.py).

What is tested is what the menu *decides* and what it *shows* — reading a line
of input and drawing the list — not the input() loop around it.
"""

import os
import sys

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

import macro_menu as MM  # noqa: E402
from macro_menu import Action  # noqa: E402

NAMES = ["abrir", "farmar", "sair"]


def choice(text):
    return MM.parse_choice(text, NAMES)


# ─────────────────────────────────────────────────────────────────────────────
# Reading a line
# ─────────────────────────────────────────────────────────────────────────────


def test_a_bare_number_toggles_that_macro():
    assert choice("2") == Action("toggle", name="farmar")


def test_l_prefix_toggles_it_as_a_loop():
    assert choice("l2") == Action("toggle", name="farmar", loop=True)


def test_e_prefix_edits_and_d_prefix_deletes():
    assert choice("e1") == Action("edit", name="abrir")
    assert choice("d3") == Action("delete", name="sair")


def test_single_letters_are_the_standalone_commands():
    assert choice("n") == Action("new")
    assert choice("s") == Action("stop")
    assert choice("q") == Action("quit")


def test_an_empty_line_just_redraws():
    assert choice("") == Action("refresh")
    assert choice("   ") == Action("refresh")


def test_spacing_and_case_do_not_matter():
    assert choice("  L2  ") == Action("toggle", name="farmar", loop=True)
    assert choice("N") == Action("new")


def test_a_number_with_no_macro_behind_it_is_an_error():
    action = choice("9")
    assert action.kind == "error"
    assert "9" in (action.message or "")


def test_gibberish_is_an_error_not_a_crash():
    assert choice("xpto").kind == "error"


def test_a_letter_prefix_with_no_number_is_an_error():
    assert choice("l").kind == "error"


# ─────────────────────────────────────────────────────────────────────────────
# Drawing the list
# ─────────────────────────────────────────────────────────────────────────────


def test_every_macro_is_listed_and_numbered():
    listing = "\n".join(MM.render(NAMES, status=None))
    assert "1" in listing and "abrir" in listing
    assert "3" in listing and "sair" in listing


def test_the_running_macro_is_marked():
    status = {"name": "farmar", "loop": False, "lap": 1}
    lines = MM.render(NAMES, status=status)
    marked = [ln for ln in lines if MM.RUNNING_MARK in ln]

    assert len(marked) == 1
    assert "farmar" in marked[0]


def test_nothing_is_marked_when_nothing_runs():
    lines = MM.render(NAMES, status=None)
    assert not [ln for ln in lines if MM.RUNNING_MARK in ln]


def test_a_looping_macro_shows_which_lap_it_is_on():
    status = {"name": "farmar", "loop": True, "lap": 12}
    marked = [ln for ln in MM.render(NAMES, status=status) if MM.RUNNING_MARK in ln]
    assert "12" in marked[0]


def test_an_empty_list_says_so_instead_of_showing_nothing():
    listing = "\n".join(MM.render([], status=None))
    assert "macro" in listing.lower()
