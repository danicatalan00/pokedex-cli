"""Datos de movimientos de PokeAPI con caché local (cache-aside).

Igual que ``GetSpeciesData``: primero la caché y, para lo que falte, la API.
Un combate necesita decenas de movimientos a la vez, así que la caché se lee
de una sola consulta y las descargas pasan por un ``mapper`` inyectable que la
composición paraleliza. Un registro que el dominio no acepta no se guarda ni
se devuelve: el combate tirará de los movimientos de reserva.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Protocol

from pokedex_cli.domain.battle.model import Move
from pokedex_cli.domain.battle.moves import move_from_record

MoveRecord = dict[str, Any]
FetchMove = Callable[[str], MoveRecord | None]
# ``map`` o un equivalente paralelo: aplica la descarga a cada slug y devuelve
# los resultados en el mismo orden.
Mapper = Callable[[FetchMove, Sequence[str]], Iterable[MoveRecord | None]]


class MoveCache(Protocol):
    def get_many(self, slugs: Iterable[str]) -> Mapping[str, Mapping[str, Any]]: ...

    def put(self, slug: str, record: Mapping[str, Any], fetched_at: str) -> None: ...


class MoveApi(Protocol):
    def fetch_move_data(self, slug: str) -> MoveRecord | None: ...


class GetMoveData:
    def __init__(
        self,
        *,
        cache: MoveCache,
        api: MoveApi,
        clock: Callable[[], datetime],
        mapper: Mapper | None = None,
    ) -> None:
        self._cache = cache
        self._api = api
        self._clock = clock
        self._mapper: Mapper = mapper if mapper is not None else map

    def execute(self, slug: str) -> Move | None:
        return self.execute_many([slug]).get(slug)

    def execute_many(self, slugs: Iterable[str]) -> dict[str, Move]:
        """Los movimientos que se puedan conseguir, por slug y sin repetir.

        Lo que falta en la caché (o está guardado de forma ilegible) se pide a
        la API; lo que la API no da o el dominio rechaza se queda fuera.
        """
        wanted = list(dict.fromkeys(slug for slug in slugs if slug))
        if not wanted:
            return {}
        cached = self._cache.get_many(wanted)
        moves: dict[str, Move] = {}
        missing: list[str] = []
        for slug in wanted:
            move = _valid_move(slug, cached.get(slug))
            if move is None:
                missing.append(slug)
            else:
                moves[slug] = move
        if not missing:
            return moves
        fetched = list(self._mapper(self._api.fetch_move_data, missing))
        fetched_at = self._fetched_at()
        for slug, record in zip(missing, fetched):
            move = _valid_move(slug, record)
            if move is None or record is None:
                continue
            self._cache.put(slug, record, fetched_at)
            moves[slug] = move
        return moves

    def _fetched_at(self) -> str:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc).isoformat()


def _valid_move(slug: str, record: Mapping[str, Any] | None) -> Move | None:
    """El ``Move`` del registro si es válido y corresponde a ``slug``."""
    if record is None:
        return None
    move = move_from_record(record)
    # La clave de la caché es el slug: un registro de otro movimiento la
    # envenenaría (los learnsets citan por slug).
    if move is None or move.slug != slug:
        return None
    return move
