"""Random flavour used only by interactive demos and capture messages."""

from __future__ import annotations

import random

LEGENDARY_SLUGS = (
    "mewtwo",
    "mew",
    "articuno",
    "zapdos",
    "moltres",
    "lugia",
    "ho-oh",
    "celebi",
    "kyogre",
    "groudon",
    "rayquaza",
    "jirachi",
    "deoxys",
    "dialga",
    "palkia",
    "giratina",
    "arceus",
    "darkrai",
    "reshiram",
    "zekrom",
    "kyurem",
    "xerneas",
    "yveltal",
    "zygarde",
    "solgaleo",
    "lunala",
    "necrozma",
    "zacian",
    "zamazenta",
    "eternatus",
    "koraidon",
    "miraidon",
)


def random_legendary(rng: random.Random | None = None) -> str:
    return (rng or random).choice(LEGENDARY_SLUGS)


def breakout_message(rng: random.Random | None = None) -> str:
    return (rng or random).choice(
        (
            "¡Oh, no! ¡El Pokémon se ha escapado!",
            "¡Vaya! ¡Parecía que lo habías atrapado!",
            "¡Aaaah! ¡Casi lo consigues!",
            "¡Qué rabia! ¡Ha faltado muy poco!",
        )
    )
