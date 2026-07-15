"""Compatibility exports for capture rules and interactive flavour."""

from pokedex_cli.domain.capture import catch_chance, escape_after_attempts, roll_capture
from pokedex_cli.presentation.game_text import (
    LEGENDARY_SLUGS,
    breakout_message,
    random_legendary,
)

__all__ = [
    "LEGENDARY_SLUGS",
    "breakout_message",
    "catch_chance",
    "escape_after_attempts",
    "random_legendary",
    "roll_capture",
]
