"""Read-only status of the current wild encounter against the Pokédex.

The encounter's own ``captured`` flag only tracks the individual waiting in
this terminal. For ``pokedex ver`` we want the Pokédex answer instead: is this
*species* already registered? A regional/mega form or a shiny is treated as a
distinct variant, so it only counts as captured when that exact variant is
registered — and is flagged otherwise."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pokedex_cli.domain.identity import normalize_form, normalize_species


class EncounterDexRepository(Protocol):
    def is_species_captured(self, species: str) -> bool: ...

    def is_variant_captured(self, species: str, form: str, shiny: bool) -> bool: ...


@dataclass(frozen=True)
class EncounterStatus:
    captured: bool
    # A shiny or non-regular form: worth flagging when it is still missing,
    # even if the base species is already in the Pokédex.
    special: bool


def is_special(form: str, shiny: bool) -> bool:
    """A variant worth distinguishing from the base species: shiny, or any
    non-regular form (regional, mega, gmax…)."""
    return shiny or normalize_form(form) != "regular"


class DescribeEncounter:
    def __init__(self, repository: EncounterDexRepository) -> None:
        self._repository = repository

    def execute(self, species: str, form: str, shiny: bool) -> EncounterStatus:
        canonical_species = normalize_species(species)
        canonical_form = normalize_form(form)
        special = is_special(canonical_form, shiny)
        if special:
            captured = self._repository.is_variant_captured(
                canonical_species, canonical_form, shiny
            )
        else:
            captured = self._repository.is_species_captured(canonical_species)
        return EncounterStatus(captured=captured, special=special)
