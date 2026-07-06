"""Probabilidad de captura y huida."""

import random

# Slugs de legendarios/singulares emblemáticos (para la demo `--legendary`).
LEGENDARY_SLUGS = [
    "mewtwo", "mew", "articuno", "zapdos", "moltres",
    "lugia", "ho-oh", "celebi",
    "kyogre", "groudon", "rayquaza", "jirachi", "deoxys",
    "dialga", "palkia", "giratina", "arceus", "darkrai",
    "reshiram", "zekrom", "kyurem",
    "xerneas", "yveltal", "zygarde",
    "solgaleo", "lunala", "necrozma",
    "zacian", "zamazenta", "eternatus",
    "koraidon", "miraidon",
]


def random_legendary(rng: random.Random | None = None) -> str:
    return (rng or random).choice(LEGENDARY_SLUGS)


def catch_chance(capture_rate: int | None, is_legendary: bool = False,
                 is_mythical: bool = False, shiny: bool = False) -> float:
    """Devuelve la probabilidad de capturar."""
    if capture_rate is None:
        # Sin datos de PokeAPI no hay capture_rate base que aplicar.
        base = 0.55 if (is_legendary or is_mythical) else 0.8
    else:
        # capture_rate va de 3 (legendarios) a 255 (los más fáciles).
        base = capture_rate / 255
    return max(0.0, min(1.0, base))


def escape_after_attempts(capture_rate: int | None, speed: int | None = None,
                          is_legendary: bool = False, is_mythical: bool = False,
                          shiny: bool = False, rng: random.Random | None = None) -> int:
    """Devuelve cuántos fallos aguanta antes de huir definitivamente."""
    r = rng or random
    catch_pressure = 1 - catch_chance(capture_rate, is_legendary, is_mythical, shiny)
    speed_pressure = min(max((speed or 80) / 180, 0.0), 1.0)
    pressure = (catch_pressure * 0.65) + (speed_pressure * 0.35)

    if is_legendary or is_mythical:
        pressure = min(1.0, pressure + 0.15)

    if pressure >= 0.75:
        low, high = 2, 4
    elif pressure >= 0.45:
        low, high = 3, 5
    else:
        low, high = 4, 6

    if shiny:
        high += 1

    return r.randint(low, high)


def roll_capture(chance: float, rng: random.Random | None = None) -> bool:
    r = rng or random
    return r.random() < chance
