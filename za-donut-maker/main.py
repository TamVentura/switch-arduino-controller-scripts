import copy
import difflib
import json
import os
import re
import sys
import unicodedata
from typing import NotRequired, TypedDict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import time

import numpy as np

from switch import *

with open(
    os.path.join(os.path.dirname(__file__), "donuts_list.json"), encoding="utf-8"
) as f:
    BERRY_LIST = json.load(f)

from extra import (  # noqa: E402  (o * nao traz nomes com "_")
    Power,
    _match_power,
    _parse_extra,
    _parse_level,
    _same_extra,
)
from menu import ask_int, ask_text, confirm, select  # noqa: E402
from powers import powers_texts  # noqa: E402

# so as hyper interessam; o cursor comeca sempre na ultima da lista
HYPER_INDEX = {b["berry"].lower(): b["index"] for b in BERRY_LIST if b["hyper"]}
LAST_INDEX = max(HYPER_INDEX.values())


"""
Top texts:
(252, 558)
(254, 744)
(254, 930)
(254, 1114)
(254, 1486)
(254, 1676)
(254, 1862)



"""

saved_recipes = {
    "item": {
        "recipe": "kasib 8",
        "powers": [
            {"power": "item", "level": 3},
            {"power": "big_haul", "level": 3},
        ],
        "arguments": {
            "item": {
                "type": "single-option",
                "options": {
                    "candies": "Candies",
                    "berries": "Berries",
                    "treasure": "Treasure",
                },
                "required": True,
                "position": "powers[0].extra",
            }
        },
    },
    "shiny": {
        "recipe": "kasib 8",
        "powers": [
            {"power": "shiny", "level": 3},
            {"power": "", "level": 3},
        ],
        "arguments": {
            "type": {
                "type": "single-option",
                "options": {
                    "normal": "Normal",
                    "fire": "Fire",
                    "water": "Water",
                    "electric": "Electric",
                    "grass": "Grass",
                    "ice": "Ice",
                    "fighting": "Fighting",
                    "poison": "Poison",
                    "ground": "Ground",
                    "flying": "Flying",
                    "psychic": "Psychic",
                    "bug": "Bug",
                    "rock": "Rock",
                    "ghost": "Ghost",
                    "dragon": "Dragon",
                    "dark": "Dark",
                    "steel": "Steel",
                    "fairy": "Fairy",
                    "all types": "All Types",
                },
                "required": False,
                "position": "powers[0].extra",
            },
            "size": {
                "type": "single-option",
                "options": {"teensy": "Small", "big": "Large", "alpha": "Alpha"},
                "required": True,
                "position": "powers[1].power",
            },
        },
    },
}


def main():
    recipe, powers = _choose_donut()
    count = ask_int("Quantos donuts")

    print()
    print(f"{Fore.CYAN}Receita:{Style.RESET_ALL} {recipe}")
    _print_powers("Powers:", powers, Fore.CYAN)
    print(f"{Fore.CYAN}Quantidade:{Style.RESET_ALL} {count}\n")

    try_donut(recipe, powers, count)


def _choose_donut() -> tuple[str, list[Power]]:
    """A receita e as powers a pedir, guardadas ou escritas a mao."""
    if confirm("Usar uma receita guardada?"):
        options = [
            (name, f"{name} {Fore.LIGHTBLACK_EX}({_summarize(entry)}){Style.RESET_ALL}")
            for name, entry in saved_recipes.items()
        ]
        return _build_saved_donut(saved_recipes[select("Que receita?", options)])
    return _ask_manual_donut()


def _summarize(entry: dict) -> str:
    """A receita guardada numa linha, para se ver o que se esta a escolher."""
    powers = " + ".join(
        _format_power(power) for power in entry["powers"] if power.get("power")
    )
    return f"{entry['recipe']} | {powers}" if powers else entry["recipe"]


def _build_saved_donut(entry: dict) -> tuple[str, list[Power]]:
    entry = copy.deepcopy(entry)  # os argumentos escrevem por cima da receita
    for name, spec in entry.get("arguments", {}).items():
        value = _ask_argument(name, spec)
        if value is None:  # argumento opcional deixado em branco
            continue
        _set_at_position(entry, spec["position"], value)

    # uma power sem nome e um sitio que nenhum argumento preencheu: nao se pede
    powers = [power for power in entry["powers"] if power.get("power")]
    return entry["recipe"], powers


def _ask_argument(name: str, spec: dict):
    label = name.replace("_", " ").capitalize()
    kind = spec.get("type", "string")
    required = spec.get("required", False)

    if kind == "single-option":
        options = [(value, text) for value, text in spec["options"].items()]
        if not required:
            options.append((None, "(qualquer)"))
        return select(f"{label}?", options)
    if kind == "string":
        return ask_text(label, required=required)
    if kind == "int":
        return ask_int(label, default=spec.get("default", 1))
    raise ValueError(f"tipo de argumento desconhecido: {kind!r}")


# nomes e indices de uma position tipo "powers[0].extra"
_POSITION_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


def _set_at_position(entry: dict, position: str, value) -> None:
    parts = [
        name if name else int(index) for name, index in _POSITION_RE.findall(position)
    ]
    if not parts:
        raise ValueError(f"position invalida: {position!r}")

    node = entry
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def _ask_manual_donut() -> tuple[str, list[Power]]:
    while True:
        recipe = ask_text('Receita (ex: "kasib 4, tanga 4")')
        try:
            recipe_to_commands(recipe)
            break
        except ValueError as error:
            print(f"{Fore.LIGHTRED_EX}{error}{Style.RESET_ALL}")

    while True:
        try:
            powers = _parse_powers(ask_text('Powers (ex: "item 3 candies")'))
            return recipe, powers
        except ValueError as error:
            print(f"{Fore.LIGHTRED_EX}{error}{Style.RESET_ALL}")


def _parse_powers(line: str) -> list[Power]:
    """Ex.: "item 3 candies, big_haul 3" -> as powers que o check_donut espera."""
    powers: list[Power] = []
    for part in line.split(","):
        tokens = part.split()
        if not tokens:
            continue

        name = tokens.pop(0).lower()
        if name not in powers_texts:
            raise ValueError(
                f"power desconhecida: {name!r} (ha: {', '.join(sorted(powers_texts))})"
            )

        power: Power = {"power": name}
        if tokens and tokens[0].isdigit():
            power["level"] = int(tokens.pop(0))
        if tokens:
            power["extra"] = " ".join(tokens).lower()
        powers.append(power)

    if not powers:
        raise ValueError("nenhuma power indicada")
    return powers


def is_order_full() -> bool:
    return capture.is_pixel_color(2612, 1408, "#214167")


def get_berries_count() -> int:
    def _getX(col):
        return 1604 + col * 144

    for i in reversed(range(0, 8)):
        if capture.is_pixel_color(_getX(i), 1408, "#214167"):
            return i + 1
    return 0


def read_powers() -> list[Power]:
    frame = capture.get_latest_frame()
    ocr_powers = []
    ocr_powers.append(
        ocr.read_field(frame, 496, 1646, 1488, 1726, config="--psm 7").removesuffix(
            "\n"
        )
    )
    ocr_powers.append(
        ocr.read_field(frame, 496, 1746, 1488, 1818, config="--psm 7").removesuffix(
            "\n"
        )
    )
    ocr_powers.append(
        ocr.read_field(frame, 496, 1846, 1488, 1918, config="--psm 7").removesuffix(
            "\n"
        )
    )

    powers = []

    for line in ocr_powers:
        print(f"{Fore.LIGHTBLACK_EX}OCR:{Style.RESET_ALL} {line}")

    for line in ocr_powers:
        # cada linha e uma power: fica so a melhor candidata, nao todas as que batem
        power_name = _match_power(line)
        if power_name is None:
            continue

        power: Power = {"power": power_name}
        level = _parse_level(line)
        if level is not None:
            power["level"] = level
        extra = _parse_extra(line)
        if extra is not None:
            power["extra"] = extra
        powers.append(power)

    return powers


def _format_power(power: Power) -> str:
    """Uma power numa linha: "item Lv.3 (berries)"."""
    parts = [power["power"]]
    if "level" in power:
        parts.append(f"Lv.{power['level']}")
    if "extra" in power:
        parts.append(f"({power['extra']})")
    return " ".join(parts)


def _print_powers(title: str, powers: list[Power], color: str) -> None:
    print(f"{color}{title}{Style.RESET_ALL}")
    if not powers:
        print(f"  {Fore.LIGHTBLACK_EX}(nenhuma){Style.RESET_ALL}")
        return
    for power in powers:
        print(f"  {color}-{Style.RESET_ALL} {_format_power(power)}")


