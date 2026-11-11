"""Movesets: qué movimientos lleva cada Pokémon al combate.

Salvajes y entrenadores llevan los cuatro últimos aprendidos, como en Gen 3;
las capturas del jugador sin moveset propio eligen con ``smart_moveset``.
Ningún moveset se queda sin un movimiento dañino. Todo es determinista: la
misma entrada da siempre el mismo moveset, y los empates se rompen por slug.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType

from pokedex_cli.domain.battle.model import MAX_MOVES, Move, MoveSlot
from pokedex_cli.domain.battle.moves import (
    CHARGE_MOVES,
    DREAM_EATER,
    FALLBACK_BY_TYPE,
    RECHARGE_MOVES,
    REST,
    SACRIFICE,
    SELF_TARGETS,
    SPLASH,
    TACKLE,
    TOXIC_MOVES,
    hit_range,
    is_fixed_damage,
    is_supported,
)

# Reparto clásico: tres dañinos y uno de estado útil (o cuatro dañinos).
DAMAGING_WITH_STATUS = 3
# Cada movimiento ya elegido del mismo tipo multiplica la puntuación por esto,
# para cubrir más tipos en lugar de repetir el mejor.
REPEATED_TYPE_PENALTY = 0.6
# Los golpes que no dependen de stats (fijos y fulminantes) puntúan como 60.
FIXED_EFFECTIVE_POWER = 60.0
STAB = 1.5
PRIORITY_BONUS = 1.1
# Golpes medios de un multigolpe 2–5 con el reparto de Gen 3 (3/8, 3/8, 1/8, 1/8).
AVERAGE_HITS_2_TO_5 = 3.0

# Valor de los movimientos de estado útiles, antes de multiplicar por la
# precisión: dormir es lo más decisivo en Gen 3 y Descanso, lo más caro.
_SLEEP_VALUE = 100.0
_PARALYSIS_VALUE = 80.0
_TOXIC_VALUE = 75.0
_BOOST_VALUE = 70.0
_HEAL_VALUE = 65.0
_CONFUSION_VALUE = 60.0
_REST_VALUE = 50.0
_AILMENT_VALUES: Mapping[str, float] = MappingProxyType(
    {"sleep": _SLEEP_VALUE, "paralysis": _PARALYSIS_VALUE, "confusion": _CONFUSION_VALUE}
)


def learnable(entries: Sequence[tuple[str, int]], level: int) -> list[str]:
    """Slugs aprendibles hasta ``level``, en orden de aprendizaje.

    ``entries`` son pares ``(slug, nivel)``. El nivel 0 (al evolucionar)
    cuenta como 1; a igual nivel manda el orden original. Si un slug se
    repite, conserva su última aparición: la que lo aprende más tarde.
    """
    reachable = sorted(
        (max(1, learned_at), index, slug)
        for index, (slug, learned_at) in enumerate(entries)
        if max(1, learned_at) <= level
    )
    last_position = {slug: position for position, (_, _, slug) in enumerate(reachable)}
    return sorted(last_position, key=last_position.__getitem__)


def wild_moveset(
    entries: Sequence[tuple[str, int]], level: int, catalog: Mapping[str, Move]
) -> tuple[Move, ...]:
    """Los cuatro últimos movimientos aprendibles, soportados y en ``catalog``.

    Si ninguno es dañino, el más antiguo deja su sitio al último dañino que
    pudo aprender (o a Placaje si nunca aprendió ninguno); con menos de
    cuatro, ese dañino se añade delante sin quitar nada. Sin datos → ``()``.
    """
    usable = [
        catalog[slug]
        for slug in learnable(entries, level)
        if slug in catalog and is_supported(catalog[slug])
    ]
    chosen = usable[-MAX_MOVES:]
    if not chosen or any(move.is_damaging for move in chosen):
        return tuple(chosen)
    older_damaging = [move for move in usable if move.is_damaging]
    replacement = older_damaging[-1] if older_damaging else TACKLE
    return (replacement, *chosen[-(MAX_MOVES - 1) :])


def smart_moveset(candidates: Sequence[Move], types: Sequence[str]) -> tuple[Move, ...]:
    """Moveset equilibrado para las capturas del jugador.

    Puntúa los dañinos con ``potencia × precisión/100 × 1,5 si STAB`` (+10 %
    con prioridad positiva; fijos y fulminantes valen 60 y sin STAB, porque en
    combate no lo aplican; los multigolpe cuentan sus golpes medios; los de
    carga o recarga, que gastan dos turnos, y los de sacrificio valen la
    mitad). Elige hasta tres
    con penalización ×0,6 por tipo repetido y añade el mejor de estado útil
    (dormir, paralizar, tóxico, confusión, subida propia de +2 o curación); si
    no hay, un cuarto dañino. Los huecos que sobren se rellenan con otros de
    estado (nunca con Salpicadura). Sin dañinos, Placaje. Empates por slug.
    Comesueños solo se queda si el moveset también puede dormir al rival.
    """
    pool = _unique_by_slug(move for move in candidates if is_supported(move))
    moves = _balanced_moveset(pool, types)
    # Comesueños solo daña a un rival dormido: sin nada que lo duerma en el
    # moveset fallaría siempre, así que se elige de nuevo sin él.
    if any(move.slug in DREAM_EATER for move in moves) and not any(
        _puts_target_to_sleep(move) for move in moves
    ):
        moves = _balanced_moveset([move for move in pool if move.slug not in DREAM_EATER], types)
    return moves


def _balanced_moveset(pool: Sequence[Move], types: Sequence[str]) -> tuple[Move, ...]:
    damaging = [move for move in pool if move.is_damaging]
    status = sorted(
        (move for move in pool if not move.is_damaging and move.slug not in SPLASH),
        key=_status_rank,
    )
    has_useful_status = bool(status) and _useful_status_value(status[0]) > 0
    limit = DAMAGING_WITH_STATUS if has_useful_status else MAX_MOVES
    chosen = _pick_damaging(damaging, types, limit) or [TACKLE]
    return tuple(chosen + status[: MAX_MOVES - len(chosen)])


def fallback_moveset(types: Sequence[str]) -> tuple[Move, ...]:
    """Moveset sin conexión: el de reserva de cada tipo y Placaje si cabe."""
    chosen: list[Move] = []
    for type_name in types:
        move = FALLBACK_BY_TYPE.get(type_name)
        if move is not None and move not in chosen and len(chosen) < MAX_MOVES:
            chosen.append(move)
    if TACKLE not in chosen and len(chosen) < MAX_MOVES:
        chosen.append(TACKLE)
    return tuple(chosen)


def slots_for(moves: Sequence[Move]) -> tuple[MoveSlot, ...]:
    """Huecos de movimiento con los PP al máximo."""
    return tuple(MoveSlot(move=move, pp=move.pp, max_pp=move.pp) for move in moves)


def _unique_by_slug(moves: Iterable[Move]) -> list[Move]:
    # Un slug repetido conserva su última versión, como en ``learnable``.
    return list({move.slug: move for move in moves}.values())


def _accuracy_factor(move: Move) -> float:
    return 1.0 if move.accuracy is None else move.accuracy / 100


def _average_hits(move: Move) -> float:
    low, high = hit_range(move)
    if (low, high) == (2, 5):
        return AVERAGE_HITS_2_TO_5
    return (low + high) / 2


def _damage_score(move: Move, types: Sequence[str]) -> float:
    if move.category == "ohko" or is_fixed_damage(move):
        score = FIXED_EFFECTIVE_POWER
    else:
        score = (move.power or 0) * _average_hits(move)
        if move.type in types:
            score *= STAB
    if move.slug in CHARGE_MOVES or move.slug in RECHARGE_MOVES or move.slug in SACRIFICE:
        score /= 2
    score *= _accuracy_factor(move)
    if move.priority > 0:
        score *= PRIORITY_BONUS
    return score


def _pick_damaging(moves: Sequence[Move], types: Sequence[str], limit: int) -> list[Move]:
    """Elección voraz: en cada paso, la mejor puntuación ya penalizada."""
    scores = {move.slug: _damage_score(move, types) for move in moves}
    remaining = list(moves)
    chosen: list[Move] = []
    while remaining and len(chosen) < limit:
        best = min(remaining, key=lambda move: (-_penalized(move, chosen, scores), move.slug))
        chosen.append(best)
        remaining.remove(best)
    return chosen


def _penalized(move: Move, chosen: Sequence[Move], scores: Mapping[str, float]) -> float:
    repeats = sum(1 for other in chosen if other.type == move.type)
    return scores[move.slug] * REPEATED_TYPE_PENALTY**repeats


def _puts_target_to_sleep(move: Move) -> bool:
    # Descanso duerme al usuario, no al rival: no cuenta.
    return not move.is_damaging and move.category == "ailment" and move.ailment == "sleep"


def _useful_status_value(move: Move) -> float:
    """Valor base de un movimiento de estado útil; 0 si no lo es."""
    if move.slug in TOXIC_MOVES:
        return _TOXIC_VALUE
    if move.slug == REST:
        return _REST_VALUE
    if move.category == "ailment":
        return _AILMENT_VALUES.get(move.ailment, 0.0)
    # Pulso Cura y similares curan a su objetivo: en individual, al rival.
    if move.category == "heal" and move.healing > 0 and move.target in SELF_TARGETS:
        return _HEAL_VALUE
    if (
        move.category == "net-good-stats"
        and move.target in SELF_TARGETS
        and any(change.change >= 2 for change in move.stat_changes)
    ):
        return _BOOST_VALUE
    return 0.0


def _status_rank(move: Move) -> tuple[bool, float, str]:
    # Primero los útiles por valor × precisión; luego el resto por precisión.
    value = _useful_status_value(move)
    accuracy = _accuracy_factor(move)
    if value > 0:
        return (False, -value * accuracy, move.slug)
    return (True, -accuracy, move.slug)
