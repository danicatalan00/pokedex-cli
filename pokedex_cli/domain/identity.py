"""Canonical identifiers shared by domain and adapter boundaries."""

from __future__ import annotations

import re
import unicodedata


def normalize_slug(value: str) -> str:
    """Return a lowercase ASCII slug with stable separator semantics."""
    prepared = value.strip().replace("♀", "-f").replace("♂", "-m")
    prepared = re.sub(r"['’´`]", "", prepared)
    ascii_value = unicodedata.normalize("NFKD", prepared).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
    if not slug:
        raise ValueError("El nombre no puede quedar vacío al normalizarlo.")
    return slug


def normalize_species(value: str) -> str:
    """Normalize a user-facing Pokémon name to its canonical species slug."""
    return normalize_slug(value)


def normalize_form(value: str | None) -> str:
    """Normalize a form name, treating an omitted value as the regular form."""
    if value is None or not value.strip():
        return "regular"
    return normalize_slug(value)