def try_donut(recipe, powers: list[Power], count=1):
    initial_count = count
    while count > 0:

        reset_backup()
        teleport_to_hotel()
        go_to_donut()
        talk_to_ansha()
        is_recipe_good = make_donut(recipe)
        if not is_recipe_good:
            return False
        # notifier.send_message("Donut feito! Esperando resultado...")
        isValid, result_powers = check_donut(powers)
        if isValid:
            if count > 1:
                # Donut is valid and we have more donuts to make

                notifier_result = notifier.send_and_wait(
                    text=f"Donut encontrado! Faltam {count - 1} de {initial_count} donuts. Pretende continuar?",
                    console_prompt=f"{Fore.LIGHTGREEN_EX}Donut encontrado! Faltam {count - 1} de {initial_count} donuts. Pretende continuar? Y para sim, N para nao, ou R para tentar novamente.: {Style.RESET_ALL}",
                    actions=[
                        TelegramAction("Sim", "y"),
                        TelegramAction("Nao", "n"),
                        TelegramAction("Repetir", "r"),
                    ],
                    frame=capture.get_latest_frame(),
                    force_console_action_value=True,
                )

                if not notifier_result.action_value:
                    print(
                        f"{Fore.LIGHTRED_EX}Erro ao ler resposta A sair do programa...{Style.RESET_ALL}"
                    )
                    return

                if notifier_result.action_value.lower() == "y":
                    count -= 1
                    click("A", 2)
                    click("B", 2)
                    click("A", 1)
                    save_game()
                    continue
                elif notifier_result.action_value.lower() == "n":
                    print(
                        f"{Fore.LIGHTYELLOW_EX}A sair do programa...{Style.RESET_ALL}"
                    )
                    return
                elif notifier_result.action_value.lower() == "r":
                    print(
                        f"{Fore.LIGHTYELLOW_EX}A tentar outra vez sem guardar o jogo{Style.RESET_ALL}"
                    )
                    continue
                else:
                    print(
                        f"{Fore.LIGHTRED_EX}Erro ao ler resposta. A sair do programa...{Style.RESET_ALL}"
                    )
                    return
            else:
                notifier_result = notifier.send_and_wait(
                    text=f"Donut encontrado! Todos os donuts foram feitos. Confirme que o donut está correto para terminar.",
                    console_prompt=f"{Fore.LIGHTGREEN_EX}Donut encontrado! Todos os donuts foram feitos. Confirme que o donut está correto para terminar. Y se estiver certo, R para tentar novamente: {Style.RESET_ALL}",
                    actions=[
                        TelegramAction("Sim", "y"),
                        TelegramAction("Repetir", "r"),
                    ],
                    frame=capture.get_latest_frame(),
                    force_console_action_value=True,
                )
                if not notifier_result.action_value:
                    print(
                        f"{Fore.LIGHTRED_EX}Erro ao ler resposta A sair do programa...{Style.RESET_ALL}"
                    )
                    return

                if notifier_result.action_value.lower() == "y":
                    count -= 1
                    click("A", 2)
                    click("B", 2)
                    click("A", 1)
                    save_game()
                    continue
                elif notifier_result.action_value.lower() == "r":
                    print(
                        f"{Fore.LIGHTYELLOW_EX}A tentar outra vez sem guardar o jogo{Style.RESET_ALL}"
                    )
                    continue
                else:
                    print(
                        f"{Fore.LIGHTRED_EX}Erro ao ler resposta. A sair do programa...{Style.RESET_ALL}"
                    )
                    return

        else:
            print(f"{Fore.LIGHTRED_EX}Poderes do donut incorretos!{Style.RESET_ALL}\n")
            _print_powers("Expected:", powers, Fore.CYAN)
            _print_powers("Result:", result_powers, Fore.MAGENTA)
            print("\n")


def check_donut(expected_powers: list[Power]):
    powers = read_powers()
    isValid = True
    # check if all expected powers are present
    for expected_power in expected_powers:
        isValid = False
        for power in powers:
            if expected_power["power"] == power["power"]:
                isValid = True
        if not isValid:
            return isValid, powers

    for expected_power in expected_powers:
        for power in powers:
            if expected_power["power"] != power["power"]:
                continue
            # so se compara o que a receita pediu; o que ela nao pede nao invalida
            if "level" in expected_power and expected_power["level"] < power.get(
                "level", 3
            ):
                isValid = False
            if "extra" in expected_power and not (
                _same_extra(expected_power["extra"], power.get("extra"))
                or _same_extra("all types", power.get("extra"))
            ):
                isValid = False
    return isValid, powers


def validateDonut():
    return "TODO"


def reset_backup():
    print(f"{Fore.LIGHTBLUE_EX}Restarting game...{Style.RESET_ALL}")
    click("H", 0.5)
    click("Y")
    click("A", 1)
    click("A", 8)
    hold("4")
    hold("X")
    hold("B")
    time.sleep(0.5)
    release("4")
    release("X")
    release("B")
    time.sleep(0.5)
    click("A", 0.5, 7)
    time.sleep(5)
    print(f"{Fore.LIGHTBLUE_EX}Game restarted.{Style.RESET_ALL} Waiting for donut...\n")


def teleport_to_hotel():
    click("+", 1)
    set_left_stick(-1, 0)
    time.sleep(0.3)
    set_left_stick(0, 0)
    time.sleep(0.5)
    click("A", repeat=2)
    time.sleep(2)


