"""Pure Gen 3 individuality rules: natures, IVs, gender, ability and stats.

The project's maximum canon is Gen 1-3, so this module intentionally omits
later mechanics: no hidden abilities (Gen 5), no EVs from battling (there are
no battles here — training raises EXP, not EVs, so EV is always 0), no
natures affecting anything but the four non-HP stats.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

STAT_KEYS: tuple[str, ...] = ("hp", "atk", "def", "spa", "spd", "spe")


@dataclass(frozen=True)
class Nature:
    index: int
    name: str
    name_es: str
    plus: str | None
    minus: str | None


NATURES: tuple[Nature, ...] = (
    Nature(0, "hardy", "Fuerte", None, None),
    Nature(1, "lonely", "Huraña", "atk", "def"),
    Nature(2, "brave", "Audaz", "atk", "spe"),
    Nature(3, "adamant", "Firme", "atk", "spa"),
    Nature(4, "naughty", "Pícara", "atk", "spd"),
    Nature(5, "bold", "Osada", "def", "atk"),
    Nature(6, "docile", "Dócil", None, None),
    Nature(7, "relaxed", "Relajada", "def", "spe"),
    Nature(8, "impish", "Agitada", "def", "spa"),
    Nature(9, "lax", "Descuidada", "def", "spd"),
    Nature(10, "timid", "Miedosa", "spe", "atk"),
    Nature(11, "hasty", "Activa", "spe", "def"),
    Nature(12, "serious", "Seria", None, None),
    Nature(13, "jolly", "Alegre", "spe", "spa"),
    Nature(14, "naive", "Ingenua", "spe", "spd"),
    Nature(15, "modest", "Modesta", "spa", "atk"),
    Nature(16, "mild", "Cordial", "spa", "def"),
    Nature(17, "quiet", "Apacible", "spa", "spe"),
    Nature(18, "bashful", "Tímida", None, None),
    Nature(19, "rash", "Alocada", "spa", "spd"),
    Nature(20, "calm", "Serena", "spd", "atk"),
    Nature(21, "gentle", "Amable", "spd", "def"),
    Nature(22, "sassy", "Grosera", "spd", "spe"),
    Nature(23, "careful", "Cauta", "spd", "spa"),
    Nature(24, "quirky", "Extravagante", None, None),
)

_NATURES_BY_NAME: dict[str, Nature] = {nature.name: nature for nature in NATURES}


def nature_by_name(name: str | None) -> Nature | None:
    if name is None:
        return None
    return _NATURES_BY_NAME.get(name.strip().lower())


def apply_nature(value: int, nature: Nature, key: str) -> int:
    if nature.plus == key:
        return value * 110 // 100
    if nature.minus == key:
        return value * 90 // 100
    return value


def hp_stat(base: int, iv: int, level: int, ev: int = 0) -> int:
    if base == 1:
        return 1
    return (2 * base + iv + ev // 4) * level // 100 + level + 10


def other_stat(base: int, iv: int, level: int, nature: Nature, key: str, ev: int = 0) -> int:
    raw = (2 * base + iv + ev // 4) * level // 100 + 5
    return apply_nature(raw, nature, key)


def compute_stats(
    bases: Mapping[str, int | None],
    ivs: Mapping[str, int],
    level: int,
    nature: Nature,
) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for key in STAT_KEYS:
        base = bases.get(key)
        if base is None:
            result[key] = None
        elif key == "hp":
            result[key] = hp_stat(base, ivs[key], level)
        else:
            result[key] = other_stat(base, ivs[key], level, nature, key)
    return result


def gender_from_roll(gender_rate: int | None, roll: float) -> str | None:
    if gender_rate is None:
        return None
    if gender_rate == -1:
        return "genderless"
    return "female" if roll < gender_rate / 8 else "male"


class RandomSource(Protocol):
    def random(self) -> float: ...

    def randint(self, low: int, high: int) -> int: ...


def roll_ivs(rng: RandomSource) -> dict[str, int]:
    return {key: rng.randint(0, 31) for key in STAT_KEYS}


def roll_nature(rng: RandomSource) -> Nature:
    return NATURES[rng.randint(0, 24)]


def roll_ability(abilities: Sequence[str], rng: RandomSource) -> str | None:
    if not abilities:
        return None
    if len(abilities) == 1:
        return abilities[0]
    return abilities[rng.randint(0, 1)]


def _digest(seed_material: str) -> bytes:
    return hashlib.sha256(seed_material.encode()).digest()


def derive_ivs_nature(seed_material: str) -> tuple[dict[str, int], Nature]:
    digest = _digest(seed_material)
    ivs = {key: digest[index] % 32 for index, key in enumerate(STAT_KEYS)}
    nature = NATURES[digest[6] % 25]
    return ivs, nature


def derive_gender(seed_material: str, gender_rate: int | None) -> str | None:
    digest = _digest(seed_material)
    return gender_from_roll(gender_rate, digest[7] / 256)


def derive_ability(seed_material: str, abilities: Sequence[str]) -> str | None:
    if not abilities:
        return None
    if len(abilities) == 1:
        return abilities[0]
    digest = _digest(seed_material)
    return abilities[digest[8] % 2]
