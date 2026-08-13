"""Atomic Safari-item use case: one rock or one bait, one turn.

Rocas y cebos son ilimitados, así que aquí no hay inventario que consumir. Lo
que se gasta es el turno del encuentro, y eso vive en la misma fila que la
captura: por eso la acción se resuelve dentro de una única transacción, igual
que un lanzamiento de Pokeball.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from pokedex_cli.domain.capture import escape_after_attempts
from pokedex_cli.domain.safari import SafariAction, SafariMood, throw

Encounter = dict[str, Any]


class RandomSource(Protocol):
    def random(self) -> float: ...

    def randint(self, low: int, high: int) -> int: ...


class EncounterRepository(Protocol):
    def load_in_transaction(self, connection: sqlite3.Connection) -> Encounter | None: ...

    def save_in_transaction(self, connection: sqlite3.Connection, state: Encounter) -> None: ...

    def clear_in_transaction(self, connection: sqlite3.Connection) -> None: ...


class SafariStatus(str, Enum):
    NO_ENCOUNTER = "no_encounter"
    ALREADY_CAPTURED = "already_captured"
    APPLIED = "applied"
    FLED = "fled"


@dataclass(frozen=True)
class SafariCommand:
    action: SafariAction
    capture_rate: int | None = None
    speed: int | None = None
    is_legendary: bool = False
    is_mythical: bool = False


@dataclass(frozen=True)
class SafariResult:
    status: SafariStatus
    action: SafariAction | None = None
    catch_stage: int = 0
    patience: int = 0
    remaining_turns: int = 0
    stage_delta: int = 0
    patience_gained: int = 0
    mood: SafariMood = SafariMood.CALM


class ThrowSafariItem:
    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        encounter_repository: EncounterRepository,
        random_source: RandomSource,
    ) -> None:
        self._connection_factory = connection_factory
        self._encounter_repository = encounter_repository
        self._random = random_source

    def execute(self, command: SafariCommand) -> SafariResult:
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            encounter = self._encounter_repository.load_in_transaction(connection)
            if encounter is None:
                connection.commit()
                return SafariResult(SafariStatus.NO_ENCOUNTER)
            if bool(encounter["captured"]):
                connection.commit()
                return SafariResult(SafariStatus.ALREADY_CAPTURED)

            patience = self._patience(command, encounter)
            outcome = throw(
                command.action,
                catch_stage=int(encounter.get("catch_stage") or 0),
                patience=patience,
                spent_turns=self._spent_turns(encounter),
            )
            if outcome.fled:
                self._encounter_repository.clear_in_transaction(connection)
                status = SafariStatus.FLED
            else:
                encounter["catch_stage"] = outcome.catch_stage
                encounter["escape_after_attempts"] = outcome.patience
                encounter["item_turns"] = outcome.spent_turns - int(
                    encounter.get("failed_capture_attempts") or 0
                )
                self._encounter_repository.save_in_transaction(connection, encounter)
                status = SafariStatus.APPLIED
            connection.commit()
            return SafariResult(
                status,
                action=command.action,
                catch_stage=outcome.catch_stage,
                patience=outcome.patience,
                remaining_turns=outcome.remaining_turns,
                stage_delta=outcome.stage_delta,
                patience_gained=outcome.patience_gained,
                mood=outcome.mood,
            )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _spent_turns(encounter: Encounter) -> int:
        """Turnos consumidos: lanzamientos fallidos más rocas y cebos."""
        failed = max(0, int(encounter.get("failed_capture_attempts") or 0))
        items = max(0, int(encounter.get("item_turns") or 0))
        return failed + items

    def _patience(self, command: SafariCommand, encounter: Encounter) -> int:
        """La paciencia la fija el dominio de captura la primera vez que hace
        falta, sea por un lanzamiento fallido o por el primer ítem."""
        stored = encounter.get("escape_after_attempts")
        if stored:
            return int(stored)
        return escape_after_attempts(
            command.capture_rate,
            command.speed,
            command.is_legendary,
            command.is_mythical,
            bool(encounter["shiny"]),
            self._random,
        )
