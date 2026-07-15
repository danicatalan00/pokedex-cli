"""Lazy, idempotent backfill of individuality data for existing captures.

Migration 007 derives IVs and nature for every pre-existing capture right
away (it only needs the capture id and its capture timestamp). Gender and
ability additionally need species data (``gender_rate`` / ``abilities``) that
may not be cached yet, so those are filled here, once that data becomes
available, from ``cmd_vision``/``cmd_list``/``cmd_ranking``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from pokedex_cli.domain.individuality import derive_ability, derive_gender, derive_ivs_nature


@dataclass(frozen=True)
class IncompleteCapture:
    id: int
    caught_at: str
    needs_ivs: bool
    needs_gender: bool
    needs_ability: bool
    gender_rate: int | None
    abilities: Sequence[str]


@dataclass(frozen=True)
class BackfillResult:
    filled_ivs: int = 0
    filled_gender: int = 0
    filled_ability: int = 0


class IndividualityRepository(Protocol):
    def select_incomplete(self, connection: sqlite3.Connection) -> list[IncompleteCapture]: ...

    def update_ivs_and_nature(
        self,
        connection: sqlite3.Connection,
        capture_id: int,
        ivs: Mapping[str, int],
        nature: str,
    ) -> None: ...

    def update_gender(
        self, connection: sqlite3.Connection, capture_id: int, gender: str
    ) -> None: ...

    def update_ability(
        self, connection: sqlite3.Connection, capture_id: int, ability: str
    ) -> None: ...


class BackfillIndividuality:
    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        repository: IndividualityRepository,
    ) -> None:
        self._connection_factory = connection_factory
        self._repository = repository

    def execute(self) -> BackfillResult:
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            pending = self._repository.select_incomplete(connection)
            filled_ivs = filled_gender = filled_ability = 0
            for capture in pending:
                seed_material = f"{capture.id}:{capture.caught_at}"
                if capture.needs_ivs:
                    ivs, nature = derive_ivs_nature(seed_material)
                    self._repository.update_ivs_and_nature(connection, capture.id, ivs, nature.name)
                    filled_ivs += 1
                if capture.needs_gender:
                    gender = derive_gender(seed_material, capture.gender_rate)
                    if gender is not None:
                        self._repository.update_gender(connection, capture.id, gender)
                        filled_gender += 1
                if capture.needs_ability:
                    ability = derive_ability(seed_material, capture.abilities)
                    if ability is not None:
                        self._repository.update_ability(connection, capture.id, ability)
                        filled_ability += 1
            connection.commit()
            return BackfillResult(filled_ivs, filled_gender, filled_ability)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
