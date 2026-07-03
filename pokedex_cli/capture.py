"""Probabilidad de captura. Emula la mecánica original (a mayor `capture_rate`
más fácil), pero es intencionadamente indulgente: siempre queda una opción
razonable de atrapar hasta a un legendario, y los Pokémon comunes caen casi
siempre. Un fallo no consume al Pokémon: se puede reintentar `pokedex capturar`."""

import random

MIN_CHANCE = 0.35   # ni el legendario más esquivo baja de aquí
MAX_CHANCE = 0.96   # ni el más común es 100% seguro: siempre hay tensión

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
    """Devuelve la probabilidad [MIN_CHANCE, MAX_CHANCE] de capturar."""
    if capture_rate is None:
        # Sin datos de PokeAPI: indulgente por defecto, algo más difícil si es
        # legendario/singular.
        base = 0.55 if (is_legendary or is_mythical) else 0.8
    else:
        # capture_rate va de 3 (legendarios) a 255 (los más fáciles).
        # La raíz cuadrada levanta la curva para no castigar de más.
        base = (capture_rate / 255) ** 0.5
    if is_legendary or is_mythical:
        base *= 0.9  # un pelín más de emoción con los grandes
    if shiny:
        base *= 0.9  # el shiny se lo piensa un poco más
    return max(MIN_CHANCE, min(MAX_CHANCE, base))


def roll_capture(chance: float, rng: random.Random | None = None) -> bool:
    r = rng or random
    return r.random() < chance
