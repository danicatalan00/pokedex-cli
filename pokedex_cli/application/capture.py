"""Atomic capture-attempt use case."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from pokedex_cli.domain.capture import catch_chance, escape_after_attempts
from pokedex_cli.domain.individuality import (
    gender_from_roll,
    roll_ability,
    roll_ivs,
    roll_nature,
)
from pokedex_cli.domain.progression import STARTING_LEVEL, experience_for_level

Inventory = dict[str, Any]
Encounter = dict[str, Any]
InventoryNormaliser = Callable[[object | None], Inventory]


class RandomSource(Protocol):
    def random(self) -> float: ...

    def randint(self, low: int, high: int) -> int: ...


class InventoryRepository(Protocol):
    def load_in_transaction(
        self, connection: sqlite3.Connection, normalise: InventoryNormaliser
    ) -> Inventory: ...

    def save_in_transaction(self, connection: sqlite3.Connection, inventory: Inventory) -> None: ...


class EncounterRepository(Protocol):
    def load_in_transaction(self, connection: sqlite3.Connection) -> Encounter | None: ...

    def save_in_transaction(self, connection: sqlite3.Connection, state: Encounter) -> None: ...

    def clear_in_transaction(self, connection: sqlite3.Connection) -> None: ...


class CaptureRepository(Protocol):
    def insert(
        self,
        connection: sqlite3.Connection,
        *,
        species: str,
        form: str,
        shiny: bool,
        caught_at: str,
        ball_slug: str,
        level: int,
        experience: int,
        ivs: Mapping[str, int],
        nature: str,
        gender: str | None,
        ability: str | None,
    ) -> int: ...


class CaptureStatus(str, Enum):
    NO_ENCOUNTER = "no_encounter"
    ALREADY_CAPTURED = "already_captured"
    NO_STOCK = "no_stock"
    CAUGHT = "caught"
    FAILED = "failed"
    FLED = "fled"


@dataclass(frozen=True)
class CaptureCommand:
    ball_slug: str
    ball_multiplier: float
    caught_at: str
    capture_rate: int | None
    speed: int | None
    is_legendary: bool
    is_mythical: bool
    growth_rate: str | None
    gender_rate: int | None = None
    abilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaptureResult:
    status: CaptureStatus
    chance: float = 0.0
    capture_id: int | None = None
    attempts: int = 0
    escape_after: int = 0


class CaptureEncounter:
    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        inventory_repository: InventoryRepository,
        encounter_repository: EncounterRepository,
        capture_repository: CaptureRepository,
        inventory_normaliser: InventoryNormaliser,
        random_source: RandomSource,
    ) -> None:
        self._connection_factory = connection_factory
        self._inventory_repository = inventory_repository
        self._encounter_repository = encounter_repository
        self._capture_repository = capture_repository
        self._inventory_normaliser = inventory_normaliser
        self._random = random_source

    def execute(self, command: CaptureCommand) -> CaptureResult:
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            inventory = self._inventory_repository.load_in_transaction(
                connection, self._inventory_normaliser
            )
            encounter = self._encounter_repository.load_in_transaction(connection)
            if encounter is None:
                connection.commit()
                return CaptureResult(CaptureStatus.NO_ENCOUNTER)
            if bool(encounter["captured"]):
                connection.commit()
                return CaptureResult(CaptureStatus.ALREADY_CAPTURED)

            if command.ball_slug != "pokeball":
                stock = max(0, int(inventory["balls"].get(command.ball_slug, 0)))
                if stock == 0:
                    connection.commit()
                    return CaptureResult(CaptureStatus.NO_STOCK)
                inventory["balls"][command.ball_slug] = stock - 1

            chance = catch_chance(
                command.capture_rate,
                is_legendary=command.is_legendary,
                is_mythical=command.is_mythical,
                shiny=bool(encounter["shiny"]),
                ball_multiplier=command.ball_multiplier,
            )
            caught = self._random.random() < chance
            self._inventory_repository.save_in_transaction(connection, inventory)
            if caught:
                encounter["captured"] = True
                self._encounter_repository.save_in_transaction(connection, encounter)
                # Fixed roll order (ivs, nature, gender, ability) so that
                # tests with a fake RNG are stable across changes here.
                ivs = roll_ivs(self._random)
                nature = roll_nature(self._random)
                gender = gender_from_roll(command.gender_rate, self._random.random())
                ability = roll_ability(command.abilities, self._random)
                level = 50 if command.is_legendary else STARTING_LEVEL
                capture_id = self._capture_repository.insert(
                    connection,
                    species=str(encounter["species"]),
                    form=str(encounter["form"]),
                    shiny=bool(encounter["shiny"]),
                    caught_at=command.caught_at,
                    ball_slug=command.ball_slug,
                    level=level,
                    experience=experience_for_level(command.growth_rate, level),
                    ivs=ivs,
                    nature=nature.name,
                    gender=gender,
                    ability=ability,
                )
                connection.commit()
                return CaptureResult(CaptureStatus.CAUGHT, chance=chance, capture_id=capture_id)

            escape_after = int(encounter.get("escape_after_attempts") or 0)
            if escape_after <= 0:
                escape_after = self._escape_after_attempts(command, encounter)
            attempts = int(encounter.get("failed_capture_attempts") or 0) + 1
            if attempts >= escape_after:
                self._encounter_repository.clear_in_transaction(connection)
                status = CaptureStatus.FLED
            else:
                encounter["failed_capture_attempts"] = attempts
                encounter["escape_after_attempts"] = escape_after
                self._encounter_repository.save_in_transaction(connection, encounter)
                status = CaptureStatus.FAILED
            connection.commit()
            return CaptureResult(
                status,
                chance=chance,
                attempts=attempts,
                escape_after=escape_after,
            )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _escape_after_attempts(self, command: CaptureCommand, encounter: Encounter) -> int:
        return escape_after_attempts(
            command.capture_rate,
            command.speed,
            command.is_legendary,
            command.is_mythical,
            bool(encounter["shiny"]),
            self._random,
        )
