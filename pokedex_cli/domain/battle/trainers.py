"""Entrenadores generados: clase, nombre y equipo a la altura del jugador.

Las clases son genéricas y originales, con sus tipos favoritos; los nombres
propios son españoles e inventados: ningún personaje de los juegos. El equipo
sale de la caché local de especies (``pool``), sin repetir especie, y los
niveles rondan la media del equipo del jugador.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar

from pokedex_cli.domain.battle.model import MAX_TEAM, RandomSource
from pokedex_cli.domain.progression import MAX_LEVEL

MIN_TRAINER_LEVEL = 2
# Desvío de nivel de cada miembro respecto a la media del jugador.
LEVEL_SPREAD = (-3, 1)
# Probabilidad de llevar un Pokémon menos que el jugador (si tiene más de uno).
SMALLER_TEAM_CHANCE = 0.5

_T = TypeVar("_T")


@dataclass(frozen=True)
class TrainerClass:
    """Clase de entrenador: título, tipos favoritos (vacío = cualquiera) y nombres."""

    title: str
    types: tuple[str, ...]
    names: tuple[str, ...]


@dataclass(frozen=True)
class SpeciesCandidate:
    species: str
    form: str
    types: tuple[str, ...]


@dataclass(frozen=True)
class TrainerPlan:
    name: str
    trainer_class: str
    members: tuple[tuple[str, str, int], ...]  # (especie, forma, nivel)

    @property
    def display_name(self) -> str:
        """«Clase Nombre», como se presenta en combate («Montañero Anselmo»)."""
        return f"{self.trainer_class} {self.name}"


_MEN = (
    "Anselmo",
    "Baltasar",
    "Celestino",
    "Dámaso",
    "Eusebio",
    "Fulgencio",
    "Gumersindo",
    "Hermenegildo",
    "Nicasio",
    "Rosendo",
    "Saturnino",
    "Wenceslao",
)
_WOMEN = (
    "Brígida",
    "Casilda",
    "Engracia",
    "Escolástica",
    "Eulalia",
    "Genoveva",
    "Herminia",
    "Leocadia",
    "Olvido",
    "Remedios",
    "Sagrario",
    "Visitación",
)

TRAINER_CLASSES: tuple[TrainerClass, ...] = (
    TrainerClass("Montañero", ("rock", "ground"), _MEN),
    TrainerClass("Nadadora", ("water",), _WOMEN),
    TrainerClass("Pescador", ("water",), _MEN),
    TrainerClass("Ornitólogo", ("flying",), _MEN),
    TrainerClass("Cazabichos", ("bug",), _MEN),
    TrainerClass("Karateka", ("fighting",), _MEN + _WOMEN),
    TrainerClass("Científica", ("electric", "steel", "poison"), _WOMEN),
    TrainerClass("Médium", ("ghost", "psychic"), _MEN + _WOMEN),
    TrainerClass("Domadora", ("dragon",), _WOMEN),
    TrainerClass("Jardinera", ("grass", "fairy"), _WOMEN),
    TrainerClass("Joven Guay", (), _MEN),
    TrainerClass("Chica Guay", (), _WOMEN),
)


def plan_trainer(
    pool: Sequence[SpeciesCandidate], player_levels: Sequence[int], rng: RandomSource
) -> TrainerPlan | None:
    """Planifica un entrenador rival con especies de ``pool``.

    - Tamaño: tantos como el equipo del jugador (1–6), a veces uno menos.
    - Nivel de cada miembro: media del jugador redondeada (mitades hacia
      arriba) + ``randint(-3, 1)``, acotado a [2, 100].
    - Clase al azar entre las que tienen alguna especie de sus tipos en el
      pool (o ninguna preferencia); sus especies favoritas van primero y el
      resto se completa con cualquiera, sin repetir especie.

    ``None`` si el pool o el equipo del jugador están vacíos. El orden del
    pool no influye: se ordena por especie y forma antes de tirar dados.
    """
    if not pool or not player_levels:
        return None
    candidates = sorted(pool, key=lambda candidate: (candidate.species, candidate.form))
    size = min(len(player_levels), MAX_TEAM)
    if size > 1 and rng.random() < SMALLER_TEAM_CHANCE:
        size -= 1
    eligible = [
        trainer_class
        for trainer_class in TRAINER_CLASSES
        if not trainer_class.types or _favorites(candidates, trainer_class)
    ]
    trainer_class = _pick(eligible, rng)
    name = _pick(trainer_class.names, rng)
    species = _choose_species(candidates, trainer_class, size, rng)
    average = _rounded_mean(player_levels)
    members = tuple(
        (candidate.species, candidate.form, _member_level(average, rng)) for candidate in species
    )
    return TrainerPlan(name=name, trainer_class=trainer_class.title, members=members)


def _favorites(
    candidates: Sequence[SpeciesCandidate], trainer_class: TrainerClass
) -> list[SpeciesCandidate]:
    return [c for c in candidates if any(own in trainer_class.types for own in c.types)]


def _choose_species(
    candidates: Sequence[SpeciesCandidate],
    trainer_class: TrainerClass,
    size: int,
    rng: RandomSource,
) -> list[SpeciesCandidate]:
    """Primero favoritas y luego cualquiera, sin repetir especie."""
    chosen: list[SpeciesCandidate] = []
    taken: set[str] = set()
    for group in (_favorites(candidates, trainer_class), list(candidates)):
        available = [c for c in group if c.species not in taken]
        while available and len(chosen) < size:
            candidate = available.pop(rng.randint(0, len(available) - 1))
            chosen.append(candidate)
            taken.add(candidate.species)
            available = [c for c in available if c.species not in taken]
    return chosen


def _rounded_mean(levels: Sequence[int]) -> int:
    # Redondeo escolar (las mitades hacia arriba) con aritmética entera.
    return (2 * sum(levels) + len(levels)) // (2 * len(levels))


def _member_level(average: int, rng: RandomSource) -> int:
    return max(MIN_TRAINER_LEVEL, min(MAX_LEVEL, average + rng.randint(*LEVEL_SPREAD)))


def _pick(items: Sequence[_T], rng: RandomSource) -> _T:
    return items[rng.randint(0, len(items) - 1)]
