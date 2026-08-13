"""Pure capture probability rules."""

from __future__ import annotations

import math
import random
from typing import Protocol


class RandomRange(Protocol):
    def randint(self, low: int, high: int) -> int: ...


class RandomFloat(Protocol):
    def random(self) -> float: ...


def _validated_multiplier(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number")
    multiplier = float(value)
    if not math.isfinite(multiplier) or multiplier < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return multiplier


def catch_chance(
    capture_rate: int | None,
    is_legendary: bool = False,
    is_mythical: bool = False,
    shiny: bool = False,
    ball_multiplier: float = 1.0,
    level: int = 5,
    mood_multiplier: float = 1.0,
) -> float:
    """Return a validated capture probability in the closed interval [0, 1].

    ``mood_multiplier`` carries the Safari state (rocks and bait). It is applied
    after the Master Ball shortcut on purpose: that ball never fails, whatever
    the Pokémon's mood.
    """
    del shiny  # Reserved for an explicit future rule; it has no effect today.
    multiplier = _validated_multiplier(ball_multiplier, "ball_multiplier")
    mood = _validated_multiplier(mood_multiplier, "mood_multiplier")

    if capture_rate is not None:
        if isinstance(capture_rate, bool) or not isinstance(capture_rate, int):
            raise TypeError("capture_rate must be an integer or None")
        if not 0 <= capture_rate <= 255:
            raise ValueError("capture_rate must be between 0 and 255")

    if multiplier >= 255:
        return 1.0
    if capture_rate is None:
        base = 0.55 if (is_legendary or is_mythical) else 0.8
    else:
        base = capture_rate / 255
    level_factor = math.sqrt(5 / max(1, int(level)))
    return max(0.0, min(1.0, base * multiplier * level_factor * mood))


def escape_after_attempts(
    capture_rate: int | None,
    speed: int | None = None,
    is_legendary: bool = False,
    is_mythical: bool = False,
    shiny: bool = False,
    rng: RandomRange | None = None,
) -> int:
    random_source = rng or random
    catch_pressure = 1 - catch_chance(capture_rate, is_legendary, is_mythical, shiny)
    speed_pressure = min(max((speed or 80) / 180, 0.0), 1.0)
    pressure = (catch_pressure * 0.65) + (speed_pressure * 0.35)
    if is_legendary or is_mythical:
        pressure = min(1.0, pressure + 0.15)
    if pressure >= 0.75:
        low, high = 2, 4
    elif pressure >= 0.45:
        low, high = 3, 5
    else:
        low, high = 4, 6
    if shiny:
        high += 1
    return random_source.randint(low, high)


def roll_capture(chance: float, rng: RandomFloat | None = None) -> bool:
    return (rng or random).random() < chance
