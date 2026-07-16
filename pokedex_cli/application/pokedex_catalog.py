"""Read-only national Pokédex catalog: krabby's national list fused with the
player's local progress (sightings and captures), so the interactive TUI can
show every one of the ~1010 species as unseen / seen / captured without ever
touching PokeAPI or spawning `krabby` itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

UNSEEN = "unseen"
SEEN = "seen"
CAPTURED = "captured"


@dataclass(frozen=True)
class CatalogEntry:
    idx: int
    slug: str
    name: str
    gen: int
    status: str  # 'unseen' | 'seen' | 'captured'
    types: tuple[str, ...] | None
    captures_count: int
    max_level: int | None
    any_shiny: bool
    times_seen: int
    description: str | None


@dataclass(frozen=True)
class SightingAggregate:
    first_seen_at: str
    times_seen: int


@dataclass(frozen=True)
class CaptureAggregate:
    captures_count: int
    max_level: int
    any_shiny: bool


class KrabbyDatabase(Protocol):
    def load_database(self) -> list[dict[str, Any]] | None: ...


class SightingsSource(Protocol):
    def aggregated(self) -> dict[str, SightingAggregate]: ...


class CapturesSource(Protocol):
    def captures_aggregated(self) -> dict[str, CaptureAggregate]: ...


class SpeciesCacheSource(Protocol):
    def species_cache_by_species(self) -> dict[str, dict[str, Any]]: ...


def _fallback_name(slug: str) -> str:
    return slug.replace("-", " ").title()


class PokedexCatalog:
    """Fuses krabby's national species list with local sightings/captures.

    If the krabby database is unavailable, the catalog degrades to only the
    species the player has already seen or captured (assigned a synthetic,
    alphabetical index) instead of raising or fabricating unseen entries for
    a list it cannot know.
    """

    def __init__(
        self,
        *,
        krabby: KrabbyDatabase,
        sightings: SightingsSource,
        captures: CapturesSource,
        species_cache: SpeciesCacheSource,
    ) -> None:
        self._krabby = krabby
        self._sightings = sightings
        self._captures = captures
        self._species_cache = species_cache

    def execute(self) -> list[CatalogEntry]:
        sightings = self._sightings.aggregated()
        captures = self._captures.captures_aggregated()
        cache = self._species_cache.species_cache_by_species()
        database = self._krabby.load_database()

        if database:
            entries = [self._entry_from_krabby(raw, sightings, captures, cache) for raw in database]
        else:
            known = sorted(set(sightings) | set(captures))
            entries = [
                self._entry_without_krabby(index, slug, sightings, captures, cache)
                for index, slug in enumerate(known, start=1)
            ]
        entries.sort(key=lambda entry: entry.idx)
        return entries

    @staticmethod
    def _status_for(
        slug: str,
        sightings: dict[str, SightingAggregate],
        captures: dict[str, CaptureAggregate],
    ) -> str:
        if slug in captures:
            return CAPTURED
        if slug in sightings:
            return SEEN
        return UNSEEN

    def _entry_from_krabby(
        self,
        raw: dict[str, Any],
        sightings: dict[str, SightingAggregate],
        captures: dict[str, CaptureAggregate],
        cache: dict[str, dict[str, Any]],
    ) -> CatalogEntry:
        slug = str(raw.get("slug") or "")
        status = self._status_for(slug, sightings, captures)
        name_table = raw.get("name") or {}
        name = str(name_table.get("en") or _fallback_name(slug))
        description = self._description(status, slug, raw.get("desc") or {}, cache)
        return self._build_entry(
            idx=int(raw.get("idx") or 0),
            slug=slug,
            name=name,
            gen=int(raw.get("gen") or 0),
            status=status,
            sightings=sightings,
            captures=captures,
            cache=cache,
            description=description,
        )

    def _entry_without_krabby(
        self,
        index: int,
        slug: str,
        sightings: dict[str, SightingAggregate],
        captures: dict[str, CaptureAggregate],
        cache: dict[str, dict[str, Any]],
    ) -> CatalogEntry:
        status = self._status_for(slug, sightings, captures)
        description = self._description(status, slug, {}, cache)
        return self._build_entry(
            idx=index,
            slug=slug,
            name=_fallback_name(slug),
            gen=0,
            status=status,
            sightings=sightings,
            captures=captures,
            cache=cache,
            description=description,
        )

    @staticmethod
    def _description(
        status: str,
        slug: str,
        desc_table: dict[str, Any],
        cache: dict[str, dict[str, Any]],
    ) -> str | None:
        if status == UNSEEN:
            return None
        flavor_text = cache.get(slug, {}).get("flavor_text")
        if status == CAPTURED and flavor_text:
            return str(flavor_text)
        english = desc_table.get("en")
        return str(english) if english else None

    @staticmethod
    def _build_entry(
        *,
        idx: int,
        slug: str,
        name: str,
        gen: int,
        status: str,
        sightings: dict[str, SightingAggregate],
        captures: dict[str, CaptureAggregate],
        cache: dict[str, dict[str, Any]],
        description: str | None,
    ) -> CatalogEntry:
        capture_info = captures.get(slug)
        sighting_info = sightings.get(slug)
        species_types = cache.get(slug, {}).get("types")
        return CatalogEntry(
            idx=idx,
            slug=slug,
            name=name,
            gen=gen,
            status=status,
            types=tuple(species_types) if species_types else None,
            captures_count=capture_info.captures_count if capture_info else 0,
            max_level=capture_info.max_level if capture_info else None,
            any_shiny=capture_info.any_shiny if capture_info else False,
            times_seen=sighting_info.times_seen if sighting_info else 0,
            description=description,
        )
