"""Transactional processing of a preexisting evolution snapshot."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from pokedex_cli.domain.evolutions import (
    EvolutionOption,
    RandomChoice,
    select_level_evolution,
)


@dataclass(frozen=True)
class PendingEvolution:
    capture_id: int
    species: str
    form: str
    target_species: str
    target_form: str
    shiny: bool
    level: int


class EvolutionRepository(Protocol):
    def pending(
        self,
        connection: sqlite3.Connection,
        capture_ids: Sequence[int] | None = None,
    ) -> list[PendingEvolution]: ...

    def complete(self, connection: sqlite3.Connection, evolution: PendingEvolution) -> None: ...

    def options(
        self, connection: sqlite3.Connection, species: str, form: str
    ) -> list[EvolutionOption]: ...

    def queue(
        self,
        connection: sqlite3.Connection,
        capture_id: int,
        option: EvolutionOption,
    ) -> None: ...


class ProcessEvolutions:
    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        repository: EvolutionRepository,
        random_source: RandomChoice,
    ) -> None:
        self._connection_factory = connection_factory
        self._repository = repository
        self._random_source = random_source

    def pending(self) -> tuple[PendingEvolution, ...]:
        connection = self._connection_factory()
        try:
            return tuple(self._repository.pending(connection))
        finally:
            connection.close()

    def execute(self, capture_ids: Sequence[int]) -> tuple[PendingEvolution, ...]:
        if not capture_ids:
            return ()
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            pending = self._repository.pending(connection, capture_ids)
            for evolution in pending:
                self._repository.complete(connection, evolution)
                option = select_level_evolution(
                    self._repository.options(
                        connection,
                        evolution.target_species,
                        evolution.target_form,
                    ),
                    evolution.level,
                    self._random_source,
                )
                if option is not None:
                    self._repository.queue(connection, evolution.capture_id, option)
            connection.commit()
            return tuple(pending)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
