"""Cache-aside species enrichment use case."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

SpeciesData = dict[str, Any]


class SpeciesCache(Protocol):
    def get(self, species: str, form: str) -> SpeciesData | None: ...

    def put(
        self,
        species: str,
        form: str,
        data: SpeciesData,
        fetched_at: str,
    ) -> None: ...


class SpeciesApi(Protocol):
    def fetch_species_data(self, species: str, form: str) -> SpeciesData | None: ...


class GetSpeciesData:
    def __init__(
        self,
        *,
        cache: SpeciesCache,
        api: SpeciesApi,
        clock: Callable[[], datetime],
    ) -> None:
        self._cache = cache
        self._api = api
        self._clock = clock

    def execute(self, species: str, form: str, *, refresh: bool = False) -> SpeciesData | None:
        if not refresh:
            cached = self._cache.get(species, form)
            if cached is not None:
                return cached
        fetched = self._api.fetch_species_data(species, form)
        if fetched is None:
            return None
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        fetched_at = now.astimezone(timezone.utc).isoformat()
        self._cache.put(species, form, fetched, fetched_at)
        return self._cache.get(species, form)
