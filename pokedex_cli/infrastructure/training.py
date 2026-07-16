"""SQLite training persistence and evolution queueing."""

from __future__ import annotations

import json
import random
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pokedex_cli.domain.evolutions import EvolutionOption, select_level_evolution
from pokedex_cli.domain.progression import (
    DEFAULT_BASE_EXPERIENCE,
    MAX_LEVEL,
    STARTING_LEVEL,
    commit_experience,
    experience_for_level,
    level_for_experience,
)
from pokedex_cli.domain.progression import (
    commit_difficulty as commit_difficulty,
)


@dataclass(frozen=True)
class TrainingResult:
    capture_id: int
    species: str
    experience: int
    old_level: int
    new_level: int
    changed_lines: int = 0


Choice = TypeVar("Choice")


class ChoiceSource(Protocol):
    def choice(self, sequence: Sequence[Choice]) -> Choice: ...


class RowData(Protocol):
    def __getitem__(self, key: str) -> Any: ...


def _cache_progression(
    conn: sqlite3.Connection, row: RowData
) -> tuple[str, int, list[dict[str, Any]]]:
    cache = conn.execute(
        "SELECT growth_rate, base_experience, level_evolutions "
        "FROM species_cache WHERE species = ? AND form = ?",
        (row["species"], row["form"]),
    ).fetchone()
    if cache is None:
        return "medium", DEFAULT_BASE_EXPERIENCE, []
    try:
        evolutions = json.loads(cache["level_evolutions"] or "[]")
    except (TypeError, json.JSONDecodeError):
        evolutions = []
    return (
        cache["growth_rate"] or "medium",
        cache["base_experience"] or DEFAULT_BASE_EXPERIENCE,
        evolutions if isinstance(evolutions, list) else [],
    )


def _queue_evolution(
    conn: sqlite3.Connection,
    row: RowData,
    evolutions: list[dict[str, Any]],
    rng: ChoiceSource,
) -> None:
    if row["pending_evolution_species"]:
        return
    options: list[EvolutionOption] = []
    for option in evolutions:
        try:
            species = str(option["species"])
            min_level = int(option["min_level"])
        except (KeyError, TypeError, ValueError):
            continue
        options.append(EvolutionOption(species, str(option.get("form") or "regular"), min_level))
    chosen = select_level_evolution(options, int(row["level"]), rng)
    if chosen is None:
        return
    conn.execute(
        "UPDATE captures SET pending_evolution_species = ?, "
        "pending_evolution_form = ? WHERE id = ?",
        (chosen.species, chosen.form, row["id"]),
    )


def queue_current_evolution(
    conn: sqlite3.Connection, capture_id: int, rng: ChoiceSource | None = None
) -> None:
    """Reevalua la especie actual, util tras completar una evolucion tardia."""
    row = conn.execute("SELECT * FROM captures WHERE id = ?", (capture_id,)).fetchone()
    if row is None:
        return
    _, _, evolutions = _cache_progression(conn, row)
    _queue_evolution(conn, row, evolutions, rng or random)
    conn.commit()


def apply_commit_experience(
    conn: sqlite3.Connection,
    commits: int | Sequence[object],
    rng: ChoiceSource | None = None,
) -> tuple[TrainingResult, ...]:
    """Reparte cada commit al azar entre Pokemon del equipo que no sean nivel 100."""
    rng = rng or random
    totals: dict[int, list[Any]] = {}
    # Reconciliación idempotente: si el perfil de una especie evolucionada se
    # descargó después de la animación anterior, su siguiente evolución se
    # recupera incluso cuando no hay commits nuevos en este arranque.
    current_team = conn.execute("SELECT * FROM captures WHERE in_team = 1 ORDER BY id").fetchall()
    for member in current_team:
        _, _, evolutions = _cache_progression(conn, member)
        _queue_evolution(conn, member, evolutions, rng)
    if isinstance(commits, int):
        workloads = [0] * max(0, commits)
    else:
        workloads = [
            max(0, int(getattr(commit, "additions", 0)))
            + max(0, int(getattr(commit, "deletions", 0)))
            for commit in commits
        ]
    for changed_lines in workloads:
        team = conn.execute(
            "SELECT * FROM captures WHERE in_team = 1 AND level < ? ORDER BY id",
            (MAX_LEVEL,),
        ).fetchall()
        if not team:
            break
        row = rng.choice(team)
        growth_rate, base_experience, evolutions = _cache_progression(conn, row)
        level = max(STARTING_LEVEL, int(row["level"] or STARTING_LEVEL))
        current_exp = max(int(row["experience"] or 0), experience_for_level(growth_rate, level))
        gained = commit_experience(level, base_experience, changed_lines)
        maximum = experience_for_level(growth_rate, MAX_LEVEL)
        new_exp = min(maximum, current_exp + gained)
        new_level = level_for_experience(growth_rate, new_exp)
        conn.execute(
            "UPDATE captures SET experience = ?, level = ? WHERE id = ?",
            (new_exp, new_level, row["id"]),
        )
        updated = dict(row)
        updated.update(experience=new_exp, level=new_level)
        _queue_evolution(conn, updated, evolutions, rng)
        aggregate = totals.setdefault(row["id"], [row["species"], 0, level, level, 0])
        aggregate[1] = int(aggregate[1]) + (new_exp - current_exp)
        aggregate[3] = new_level
        aggregate[4] = int(aggregate[4]) + changed_lines
    conn.commit()
    return tuple(
        TrainingResult(
            capture_id,
            str(values[0]),
            int(values[1]),
            int(values[2]),
            int(values[3]),
            int(values[4]),
        )
        for capture_id, values in totals.items()
    )
