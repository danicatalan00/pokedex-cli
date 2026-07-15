"""Small typed domain models shared across boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Pokemon:
    species: str
    form: str = "regular"
    shiny: bool = False


@dataclass(frozen=True)
class Ball:
    slug: str
    name: str
    multiplier: float
    max_stock: int | None

    @property
    def unlimited(self) -> bool:
        return self.max_stock is None

    @property
    def guaranteed(self) -> bool:
        return self.multiplier >= 255


@dataclass(frozen=True)
class Encounter:
    pokemon: Pokemon
    seen_at: str
    captured: bool = False
    failed_capture_attempts: int = 0
    escape_after_attempts: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "species": self.pokemon.species,
            "form": self.pokemon.form,
            "shiny": self.pokemon.shiny,
            "seen_at": self.seen_at,
            "captured": self.captured,
            "failed_capture_attempts": self.failed_capture_attempts,
            "escape_after_attempts": self.escape_after_attempts,
        }

    @classmethod
    def from_dict(cls, raw: object) -> Encounter | None:
        if not isinstance(raw, dict):
            return None
        species = raw.get("species")
        form = raw.get("form")
        seen_at = raw.get("seen_at")
        if not isinstance(species, str) or not species:
            return None
        if not isinstance(form, str) or not form:
            return None
        if not isinstance(seen_at, str) or not seen_at:
            return None
        try:
            failed = max(0, int(raw.get("failed_capture_attempts") or 0))
            escape_raw = raw.get("escape_after_attempts")
            escape_after = int(escape_raw) if escape_raw is not None else None
        except (TypeError, ValueError):
            return None
        if escape_after is not None and escape_after <= 0:
            return None
        return cls(
            Pokemon(species, form, bool(raw.get("shiny", False))),
            seen_at,
            bool(raw.get("captured", False)),
            failed,
            escape_after,
        )


@dataclass(frozen=True)
class WorkCommit:
    oid: str
    additions: int
    deletions: int

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions
