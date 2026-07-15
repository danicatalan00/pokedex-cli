"""Read-only collection queries returning presentation-ready dictionaries."""

from __future__ import annotations

from typing import Any, Protocol

from pokedex_cli.domain.progression import (
    MAX_LEVEL,
    STARTING_LEVEL,
    experience_for_level,
)

CaptureView = dict[str, Any]


class CollectionRepository(Protocol):
    def list_captures(self) -> list[CaptureView]: ...


class CollectionQueries:
    def __init__(self, repository: CollectionRepository) -> None:
        self._repository = repository

    def captures(self) -> list[CaptureView]:
        rows = self._repository.list_captures()
        for row in rows:
            level = max(STARTING_LEVEL, int(row.get("level") or STARTING_LEVEL))
            row["level"] = level
            row["is_max_level"] = level >= MAX_LEVEL
            if level < MAX_LEVEL:
                growth = str(row.get("growth_rate") or "medium")
                floor = experience_for_level(growth, level)
                ceiling = experience_for_level(growth, level + 1)
                current = max(floor, int(row.get("experience") or 0))
                row["experience_into_level"] = current - floor
                row["experience_for_next_level"] = ceiling - floor
            else:
                row["experience_into_level"] = 0
                row["experience_for_next_level"] = 0
        return rows

    def resolve(self, identifier: str, form: str | None) -> CaptureView | None:
        rows = self.captures()
        if identifier.isdigit():
            capture_id = int(identifier)
            return next((row for row in rows if int(row["id"]) == capture_id), None)
        species = identifier.lower()
        return next(
            (
                row
                for row in rows
                if row["species"] == species and (form is None or row["form"] == form)
            ),
            None,
        )

    def team(self) -> list[CaptureView]:
        return [row for row in self.captures() if bool(row["in_team"])]

    def available_for_team(self) -> list[CaptureView]:
        return [row for row in self.captures() if not bool(row["in_team"])]

    def type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.captures():
            for type_name in row["types"] or []:
                counts[str(type_name)] = counts.get(str(type_name), 0) + 1
        return counts

    def ranking(self) -> tuple[list[CaptureView], int]:
        ranked: list[CaptureView] = []
        missing = 0
        for source in self.captures():
            if source["types"] is None:
                missing += 1
                continue
            row = dict(source)
            row["total"] = sum(
                int(row.get(key) or 0) for key in ("hp", "atk", "def", "spa", "spd", "spe")
            )
            ranked.append(row)
        ranked.sort(key=lambda row: -int(row["total"]))
        return ranked, missing

    def rare(self) -> list[CaptureView]:
        return [
            row for row in self.captures() if bool(row["is_legendary"]) or bool(row["is_mythical"])
        ]
