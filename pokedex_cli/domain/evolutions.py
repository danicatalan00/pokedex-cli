"""Pure level-based evolution selection."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EvolutionOption:
    species: str
    form: str
    min_level: int


class RandomChoice(Protocol):
    def choice(self, options: Sequence[EvolutionOption]) -> EvolutionOption: ...


def select_level_evolution(
    options: Sequence[EvolutionOption], level: int, random_source: RandomChoice
) -> EvolutionOption | None:
    eligible = [option for option in options if option.min_level <= level]
    if not eligible:
        return None
    earliest = min(option.min_level for option in eligible)
    choices = [option for option in eligible if option.min_level == earliest]
    return random_source.choice(choices)
