"""Codec del estado de combate: ``BattleState`` ↔ dict JSON-serializable.

El combate en curso se guarda entre turnos. ``state_to_dict`` produce solo
dicts, listas, cadenas, enteros, booleanos y ``None``; ``state_from_dict`` lo
valida de forma estricta y devuelve ``None`` ante cualquier dato corrupto, para
que la aplicación descarte el combate en lugar de fallar a mitad de un turno.
La ida y vuelta es exacta.

Cada movimiento distinto se guarda una sola vez en la tabla ``moves`` con el
formato de la caché (``move_to_record``) y los huecos y learnsets lo citan por
posición: el mismo movimiento se repite a menudo en varios Pokémon.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import TypeVar

from pokedex_cli.domain.battle.model import (
    MAX_MOVES,
    MAX_STAGE,
    MAX_TEAM,
    MIN_STAGE,
    STAGE_KEYS,
    STAT_KEYS,
    BattleKind,
    BattleState,
    Combatant,
    LearnsetEntry,
    Move,
    MoveSlot,
    Outcome,
    Phase,
    Side,
    Stages,
    Stats,
    Status,
    Volatile,
)
from pokedex_cli.domain.battle.moves import move_from_record, move_to_record
from pokedex_cli.domain.progression import MAX_LEVEL

SCHEMA_VERSION = 1

_EnumT = TypeVar("_EnumT", bound=Enum)


class _Corrupt(ValueError):
    """Un dato falta o tiene una forma, un tipo o un valor imposible."""


# --- Estado → dict ----------------------------------------------------------


class _MoveTable:
    """Tabla de movimientos distintos en orden de aparición."""

    def __init__(self) -> None:
        self.positions: dict[Move, int] = {}
        self.records: list[dict[str, object]] = []

    def ref(self, move: Move) -> int:
        position = self.positions.get(move)
        if position is None:
            position = len(self.records)
            self.positions[move] = position
            self.records.append(move_to_record(move))
        return position


def state_to_dict(state: BattleState) -> dict[str, object]:
    """Dict JSON-serializable con versión de esquema ``"v": 1``."""
    table = _MoveTable()
    player = _side_to_dict(state.player, table)
    opponent = _side_to_dict(state.opponent, table)
    return {
        "v": SCHEMA_VERSION,
        "kind": state.kind.value,
        "turn": state.turn,
        "phase": state.phase.value,
        "outcome": None if state.outcome is None else state.outcome.value,
        "escape_attempts": state.escape_attempts,
        "participants": [[foe, list(keys)] for foe, keys in state.participants],
        "award_experience": state.award_experience,
        "moves": table.records,
        "player": player,
        "opponent": opponent,
    }


def _side_to_dict(side: Side, table: _MoveTable) -> dict[str, object]:
    return {
        "team": [_combatant_to_dict(member, table) for member in side.team],
        "active": side.active,
        "reflect_turns": side.reflect_turns,
        "light_screen_turns": side.light_screen_turns,
        "trainer": side.trainer,
    }


def _combatant_to_dict(c: Combatant, table: _MoveTable) -> dict[str, object]:
    volatile = c.volatile
    return {
        "key": c.key,
        "species": c.species,
        "form": c.form,
        "name": c.name,
        "level": c.level,
        "types": list(c.types),
        "base_stats": c.base_stats.as_mapping(),
        "ivs": c.ivs.as_mapping(),
        "nature": c.nature,
        "stats": c.stats.as_mapping(),
        "hp": c.hp,
        "moves": [
            {"move": table.ref(slot.move), "pp": slot.pp, "max_pp": slot.max_pp} for slot in c.moves
        ],
        "shiny": c.shiny,
        "gender": c.gender,
        "ability": c.ability,
        "status": c.status.value,
        "status_turns": c.status_turns,
        "stages": {key: c.stages.value(key) for key in STAGE_KEYS},
        "volatile": {
            "confusion_turns": volatile.confusion_turns,
            "flinched": volatile.flinched,
            "protected": volatile.protected,
            "protect_streak": volatile.protect_streak,
            "charging": volatile.charging,
            "semi_invulnerable": volatile.semi_invulnerable,
            "recharging": volatile.recharging,
            "trapped_turns": volatile.trapped_turns,
            "trapped_by": volatile.trapped_by,
            "seeded": volatile.seeded,
            "focus_energy": volatile.focus_energy,
            "last_move": volatile.last_move,
        },
        "capture_id": c.capture_id,
        "base_experience": c.base_experience,
        "growth_rate": c.growth_rate,
        "experience": c.experience,
        "learnset": [[entry.level, table.ref(entry.move)] for entry in c.learnset],
        "custom_moves": c.custom_moves,
    }


# --- dict → estado ----------------------------------------------------------


def state_from_dict(raw: object) -> BattleState | None:
    """Reconstruye el estado; ``None`` si falta algo o algo es imposible.

    Además de tipos y rangos comprueba la coherencia básica: versión 1,
    claves de combatiente únicas, activos dentro del equipo, PS y PP dentro
    de su máximo, escalones en ±6, fase ``FINISHED`` si y solo si hay
    ``outcome`` y participantes que existen.
    """
    try:
        return _decode_state(raw)
    except _Corrupt:
        return None


def _decode_state(raw: object) -> BattleState:
    data = _mapping(raw)
    if _int(data, "v") != SCHEMA_VERSION:
        raise _Corrupt("v")
    moves = tuple(_decode_move(record) for record in _list(data, "moves"))
    player = _decode_side(_mapping(_get(data, "player")), moves)
    opponent = _decode_side(_mapping(_get(data, "opponent")), moves)
    phase = _enum(Phase, data, "phase")
    outcome_raw = _get(data, "outcome")
    outcome = None if outcome_raw is None else _enum_value(Outcome, outcome_raw)
    if (phase is Phase.FINISHED) != (outcome is not None):
        raise _Corrupt("outcome")
    player_keys = [member.key for member in player.team]
    opponent_keys = [member.key for member in opponent.team]
    if len(set(player_keys + opponent_keys)) != len(player_keys) + len(opponent_keys):
        raise _Corrupt("key")
    return BattleState(
        kind=_enum(BattleKind, data, "kind"),
        player=player,
        opponent=opponent,
        turn=_int(data, "turn", low=0),
        phase=phase,
        outcome=outcome,
        escape_attempts=_int(data, "escape_attempts", low=0),
        participants=_decode_participants(
            _get(data, "participants"), set(opponent_keys), set(player_keys)
        ),
        award_experience=_bool(data, "award_experience"),
    )


def _decode_move(record: object) -> Move:
    data = _mapping(record)
    move = move_from_record(data)
    # ``move_from_record`` tolera huecos (es el formato de la caché); aquí una
    # clave ausente, nula o distinta de su forma canónica es corrupción.
    if move is None or any(_get(data, key) != value for key, value in move_to_record(move).items()):
        raise _Corrupt("moves")
    return move


def _decode_participants(
    raw: object, opponent_keys: set[str], player_keys: set[str]
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    entries: list[tuple[str, tuple[str, ...]]] = []
    for item in _sequence(raw):
        pair = _sequence(item)
        if len(pair) != 2:
            raise _Corrupt("participants")
        foe = _text_value(pair[0])
        keys = tuple(_text_value(key) for key in _sequence(pair[1]))
        if foe not in opponent_keys or not set(keys) <= player_keys:
            raise _Corrupt("participants")
        if len(set(keys)) != len(keys) or any(foe == known for known, _ in entries):
            raise _Corrupt("participants")
        entries.append((foe, keys))
    return tuple(entries)


def _decode_side(data: Mapping[str, object], moves: Sequence[Move]) -> Side:
    team = tuple(_decode_combatant(_mapping(raw), moves) for raw in _list(data, "team"))
    if not 1 <= len(team) <= MAX_TEAM:
        raise _Corrupt("team")
    return Side(
        team=team,
        active=_int(data, "active", low=0, high=len(team) - 1),
        reflect_turns=_int(data, "reflect_turns", low=0),
        light_screen_turns=_int(data, "light_screen_turns", low=0),
        trainer=_optional_text(data, "trainer"),
    )


def _decode_combatant(data: Mapping[str, object], moves: Sequence[Move]) -> Combatant:
    stats = _decode_stats(_get(data, "stats"), low=1)
    slots = tuple(_decode_slot(raw, moves) for raw in _list(data, "moves"))
    if len(slots) > MAX_MOVES:
        raise _Corrupt("moves")
    learnset = tuple(_decode_entry(raw, moves) for raw in _list(data, "learnset"))
    return Combatant(
        key=_text(data, "key"),
        species=_text(data, "species"),
        form=_any_text(data, "form"),
        name=_text(data, "name"),
        level=_int(data, "level", low=1, high=MAX_LEVEL),
        types=tuple(_text_value(value) for value in _list(data, "types")),
        base_stats=_decode_stats(_get(data, "base_stats"), low=1),
        ivs=_decode_stats(_get(data, "ivs"), low=0),
        nature=_any_text(data, "nature"),
        stats=stats,
        hp=_int(data, "hp", low=0, high=stats.hp),
        moves=slots,
        shiny=_bool(data, "shiny"),
        gender=_optional_text(data, "gender"),
        ability=_optional_text(data, "ability"),
        status=_enum(Status, data, "status"),
        status_turns=_int(data, "status_turns", low=0),
        stages=_decode_stages(_mapping(_get(data, "stages"))),
        volatile=_decode_volatile(_mapping(_get(data, "volatile"))),
        capture_id=_optional_int(data, "capture_id", low=0),
        base_experience=_int(data, "base_experience", low=0),
        growth_rate=_any_text(data, "growth_rate"),
        experience=_int(data, "experience", low=0),
        learnset=learnset,
        custom_moves=_bool(data, "custom_moves"),
    )


def _decode_stats(raw: object, *, low: int) -> Stats:
    data = _mapping(raw)
    return Stats.from_mapping({key: _int(data, key, low=low) for key in STAT_KEYS})


def _decode_slot(raw: object, moves: Sequence[Move]) -> MoveSlot:
    data = _mapping(raw)
    max_pp = _int(data, "max_pp", low=1)
    return MoveSlot(
        move=_move_ref(_get(data, "move"), moves),
        pp=_int(data, "pp", low=0, high=max_pp),
        max_pp=max_pp,
    )


def _decode_entry(raw: object, moves: Sequence[Move]) -> LearnsetEntry:
    pair = _sequence(raw)
    if len(pair) != 2:
        raise _Corrupt("learnset")
    level = _int_value(pair[0], low=0, high=MAX_LEVEL)
    return LearnsetEntry(level=level, move=_move_ref(pair[1], moves))


def _move_ref(raw: object, moves: Sequence[Move]) -> Move:
    return moves[_int_value(raw, low=0, high=len(moves) - 1)]


def _decode_stages(data: Mapping[str, object]) -> Stages:
    values = {key: _int(data, key, low=MIN_STAGE, high=MAX_STAGE) for key in STAGE_KEYS}
    return Stages(
        atk=values["atk"],
        defense=values["def"],
        spa=values["spa"],
        spd=values["spd"],
        spe=values["spe"],
        accuracy=values["accuracy"],
        evasion=values["evasion"],
    )


def _decode_volatile(data: Mapping[str, object]) -> Volatile:
    return Volatile(
        confusion_turns=_int(data, "confusion_turns", low=0),
        flinched=_bool(data, "flinched"),
        protected=_bool(data, "protected"),
        protect_streak=_int(data, "protect_streak", low=0),
        charging=_optional_text(data, "charging"),
        semi_invulnerable=_bool(data, "semi_invulnerable"),
        recharging=_bool(data, "recharging"),
        trapped_turns=_int(data, "trapped_turns", low=0),
        trapped_by=_optional_text(data, "trapped_by"),
        seeded=_bool(data, "seeded"),
        focus_energy=_bool(data, "focus_energy"),
        last_move=_optional_text(data, "last_move"),
    )


# --- Lectores estrictos -----------------------------------------------------


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _Corrupt("mapping")
    return value


def _sequence(value: object) -> Sequence[object]:
    # Solo listas y tuplas: una cadena también es secuencia, pero no vale.
    if not isinstance(value, (list, tuple)):
        raise _Corrupt("list")
    return value


def _get(data: Mapping[str, object], key: str) -> object:
    if key not in data:
        raise _Corrupt(key)
    return data[key]


def _list(data: Mapping[str, object], key: str) -> Sequence[object]:
    return _sequence(_get(data, key))


def _int_value(value: object, *, low: int | None = None, high: int | None = None) -> int:
    # ``bool`` es subclase de ``int``, pero ``True`` no es un número válido.
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Corrupt("int")
    if (low is not None and value < low) or (high is not None and value > high):
        raise _Corrupt("range")
    return value


def _int(
    data: Mapping[str, object], key: str, *, low: int | None = None, high: int | None = None
) -> int:
    return _int_value(_get(data, key), low=low, high=high)


def _optional_int(data: Mapping[str, object], key: str, *, low: int | None = None) -> int | None:
    value = _get(data, key)
    return None if value is None else _int_value(value, low=low)


def _bool(data: Mapping[str, object], key: str) -> bool:
    value = _get(data, key)
    if not isinstance(value, bool):
        raise _Corrupt(key)
    return value


def _text_value(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise _Corrupt("text")
    return value


def _text(data: Mapping[str, object], key: str) -> str:
    return _text_value(_get(data, key))


def _any_text(data: Mapping[str, object], key: str) -> str:
    value = _get(data, key)
    if not isinstance(value, str):
        raise _Corrupt(key)
    return value


def _optional_text(data: Mapping[str, object], key: str) -> str | None:
    value = _get(data, key)
    if value is None or isinstance(value, str):
        return value
    raise _Corrupt(key)


def _enum_value(enum: type[_EnumT], value: object) -> _EnumT:
    if not isinstance(value, str):
        raise _Corrupt(enum.__name__)
    try:
        return enum(value)
    except ValueError as error:
        raise _Corrupt(enum.__name__) from error


def _enum(enum: type[_EnumT], data: Mapping[str, object], key: str) -> _EnumT:
    return _enum_value(enum, _get(data, key))
