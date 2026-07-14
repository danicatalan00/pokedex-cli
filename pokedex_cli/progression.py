"""Niveles, experiencia por commits y evoluciones por nivel."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass


STARTING_LEVEL = 5
MAX_LEVEL = 100
# En Rojo/Azul: EXP = base_exp del rival * nivel del rival / 7. Cada commit
# representa un entrenamiento contra un rival equivalente al propio Pokemon.
DEFAULT_BASE_EXPERIENCE = 64
# Hasta 50 lineas, cada bloque completo equivale aproximadamente a otro
# combate. El techo impide que dependencias o codigo generado rompan la curva.
LINES_PER_DIFFICULTY = 50
MAX_COMMIT_DIFFICULTY = 50.0


@dataclass(frozen=True)
class TrainingResult:
    capture_id: int
    species: str
    experience: int
    old_level: int
    new_level: int
    changed_lines: int = 0


def experience_for_level(growth_rate: str | None, level: int) -> int:
    """Experiencia acumulada oficial para alcanzar ``level`` (1..100)."""
    n = max(1, min(MAX_LEVEL, int(level)))
    rate = (growth_rate or "medium").lower()
    if rate in {"medium", "medium-fast"}:
        value = n ** 3
    elif rate == "fast":
        value = 4 * n ** 3 // 5
    elif rate == "slow":
        value = 5 * n ** 3 // 4
    elif rate == "medium-slow":
        value = 6 * n ** 3 // 5 - 15 * n ** 2 + 100 * n - 140
    elif rate == "erratic":
        if n <= 50:
            value = n ** 3 * (100 - n) // 50
        elif n <= 68:
            value = n ** 3 * (150 - n) // 100
        elif n <= 98:
            value = n ** 3 * ((1911 - 10 * n) // 3) // 500
        else:
            value = n ** 3 * (160 - n) // 100
    elif rate == "fluctuating":
        if n <= 15:
            value = n ** 3 * ((n + 1) // 3 + 24) // 50
        elif n <= 36:
            value = n ** 3 * (n + 14) // 50
        else:
            value = n ** 3 * (n // 2 + 32) // 50
    else:
        value = n ** 3
    # Las tablas oficiales fijan nivel 1 en cero (tambien para medium-slow).
    return 0 if n == 1 else max(0, value)


def level_for_experience(growth_rate: str | None, experience: int) -> int:
    total = max(0, int(experience))
    level = 1
    for candidate in range(2, MAX_LEVEL + 1):
        if total < experience_for_level(growth_rate, candidate):
            break
        level = candidate
    return level


def commit_difficulty(changed_lines: int) -> float:
    return min(
        MAX_COMMIT_DIFFICULTY,
        1.0 + max(0, int(changed_lines)) / LINES_PER_DIFFICULTY,
    )


def commit_experience(level: int, base_experience: int | None,
                      changed_lines: int = 0) -> int:
    """EXP de un commit con la formula de combate de la primera generacion."""
    base = max(1, int(base_experience or DEFAULT_BASE_EXPERIENCE))
    battle_exp = max(1, base * max(1, int(level)) // 7)
    return max(1, int(battle_exp * commit_difficulty(changed_lines)))


def _cache_progression(conn, row) -> tuple[str, int, list[dict]]:
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


def _queue_evolution(conn, row, evolutions: list[dict], rng) -> None:
    if row["pending_evolution_species"]:
        return
    eligible = [
        option for option in evolutions
        if int(option.get("min_level") or MAX_LEVEL + 1) <= int(row["level"])
    ]
    if not eligible:
        return
    earliest = min(int(option["min_level"]) for option in eligible)
    choices = [option for option in eligible if int(option["min_level"]) == earliest]
    chosen = rng.choice(choices)
    conn.execute(
        "UPDATE captures SET pending_evolution_species = ?, "
        "pending_evolution_form = ? WHERE id = ?",
        (chosen["species"], chosen.get("form") or "regular", row["id"]),
    )


def queue_current_evolution(conn, capture_id: int, rng=None) -> None:
    """Reevalua la especie actual, util tras completar una evolucion tardia."""
    row = conn.execute("SELECT * FROM captures WHERE id = ?", (capture_id,)).fetchone()
    if row is None:
        return
    _, _, evolutions = _cache_progression(conn, row)
    _queue_evolution(conn, row, evolutions, rng or random)
    conn.commit()


def apply_commit_experience(conn, commits, rng=None) -> tuple[TrainingResult, ...]:
    """Reparte cada commit al azar entre Pokemon del equipo que no sean nivel 100."""
    rng = rng or random
    totals: dict[int, list[int | str]] = {}
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
        current_exp = max(
            int(row["experience"] or 0), experience_for_level(growth_rate, level)
        )
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
        aggregate = totals.setdefault(
            row["id"], [row["species"], 0, level, level, 0]
        )
        aggregate[1] = int(aggregate[1]) + (new_exp - current_exp)
        aggregate[3] = new_level
        aggregate[4] = int(aggregate[4]) + changed_lines
    conn.commit()
    return tuple(
        TrainingResult(capture_id, str(values[0]), int(values[1]),
                       int(values[2]), int(values[3]), int(values[4]))
        for capture_id, values in totals.items()
    )
