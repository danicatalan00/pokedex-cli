"""Atomic team-management use case."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class TeamRepository(Protocol):
    def membership(self, connection: sqlite3.Connection, capture_id: int) -> bool | None: ...

    def count(self, connection: sqlite3.Connection) -> int: ...

    def set_membership(
        self, connection: sqlite3.Connection, capture_id: int, in_team: bool
    ) -> None: ...


class TeamAction(str, Enum):
    ADD = "add"
    REMOVE = "remove"


class TeamStatus(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    ALREADY_MEMBER = "already_member"
    ALREADY_REMOVED = "already_removed"
    NOT_FOUND = "not_found"
    FULL = "full"


@dataclass(frozen=True)
class TeamResult:
    status: TeamStatus
    capture_id: int


class ManageTeam:
    MAX_MEMBERS = 6

    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        repository: TeamRepository,
    ) -> None:
        self._connection_factory = connection_factory
        self._repository = repository

    def execute(self, action: TeamAction, capture_id: int) -> TeamResult:
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            membership = self._repository.membership(connection, capture_id)
            if membership is None:
                connection.commit()
                return TeamResult(TeamStatus.NOT_FOUND, capture_id)
            if action is TeamAction.ADD:
                if membership:
                    connection.commit()
                    return TeamResult(TeamStatus.ALREADY_MEMBER, capture_id)
                if self._repository.count(connection) >= self.MAX_MEMBERS:
                    connection.commit()
                    return TeamResult(TeamStatus.FULL, capture_id)
                self._repository.set_membership(connection, capture_id, True)
                status = TeamStatus.ADDED
            else:
                if not membership:
                    connection.commit()
                    return TeamResult(TeamStatus.ALREADY_REMOVED, capture_id)
                self._repository.set_membership(connection, capture_id, False)
                status = TeamStatus.REMOVED
            connection.commit()
            return TeamResult(status, capture_id)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
