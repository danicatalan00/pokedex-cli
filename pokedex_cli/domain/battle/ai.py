"""IA del combate: qué movimiento usa cada lado y a quién saca de relevo.

Un salvaje elige al azar entre los movimientos con PP. Un entrenador (y el
modo automático del jugador) puntúa cada movimiento con lo que se ve en el
campo —daño esperado con eficacia, STAB y precisión, un gran extra si
debilita, estados útiles contra un rival sano y no inmune, subidas propias al
principio con PS altos y curación por debajo del 40 %— y usa el mejor con
probabilidad 0,8; si no, uno al azar. No hace trampas: no conoce la acción
del rival ni el azar del turno. Nunca devuelve una acción inválida.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pokedex_cli.domain.battle.mechanics import (
    accuracy_stage_multiplier,
    calculate_damage,
    can_receive_status,
    fixed_damage,
    move_effectiveness,
)
from pokedex_cli.domain.battle.model import (
    STAGE_KEYS,
    Action,
    BattleKind,
    BattleState,
    Combatant,
    DamageClass,
    Move,
    RandomSource,
    SideId,
    Status,
    UseMove,
    other_side,
)
from pokedex_cli.domain.battle.moves import (
    CHARGE_MOVES,
    DREAM_EATER,
    FOCUS_ENERGY,
    FORCE_SWITCH,
    HAZE,
    PROTECT_MOVES,
    RECHARGE_MOVES,
    REST,
    SACRIFICE,
    SCREENS,
    SELF_TARGETS,
    TOXIC_MOVES,
    hit_range,
    is_supported,
)
from pokedex_cli.domain.battle.movesets import AVERAGE_HITS_2_TO_5
from pokedex_cli.domain.battle.typechart import effectiveness

BEST_MOVE_CHANCE = 0.8
# Tirada media del factor aleatorio (85..100) para estimar el daño.
AVERAGE_ROLL = 92
# Las puntuaciones se miden en «% de los PS máximos del rival»: un golpe que
# quita la mitad vale 50. Debilitar suma este extra.
KO_BONUS = 100.0
UNSUPPORTED_SCORE = -1.0
# Curarse solo compensa por debajo de este umbral de PS; subir
# características, por encima del otro y mientras el escalón sea bajo.
HEAL_THRESHOLD = 0.4
BOOST_THRESHOLD = 0.7
BOOST_STAGE_LIMIT = 2
BOOST_VALUE = 15.0  # por escalón propio subido
DROP_VALUE = 8.0  # por escalón rival bajado
CONFUSION_VALUE = 30.0
LEECH_SEED_VALUE = 30.0
SWAGGER_VALUE = 15.0
SCREEN_VALUE = 20.0
HAZE_VALUE = 30.0
FOCUS_ENERGY_VALUE = 12.0
PROTECT_VALUE = 5.0
REST_VALUE = 50.0
HEAL_WEIGHT = 1.2
# Curar al rival (Pulso Cura en un combate individual) nunca es buena idea.
HEAL_FOE_SCORE = -10.0
# Los golpes de dos turnos dejan un turno al rival; los de recarga, otro después.
CHARGE_FACTOR = 0.5
RECHARGE_FACTOR = 0.75
# Explosión y compañía solo compensan cuando ya queda poco que perder.
SACRIFICE_FACTOR = 0.5
SACRIFICE_HP_THRESHOLD = 0.25
# Sorpresa y Escaramuza solo funcionan en el primer turno del usuario en el
# campo y el motor las hace fallar después. Sin esa regla, la prioridad +3 y
# el retroceso seguro de Sorpresa bloquearían al rival turno tras turno.
FIRST_TURN_ONLY: frozenset[str] = frozenset({"fake-out", "first-impression"})

_STATUS_VALUES: Mapping[Status, float] = MappingProxyType(
    {
        Status.SLEEP: 60.0,
        Status.FREEZE: 50.0,
        Status.PARALYSIS: 45.0,
        Status.TOXIC: 45.0,
        Status.BURN: 40.0,
        Status.POISON: 30.0,
    }
)
_AILMENT_STATUS: Mapping[str, Status] = MappingProxyType(
    {
        "paralysis": Status.PARALYSIS,
        "sleep": Status.SLEEP,
        "freeze": Status.FREEZE,
        "burn": Status.BURN,
        "poison": Status.POISON,
    }
)


def choose_action(state: BattleState, side: SideId, rng: RandomSource) -> Action:
    """Acción del lado ``side`` para este turno (siempre un movimiento).

    Sin PP en ningún movimiento, o con el turno comprometido (carga o
    recarga), devuelve ``UseMove(0)``: el motor hará Forcejeo o seguirá con
    lo que tenga en curso. Consume como mucho un ``random()`` y un ``randint``.
    """
    me = state.side(side).current
    usable = [index for index, slot in enumerate(me.moves) if slot.pp > 0]
    if not usable or me.volatile.charging is not None or me.volatile.recharging:
        return UseMove(0)
    if len(usable) == 1:
        return UseMove(usable[0])
    if state.kind is BattleKind.WILD and side == "opponent":
        return UseMove(usable[rng.randint(0, len(usable) - 1)])
    scores = {index: move_score(state, side, me.moves[index].move) for index in usable}
    best = max(usable, key=lambda index: (scores[index], -index))
    if rng.random() < BEST_MOVE_CHANCE:
        return UseMove(best)
    return UseMove(usable[rng.randint(0, len(usable) - 1)])


def choose_replacement_index(state: BattleState, side: SideId, rng: RandomSource) -> int:
    """Relevo para ``side``: el miembro vivo con más ventaja de tipo.

    La ventaja es la mejor eficacia de sus movimientos dañinos con PP contra
    el rival activo (o la de sus tipos, si no tiene) menos la peor eficacia de
    los tipos del rival contra él. Los empates van al primero del equipo: sin
    ventaja clara, sale el siguiente vivo. ``rng`` no se usa: la elección es
    determinista, pero la firma deja sitio para variarla.
    """
    team = state.side(side)
    candidates = [index for index in team.able if index != team.active]
    if not candidates:
        raise ValueError("No quedan Pokémon para sacar.")
    foe = state.side(other_side(side)).current
    return max(candidates, key=lambda index: (_type_advantage(team.team[index], foe), -index))


def move_score(state: BattleState, side: SideId, move: Move) -> float:
    """Puntuación de ``move`` para el activo de ``side``; más es mejor."""
    if not is_supported(move):
        return UNSUPPORTED_SCORE
    me = state.side(side).current
    foe_side = other_side(side)
    foe = state.side(foe_side).current
    if move.is_damaging:
        return _damage_score(state, foe_side, me, foe, move)
    return _status_score(state, side, me, foe, move)


# --- Daño -------------------------------------------------------------------


def _damage_score(
    state: BattleState, foe_side: SideId, me: Combatant, foe: Combatant, move: Move
) -> float:
    if foe.fainted or foe.volatile.semi_invulnerable or foe.max_hp <= 0:
        return 0.0
    if move.slug in DREAM_EATER and foe.status is not Status.SLEEP:
        return 0.0
    if move.slug in FIRST_TURN_ONLY and me.volatile.last_move is not None:
        return 0.0
    if move_effectiveness(move, foe.types) == 0:
        return 0.0
    hit = _hit_chance(me, foe, move)
    if hit <= 0:
        return 0.0
    expected = _expected_damage(state, foe_side, me, foe, move)
    knocks_out = expected >= foe.hp
    score = min(expected, foe.hp) / foe.max_hp * 100 * hit
    if knocks_out:
        score += KO_BONUS * hit
    if move.slug in CHARGE_MOVES:
        score *= CHARGE_FACTOR
    if move.slug in RECHARGE_MOVES and not knocks_out:
        score *= RECHARGE_FACTOR
    if move.slug in SACRIFICE and me.hp > me.max_hp * SACRIFICE_HP_THRESHOLD:
        score *= SACRIFICE_FACTOR
    return score


def _expected_damage(
    state: BattleState, foe_side: SideId, me: Combatant, foe: Combatant, move: Move
) -> float:
    if move.category == "ohko":
        return float(foe.hp)
    fixed = fixed_damage(me, foe, move)
    if fixed is not None:
        return float(fixed)
    screens = state.side(foe_side)
    screen = (
        screens.reflect_turns > 0
        if move.damage_class is DamageClass.PHYSICAL
        else screens.light_screen_turns > 0
    )
    per_hit = calculate_damage(me, foe, move, critical=False, roll=AVERAGE_ROLL, screen=screen)
    low, high = hit_range(move)
    hits = AVERAGE_HITS_2_TO_5 if (low, high) == (2, 5) else (low + high) / 2
    return per_hit * hits


def _hit_chance(me: Combatant, foe: Combatant, move: Move) -> float:
    """Probabilidad de acertar, con la misma regla que ``roll_hits``."""
    if move.category == "ohko":
        if foe.level > me.level:
            return 0.0
        return min(1.0, (30 + me.level - foe.level) / 100)
    if move.accuracy is None:
        return 1.0
    stage = me.stages.accuracy - foe.stages.evasion
    return min(1.0, move.accuracy / 100 * accuracy_stage_multiplier(stage))


# --- Estado -----------------------------------------------------------------


def _status_score(
    state: BattleState, side: SideId, me: Combatant, foe: Combatant, move: Move
) -> float:
    slug = move.slug
    if slug in PROTECT_MOVES:
        return PROTECT_VALUE if me.volatile.protect_streak == 0 else 0.0
    if slug == REST:
        return REST_VALUE if _hp_fraction(me) < HEAL_THRESHOLD else 0.0
    if slug in SCREENS:
        own = state.side(side)
        active = own.reflect_turns if SCREENS[slug] == "reflect" else own.light_screen_turns
        return SCREEN_VALUE if active == 0 else 0.0
    if slug in HAZE:
        swing = any(foe.stages.value(key) > 0 or me.stages.value(key) < 0 for key in STAGE_KEYS)
        return HAZE_VALUE if swing else 0.0
    if slug in FOCUS_ENERGY:
        return 0.0 if me.volatile.focus_energy else FOCUS_ENERGY_VALUE
    if slug in FORCE_SWITCH or move.category == "force-switch":
        # Contra un salvaje acabaría el combate y contra un entrenador solo
        # baraja: nunca es la mejor jugada a propósito.
        return 0.0
    if move.category == "ailment":
        return _ailment_score(me, foe, move)
    if move.category == "net-good-stats":
        return _stats_score(me, foe, move)
    if move.category == "heal":
        return _heal_score(me, move)
    if move.category == "swagger":
        reachable = not foe.volatile.semi_invulnerable and foe.volatile.confusion_turns == 0
        return SWAGGER_VALUE * _hit_chance(me, foe, move) if reachable else 0.0
    # Salpicadura, Teletransporte y cualquier otra cosa sin valor táctico.
    return 0.0


def _ailment_score(me: Combatant, foe: Combatant, move: Move) -> float:
    if foe.fainted or foe.volatile.semi_invulnerable:
        return 0.0
    if move.ailment == "leech-seed":
        useful = not foe.volatile.seeded and "grass" not in foe.types
        value = LEECH_SEED_VALUE if useful else 0.0
    elif move.ailment == "confusion":
        value = CONFUSION_VALUE if foe.volatile.confusion_turns == 0 else 0.0
    else:
        status = Status.TOXIC if move.slug in TOXIC_MOVES else _AILMENT_STATUS.get(move.ailment)
        if status is None or not can_receive_status(foe, status):
            return 0.0
        # Gen 3: los movimientos que paralizan respetan la tabla de tipos.
        if status is Status.PARALYSIS and move_effectiveness(move, foe.types) == 0:
            return 0.0
        value = _STATUS_VALUES[status]
    return value * _hit_chance(me, foe, move)


def _stats_score(me: Combatant, foe: Combatant, move: Move) -> float:
    if move.target == "ally":
        return 0.0
    if move.target in SELF_TARGETS:
        if _hp_fraction(me) < BOOST_THRESHOLD:
            return 0.0
        return sum(
            BOOST_VALUE * change.change
            for change in move.stat_changes
            if change.stat in STAGE_KEYS
            and change.change > 0
            and me.stages.value(change.stat) < BOOST_STAGE_LIMIT
        )
    if foe.volatile.semi_invulnerable:
        return 0.0
    value = sum(
        DROP_VALUE * -change.change
        for change in move.stat_changes
        if change.stat in STAGE_KEYS
        and change.change < 0
        and foe.stages.value(change.stat) > -BOOST_STAGE_LIMIT
    )
    return value * _hit_chance(me, foe, move)


def _heal_score(me: Combatant, move: Move) -> float:
    if move.target not in SELF_TARGETS:
        return HEAL_FOE_SCORE
    if me.max_hp <= 0 or _hp_fraction(me) >= HEAL_THRESHOLD:
        return 0.0
    healed = min(me.max_hp - me.hp, me.max_hp * move.healing // 100)
    return healed / me.max_hp * 100 * HEAL_WEIGHT


def _hp_fraction(c: Combatant) -> float:
    return c.hp / c.max_hp if c.max_hp > 0 else 0.0


# --- Relevos ----------------------------------------------------------------


def _type_advantage(candidate: Combatant, foe: Combatant) -> float:
    offense = [
        move_effectiveness(slot.move, foe.types)
        for slot in candidate.moves
        if slot.move.is_damaging and slot.pp > 0
    ]
    if not offense:
        offense = [effectiveness(own, foe.types) for own in candidate.types] or [1.0]
    defense = [effectiveness(foe_type, candidate.types) for foe_type in foe.types] or [1.0]
    return max(offense) - max(defense)
