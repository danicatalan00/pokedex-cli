"""Combatientes: stats reales, subidas de nivel y moveset automático.

Las stats salen de ``domain.individuality`` (fórmula de Gen 3 con EV 0), así
que cada Pokémon pelea con las mismas stats que muestra su ficha.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from pokedex_cli.domain.battle.model import (
    MAX_MOVES,
    STAT_KEYS,
    Combatant,
    LearnsetEntry,
    Move,
    MoveSlot,
    Stats,
)
from pokedex_cli.domain.battle.movesets import learnable, slots_for, smart_moveset
from pokedex_cli.domain.individuality import NATURES, compute_stats, nature_by_name
from pokedex_cli.domain.progression import MAX_LEVEL

# Naturaleza neutra (Fuerte) para nombres desconocidos.
_NEUTRAL_NATURE = NATURES[0]


def _clamp_level(level: int) -> int:
    return max(1, min(MAX_LEVEL, level))


def build_stats(base: Stats, ivs: Stats, level: int, nature: str) -> Stats:
    """Stats de Gen 3 con EV 0; nivel acotado a 1..100 y naturaleza desconocida neutra."""
    resolved = nature_by_name(nature) or _NEUTRAL_NATURE
    computed = compute_stats(base.as_mapping(), ivs.as_mapping(), _clamp_level(level), resolved)
    return Stats.from_mapping({key: value for key, value in computed.items() if value is not None})


def build_combatant(
    *,
    key: str,
    species: str,
    form: str,
    name: str,
    level: int,
    types: Sequence[str],
    base_stats: Stats,
    ivs: Stats,
    nature: str,
    moves: Sequence[Move],
    shiny: bool = False,
    gender: str | None = None,
    ability: str | None = None,
    capture_id: int | None = None,
    base_experience: int = 64,
    growth_rate: str = "medium",
    experience: int = 0,
    learnset: Sequence[LearnsetEntry] = (),
    custom_moves: bool = False,
) -> Combatant:
    """Combatiente listo para luchar: PS llenos, stats calculadas y PP al máximo.

    Conserva los cuatro primeros movimientos distintos (por slug).
    """
    stats = build_stats(base_stats, ivs, level, nature)
    distinct: dict[str, Move] = {}
    for move in moves:
        distinct.setdefault(move.slug, move)
    return Combatant(
        key=key,
        species=species,
        form=form,
        name=name,
        level=_clamp_level(level),
        types=tuple(types),
        base_stats=base_stats,
        ivs=ivs,
        nature=nature,
        stats=stats,
        hp=stats.hp,
        moves=slots_for(list(distinct.values())[:MAX_MOVES]),
        shiny=shiny,
        gender=gender,
        ability=ability,
        capture_id=capture_id,
        base_experience=base_experience,
        growth_rate=growth_rate,
        experience=experience,
        learnset=tuple(learnset),
        custom_moves=custom_moves,
    )


def level_up(c: Combatant, new_level: int) -> tuple[Combatant, tuple[tuple[str, int], ...]]:
    """Recalcula las stats al nuevo nivel y devuelve la subida de cada una.

    Los PS actuales suben lo mismo que el máximo (un debilitado sigue a 0).
    Los movimientos no cambian: eso es ``refresh_auto_moveset``.
    """
    level = _clamp_level(new_level)
    stats = build_stats(c.base_stats, c.ivs, level, c.nature)
    deltas = tuple((key, stats.value(key) - c.stats.value(key)) for key in STAT_KEYS)
    hp = c.hp if c.fainted else max(1, min(stats.hp, c.hp + stats.hp - c.max_hp))
    return replace(c, level=level, stats=stats, hp=hp), deltas


def refresh_auto_moveset(
    c: Combatant,
) -> tuple[Combatant, tuple[tuple[str, str | None], ...]]:
    """Recalcula el moveset automático con lo aprendible al nivel actual.

    Solo si ``custom_moves`` es falso y hay ``learnset``. Los movimientos que
    siguen conservan hueco y PP; cada nuevo ocupa el hueco de uno olvidado (o
    se añade al final) con los PP llenos. Devuelve pares (aprendido, olvidado
    o ``None``); si nada cambia, el mismo combatiente y ``()``.
    """
    if c.custom_moves or not c.learnset:
        return c, ()
    catalog = {entry.move.slug: entry.move for entry in c.learnset}
    slugs = learnable([(entry.move.slug, entry.level) for entry in c.learnset], c.level)
    if not slugs:
        return c, ()
    target = smart_moveset([catalog[slug] for slug in slugs], c.types)
    target_slugs = {move.slug for move in target}
    current_slugs = {slot.move.slug for slot in c.moves}
    if target_slugs == current_slugs:
        return c, ()
    incoming = [move for move in target if move.slug not in current_slugs]
    forgotten = [slot.move for slot in c.moves if slot.move.slug not in target_slugs]
    pending = list(incoming)
    slots: list[MoveSlot] = []
    for slot in c.moves:
        if slot.move.slug in target_slugs:
            slots.append(slot)
        elif pending:
            slots.extend(slots_for([pending.pop(0)]))
    slots.extend(slots_for(pending))
    learned = tuple(
        (move.name, forgotten[index].name if index < len(forgotten) else None)
        for index, move in enumerate(incoming)
    )
    return replace(c, moves=tuple(slots[:MAX_MOVES])), learned
