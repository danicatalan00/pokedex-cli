"""Pure experience, level, and training-difficulty rules."""

from __future__ import annotations

STARTING_LEVEL = 5
MAX_LEVEL = 100
DEFAULT_BASE_EXPERIENCE = 64
LINES_PER_DIFFICULTY = 50
MAX_COMMIT_DIFFICULTY = 50.0


def experience_for_level(growth_rate: str | None, level: int) -> int:
    """Return official cumulative experience for a level in the range 1..100."""
    n = max(1, min(MAX_LEVEL, int(level)))
    rate = (growth_rate or "medium").lower()
    if rate in {"medium", "medium-fast"}:
        value = n**3
    elif rate == "fast":
        value = 4 * n**3 // 5
    elif rate == "slow":
        value = 5 * n**3 // 4
    elif rate == "medium-slow":
        value = 6 * n**3 // 5 - 15 * n**2 + 100 * n - 140
    elif rate == "erratic":
        if n <= 50:
            value = n**3 * (100 - n) // 50
        elif n <= 68:
            value = n**3 * (150 - n) // 100
        elif n <= 98:
            value = n**3 * ((1911 - 10 * n) // 3) // 500
        else:
            value = n**3 * (160 - n) // 100
    elif rate == "fluctuating":
        if n <= 15:
            value = n**3 * ((n + 1) // 3 + 24) // 50
        elif n <= 36:
            value = n**3 * (n + 14) // 50
        else:
            value = n**3 * (n // 2 + 32) // 50
    else:
        value = n**3
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


def commit_experience(level: int, base_experience: int | None, changed_lines: int = 0) -> int:
    """Return experience earned from one first-generation-style battle."""
    base = max(1, int(base_experience or DEFAULT_BASE_EXPERIENCE))
    battle_exp = max(1, base * max(1, int(level)) // 7)
    return max(1, int(battle_exp * commit_difficulty(changed_lines)))