def go_to_donut():
    time.sleep(3)
    set_left_stick(0, -1)
    click("B", 1)
    click("A", 2.5)
    click("B")
    time.sleep(1)
    set_left_stick(-1, 0)
    time.sleep(0.5)
    set_left_stick(0, 0)


def talk_to_ansha():
    click("A", 2)
    click("A")
    click("down", repeat=2)
    click("A")
    click("up")


def recipe_to_commands(recipe) -> list[dict]:
    """
    A receita vai ser algo como "roseli 2, chrati 2, etc". Isto usa uma vírgula para separar os ingredientes e um espaço para separar o nome da quantidade.
    a ordem das berries está em donuts_list.json . Apenas vão ser usadas hyper berries e começa sempre da ultima berry da lista.
    Eu preciso que ordenes as berries da receita de acordo com o inverso da ordem da lista e quantos clicks são necessários para cada uma. Há 2 tipos de click, os individiuais e os rápidos
    O cima e baixo movem 1 casa na lista. O esquerda e direita movem 9 casas na lista. A resposta deve ser uma lista de dicionários com a seguinte estrutura (este é para o exemplo acima):
    [
        {"berry": "roseli", "clicks_left": 0, "clicks_up": 0, "quantity": 2},
        {"berry": "charti", "clicks_left": 1, "clicks_up": -3, "quantity": 2}
    ]
    (roseli e a 66, charti a 60: 6 casas para tras -> 1 salto de 9 e 3 clicks de volta, 4 presses em vez de 6)
    Começa sempre na ultima berry da lista e só vai para a primeira se clicarmos down enquanto a ultima está selecionada. Isto nunca pode ser possivel, visto que manda para as berries normais, que eu não quero usar.
    """

    if isinstance(recipe, str):
        recipe = [part.strip() for part in recipe.split(",") if part.strip()]

    entries = []
    for item in recipe:
        name, quantity = item.strip().rsplit(" ", 1)
        name = name.strip().lower()
        if name not in HYPER_INDEX:
            raise ValueError(f"berry desconhecida na receita: {name!r}")
        entries.append((HYPER_INDEX[name], name, int(quantity)))

    # inverso da ordem da lista: da ultima berry para a primeira
    entries.sort(key=lambda e: e[0], reverse=True)

    commands: list[dict] = []
    cursor = LAST_INDEX
    for index, name, quantity in entries:
        distance = cursor - index  # >= 0: so andamos para tras, nunca passamos a ultima

        # o multiplo de 9 mais proximo, mesmo que passe o alvo: o resto volta atras
        clicks_left = (distance + 4) // 8
        commands.append(
            {
                "berry": name,
                "clicks_left": clicks_left,  # saltos rapidos, 8 casas cada
                # > 0 sobrou para tras (up), < 0 passou o alvo (down)
                "clicks_up": distance - 8 * clicks_left,
                "quantity": quantity,
            }
        )
        cursor = index

    return commands


def make_donut(recipe):
    commands = recipe_to_commands(recipe)
    for command in commands:
        click("left", 0.2, command["clicks_left"], 0.05)
        if command["clicks_up"] > 0:
            click("up", 0.2, command["clicks_up"], 0.05)
        else:
            click("down", 0.2, -command["clicks_up"], 0.05)
        time.sleep(0.1)
        click("A", 0.05, command["quantity"], 0.05)
    time.sleep(0.2)
    if not is_recipe_complete(commands):
        notifier_result = notifier.send_and_wait(
            text="Receita incompleta. Precisa de mais berries?",
            console_prompt="Precisa de mais berries? (y/n): ",
            frame=capture.get_latest_frame(),
            actions=[
                TelegramAction("Sim", "y"),
                TelegramAction("Nao", "n"),
            ],
            force_console_action_value=True,
        )
        if notifier_result.action_value == "y":
            reset_backup()
            exit()
        else:
            return False

    click("+", 1)
    click("A")
    click("A", 1)
    click("A", 4)
    click("A", 4)
    return True


def is_recipe_complete(commands):
    expected_berries_count = sum(command["quantity"] for command in commands)
    actual_berries_count = get_berries_count()

    if expected_berries_count != actual_berries_count:
        return False

    return True


def save_game():
    click("+", 1)
    set_left_stick(1, 0)
    time.sleep(0.3)
    set_left_stick(0, 0)
    time.sleep(0.5)
    click("A", repeat=2)
    time.sleep(2)

    set_left_stick(0, -1)
    click("B", 3)
    set_left_stick(0, 0)
    click("A", repeat=10)
    time.sleep(3)

    click("B", repeat=10)


if __name__ == "__main__":
    launch(main)
