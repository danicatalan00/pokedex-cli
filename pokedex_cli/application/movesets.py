"""Movesets de las capturas: el automático, el propio del jugador y su gestión.

Una captura sin moveset propio lucha con ``smart_moveset`` sobre lo que puede
aprender a su nivel; con moveset propio, con los movimientos que eligió el
jugador. Este módulo calcula los dos igual para el combate y para la ficha de
movimientos, y valida los cambios: entre uno y cuatro movimientos distintos,
aprendibles a su nivel (o ya conocidos), soportados por el motor y al menos uno
que haga daño, la misma regla que siguen los movesets automáticos.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from pokedex_cli.domain.battle.model import MAX_MOVES, LearnsetEntry, Move
from pokedex_cli.domain.battle.moves import TACKLE, is_supported
from pokedex_cli.domain.battle.movesets import fallback_moveset, learnable, smart_moveset
from pokedex_cli.domain.identity import normalize_slug

SpeciesData = dict[str, Any]
CaptureView = dict[str, Any]
LevelMoves = tuple[tuple[str, int], ...]
# Una captura sin tipos conocidos (sin datos de especie) lucha como Normal.
DEFAULT_TYPES: tuple[str, ...] = ("normal",)


class SpeciesSource(Protocol):
    def execute(self, species: str, form: str, *, refresh: bool = False) -> SpeciesData | None: ...


class MoveSource(Protocol):
    def execute_many(self, slugs: Iterable[str]) -> Mapping[str, Move]: ...


class CustomMovesets(Protocol):
    def get(self, capture_id: int) -> tuple[str, ...]: ...


class CustomMovesetStore(CustomMovesets, Protocol):
    def replace(self, capture_id: int, slugs: Sequence[str]) -> None: ...


class CaptureLookup(Protocol):
    def resolve(self, identifier: str, form: str | None) -> CaptureView | None: ...


# --- Reglas compartidas con el combate ---------------------------------------


def species_with_learnset(source: SpeciesSource, species: str, form: str) -> SpeciesData | None:
    """Datos de la especie con learnset por nivel.

    ``level_moves`` NULL significa que el perfil se descargó antes de existir
    el combate: se refresca una vez. Sin conexión se conserva el perfil que
    había (sus stats siguen valiendo) y la captura usa la reserva por tipo.
    """
    data = source.execute(species, form)
    if data is None or data.get("level_moves") is not None:
        return data
    refreshed = source.execute(species, form, refresh=True)
    return refreshed if refreshed is not None else data


def decoded_list(value: object) -> list[Any]:
    """Lista guardada como JSON en la caché (o ya decodificada); vacía si no lo es."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return list(value) if isinstance(value, (list, tuple)) else []


def level_moves(data: Mapping[str, Any] | None) -> LevelMoves:
    """Pares ``(slug, nivel)`` del learnset cacheado, en su orden; nivel 0 → 1."""
    entries: list[tuple[str, int]] = []
    for item in decoded_list(None if data is None else data.get("level_moves")):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        slug, level = item
        if (
            isinstance(slug, str)
            and slug
            and isinstance(level, int)
            and not isinstance(level, bool)
        ):
            entries.append((slug, max(1, level)))
    return tuple(entries)


def species_types(data: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Tipos de la especie según la caché; vacío si no los tiene."""
    raw = decoded_list(None if data is None else data.get("types"))
    return tuple(str(item) for item in raw if isinstance(item, str) and item)


def species_name(species: str) -> str:
    """Nombre de una especie para el combate: ``mr-mime`` → ``Mr Mime``."""
    return species.replace("-", " ").title()


@dataclass(frozen=True)
class CaptureMoves:
    """Moveset con el que una captura entra en combate.

    ``learnset`` solo se rellena si el moveset es automático: con él, el
    motor recalcula los movimientos al subir de nivel.
    """

    moves: tuple[Move, ...]
    custom: bool
    learnset: tuple[LearnsetEntry, ...] = ()


def capture_moves(
    *,
    custom: Sequence[str],
    entries: Sequence[tuple[str, int]],
    level: int,
    types: Sequence[str],
    catalog: Mapping[str, Move],
) -> CaptureMoves:
    """Moveset propio (si lo hay) o el automático de ``smart_moveset``.

    Sin datos de ningún movimiento queda la reserva por tipo. Un moveset
    propio sin nada que haga daño (datos perdidos sin conexión) recibe
    Placaje delante para no quedarse sin forma de atacar.
    """
    if custom:
        chosen = [catalog[slug] for slug in custom if slug in catalog]
        return CaptureMoves(_with_damaging_move(chosen, types), custom=True)
    learnset = tuple(
        LearnsetEntry(level=learned_at, move=catalog[slug])
        for slug, learned_at in entries
        if slug in catalog
    )
    candidates = [catalog[slug] for slug in learnable(entries, level) if slug in catalog]
    moves = smart_moveset(candidates, types) if candidates else fallback_moveset(types)
    return CaptureMoves(moves, custom=False, learnset=learnset)


def _with_damaging_move(moves: Sequence[Move], types: Sequence[str]) -> tuple[Move, ...]:
    if not moves:
        return fallback_moveset(types)
    if any(move.is_damaging for move in moves):
        return tuple(moves[:MAX_MOVES])
    return (TACKLE, *moves[: MAX_MOVES - 1])


# --- Gestión del moveset propio ----------------------------------------------


class MovesetStatus(str, Enum):
    SAVED = "saved"
    RESET = "reset"
    NOT_FOUND = "not_found"
    WRONG_COUNT = "wrong_count"
    NOT_LEARNABLE = "not_learnable"
    REPEATED = "repeated"
    NO_MOVE_DATA = "no_move_data"
    UNSUPPORTED = "unsupported"
    NO_DAMAGING_MOVE = "no_damaging_move"


@dataclass(frozen=True)
class MovesetResult:
    """Resultado de cambiar el moveset: estado, motivo en español si falla y
    los movimientos guardados si sale bien."""

    status: MovesetStatus
    reason: str | None = None
    moves: tuple[Move, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status in (MovesetStatus.SAVED, MovesetStatus.RESET)


@dataclass(frozen=True)
class LearnableMove:
    """Un movimiento del learnset: slug, nivel al que se aprende y sus datos
    (``None`` si no se han podido descargar)."""

    slug: str
    level: int
    move: Move | None

    @property
    def supported(self) -> bool:
        return self.move is not None and is_supported(self.move)


@dataclass(frozen=True)
class MovesetView:
    capture_id: int
    name: str
    species: str
    form: str
    level: int
    types: tuple[str, ...]
    custom: bool
    moves: tuple[Move, ...]
    # Aprendibles hasta su nivel (en orden de aprendizaje) y los próximos.
    learnable: tuple[LearnableMove, ...]
    upcoming: tuple[LearnableMove, ...]


@dataclass(frozen=True)
class _CaptureContext:
    capture_id: int
    name: str
    species: str
    form: str
    level: int
    types: tuple[str, ...]
    entries: LevelMoves
    catalog: Mapping[str, Move]
    current: CaptureMoves


class ManageMoveset:
    """Ver, fijar o quitar el moveset propio de una captura."""

    def __init__(
        self,
        *,
        captures: CaptureLookup,
        species: SpeciesSource,
        moves: MoveSource,
        movesets: CustomMovesetStore,
    ) -> None:
        self._captures = captures
        self._species = species
        self._moves = moves
        self._movesets = movesets

    def view(self, identifier: str) -> MovesetView | None:
        """Moveset actual de la captura (por id o por especie) y su learnset."""
        row = self._captures.resolve(identifier, None)
        if row is None:
            return None
        context = self._context(row)
        reached = learnable(context.entries, context.level)
        # Como en ``learnable``, un movimiento repetido cuenta en su última
        # aparición alcanzada: la de más nivel.
        learned_at: dict[str, int] = {}
        for slug, level in context.entries:
            if level <= context.level:
                learned_at[slug] = max(level, learned_at.get(slug, level))
        upcoming: dict[str, int] = {}
        for slug, level in sorted(context.entries, key=lambda entry: entry[1]):
            if level > context.level and slug not in learned_at:
                upcoming.setdefault(slug, level)
        return MovesetView(
            capture_id=context.capture_id,
            name=context.name,
            species=context.species,
            form=context.form,
            level=context.level,
            types=context.types,
            custom=context.current.custom,
            moves=context.current.moves,
            learnable=tuple(
                LearnableMove(slug, learned_at[slug], context.catalog.get(slug)) for slug in reached
            ),
            upcoming=tuple(
                LearnableMove(slug, level, context.catalog.get(slug))
                for slug, level in upcoming.items()
            ),
        )

    def set(self, capture_id: int, slugs: Sequence[str]) -> MovesetResult:
        """Fija un moveset propio de 1 a 4 movimientos distintos.

        Cada movimiento se puede dar por slug o por su nombre en español
        («Impactrueno», «pistola agua»). Vale lo aprendible a su nivel y lo
        que ya lleva ahora. Si algo no cuadra no cambia nada y devuelve el
        motivo.
        """
        row = self._captures.resolve(str(capture_id), None)
        if row is None:
            return MovesetResult(
                MovesetStatus.NOT_FOUND, f"No tienes ninguna captura #{capture_id}."
            )
        tokens = [token.strip() for token in slugs if token.strip()]
        if not 1 <= len(tokens) <= MAX_MOVES:
            return MovesetResult(
                MovesetStatus.WRONG_COUNT, f"Elige entre 1 y {MAX_MOVES} movimientos."
            )
        context = self._context(row)
        allowed = [
            *learnable(context.entries, context.level),
            *(move.slug for move in context.current.moves),
        ]
        # Lo que ya lleva puede no estar en la caché (Placaje de reserva).
        known = {**{move.slug: move for move in context.current.moves}, **context.catalog}
        chosen: list[str] = []
        for token in tokens:
            slug = _match_move(token, allowed, known)
            if slug is None:
                return MovesetResult(
                    MovesetStatus.NOT_LEARNABLE,
                    f"{context.name} no puede aprender «{token}» a su nivel.",
                )
            chosen.append(slug)
        if len(set(chosen)) != len(chosen):
            return MovesetResult(MovesetStatus.REPEATED, "No repitas movimientos.")
        moves: list[Move] = []
        for slug in chosen:
            move = known.get(slug)
            if move is None:
                return MovesetResult(
                    MovesetStatus.NO_MOVE_DATA,
                    f"No hay datos de «{slug}»; prueba de nuevo con conexión.",
                )
            if not is_supported(move):
                return MovesetResult(
                    MovesetStatus.UNSUPPORTED, f"{move.name} aún no funciona en combate."
                )
            moves.append(move)
        if not any(move.is_damaging for move in moves):
            return MovesetResult(
                MovesetStatus.NO_DAMAGING_MOVE, "Elige al menos un movimiento que haga daño."
            )
        self._movesets.replace(context.capture_id, chosen)
        return MovesetResult(MovesetStatus.SAVED, moves=tuple(moves))

    def reset(self, capture_id: int) -> MovesetResult:
        """Vuelve al moveset automático (borra el propio)."""
        if self._captures.resolve(str(capture_id), None) is None:
            return MovesetResult(
                MovesetStatus.NOT_FOUND, f"No tienes ninguna captura #{capture_id}."
            )
        self._movesets.replace(capture_id, ())
        return MovesetResult(MovesetStatus.RESET)

    def _context(self, row: Mapping[str, Any]) -> _CaptureContext:
        capture_id = int(row["id"])
        species, form = str(row["species"]), str(row["form"])
        data = species_with_learnset(self._species, species, form)
        entries = level_moves(data)
        level = int(row["level"])
        types = species_types(data) or _row_types(row) or DEFAULT_TYPES
        custom = self._movesets.get(capture_id)
        catalog = self._moves.execute_many([*custom, *(slug for slug, _ in entries)])
        current = capture_moves(
            custom=custom, entries=entries, level=level, types=types, catalog=catalog
        )
        nickname = row.get("nickname")
        name = str(nickname) if nickname else species_name(species)
        return _CaptureContext(
            capture_id, name, species, form, level, types, entries, catalog, current
        )


def _row_types(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(item) for item in decoded_list(row.get("types")) if item)


def _match_move(token: str, allowed: Sequence[str], catalog: Mapping[str, Move]) -> str | None:
    """Slug permitido que corresponde a ``token`` (slug o nombre, sin tildes)."""
    if token in allowed:
        return token
    try:
        wanted = normalize_slug(token)
    except ValueError:
        return None
    for slug in allowed:
        move = catalog.get(slug)
        names = [slug] if move is None else [slug, move.name]
        if any(_comparable(name) == wanted for name in names):
            return slug
    return None


def _comparable(name: str) -> str | None:
    try:
        return normalize_slug(name)
    except ValueError:
        return None
