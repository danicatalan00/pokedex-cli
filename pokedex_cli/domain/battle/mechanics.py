"""Reglas numéricas del combate: Gen 3 con categorías y tabla de tipos modernas.

Funciones puras: reciben combatientes y, cuando hay azar, un ``RandomSource``
inyectado. Las fórmulas truncan tras cada factor, como los juegos, y usan
aritmética entera para los escalones (sin errores de coma flotante).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from types import MappingProxyType

from pokedex_cli.domain.battle.model import (
    MAX_STAGE,
    MIN_STAGE,
    Action,
    BattleState,
    Combatant,
    DamageClass,
    Move,
    RandomSource,
    SideId,
    Status,
    UseMove,
)
from pokedex_cli.domain.battle.moves import (
    FIXED_DAMAGE,
    LEVEL_DAMAGE,
    STRUGGLE,
    hit_range,
    is_fixed_damage,
)
from pokedex_cli.domain.battle.typechart import effectiveness

# Factor aleatorio del daño: r/100 con r entre 85 y 100.
MIN_ROLL = 85
MAX_ROLL = 100

# Probabilidad de crítico por escalón (0, 1, 2, 3, 4 o más).
_CRIT_CHANCES: tuple[float, ...] = (1 / 16, 1 / 8, 1 / 4, 1 / 3, 1 / 2)
FOCUS_ENERGY_CRIT_STAGES = 2

# Gen 3: un multigolpe 2–5 da 2 o 3 golpes con 3/8 cada uno y 4 o 5 con 1/8.
_HITS_2_TO_5: tuple[int, ...] = (2, 2, 2, 3, 3, 3, 4, 5)

# Multiplicador de captura por estado: dormido o congelado ×2; el resto ×1,5.
STATUS_CATCH_BONUS: Mapping[Status, float] = MappingProxyType(
    {
        Status.NONE: 1.0,
        Status.SLEEP: 2.0,
        Status.FREEZE: 2.0,
        Status.PARALYSIS: 1.5,
        Status.BURN: 1.5,
        Status.POISON: 1.5,
        Status.TOXIC: 1.5,
    }
)

# Inmunidades de estado por tipo (modernas: Eléctrico no se paraliza).
_STATUS_IMMUNE_TYPES: Mapping[Status, frozenset[str]] = MappingProxyType(
    {
        Status.BURN: frozenset({"fire"}),
        Status.PARALYSIS: frozenset({"electric"}),
        Status.POISON: frozenset({"poison", "steel"}),
        Status.TOXIC: frozenset({"poison", "steel"}),
        Status.FREEZE: frozenset({"ice"}),
    }
)

# Forcejeo no tiene tipo desde Gen 2 (en Gen 3 ``typecalc`` lo salta): sin STAB
# y sin tabla de tipos, así que también golpea a los Fantasma.
_TYPELESS_MOVES: frozenset[str] = frozenset({STRUGGLE.slug})

# Golpe a sí mismo por confusión: físico de potencia 40 y sin tipo, así que
# la eficacia es siempre ×1 y nunca hay STAB.
_CONFUSION_HIT = Move(
    slug="confusion-self-hit",
    name="Confusión",
    type="typeless",
    damage_class=DamageClass.PHYSICAL,
    power=40,
    accuracy=None,
    pp=1,
)


# --- Escalones --------------------------------------------------------------


def _stage_ratio(stage: int, base: int) -> tuple[int, int]:
    """Fracción ``(base + n)/base`` o ``base/(base − n)`` con ``n`` acotado a ±6."""
    clamped = max(MIN_STAGE, min(MAX_STAGE, stage))
    if clamped >= 0:
        return base + clamped, base
    return base, base - clamped


def stage_multiplier(stage: int) -> float:
    """Ataque, Defensa, Velocidad…: ×(2+n)/2 si n ≥ 0 y ×2/(2−n) si n < 0."""
    numerator, denominator = _stage_ratio(stage, 2)
    return numerator / denominator


def accuracy_stage_multiplier(stage: int) -> float:
    """Precisión neta: ×(3+n)/3 si n ≥ 0 y ×3/(3−n) si n < 0."""
    numerator, denominator = _stage_ratio(stage, 3)
    return numerator / denominator


def crit_chance(stage: int) -> float:
    """Escalón de crítico 0..4+ → 1/16, 1/8, 1/4, 1/3, 1/2."""
    return _CRIT_CHANCES[max(0, min(stage, len(_CRIT_CHANCES) - 1))]


def effective_stat(
    c: Combatant, key: str, *, ignore_drops: bool = False, ignore_boosts: bool = False
) -> int:
    """Stat (``atk``, ``def``, ``spa``, ``spd`` o ``spe``) con su escalón; mínimo 1.

    ``ignore_drops`` descarta un escalón negativo y ``ignore_boosts`` uno
    positivo: es lo que hace un golpe crítico con el atacante y el defensor.
    """
    stage = c.stages.value(key)
    if (ignore_drops and stage < 0) or (ignore_boosts and stage > 0):
        stage = 0
    numerator, denominator = _stage_ratio(stage, 2)
    return max(1, c.stats.value(key) * numerator // denominator)


def effective_speed(c: Combatant) -> int:
    """Velocidad con su escalón, ×0,25 si está paralizado; mínimo 1."""
    speed = effective_stat(c, "spe")
    if c.status is Status.PARALYSIS:
        speed //= 4
    return max(1, speed)


# --- Daño -------------------------------------------------------------------


def move_effectiveness(move: Move, defender_types: Sequence[str]) -> float:
    """Eficacia de tipo de ``move``; un movimiento sin tipo (Forcejeo) es ×1.

    El motor debe decidir la inmunidad y el «¡Es muy eficaz!» con esta función,
    no con ``typechart.effectiveness(move.type, …)``, para coincidir con el daño.
    """
    if move.slug in _TYPELESS_MOVES:
        return 1.0
    return effectiveness(move.type, defender_types)


def calculate_damage(
    attacker: Combatant,
    defender: Combatant,
    move: Move,
    *,
    critical: bool,
    roll: int,
    screen: bool,
    power_override: int | None = None,
) -> int:
    """Daño de Gen 3 de un golpe, truncando tras cada factor.

    ``base = floor(floor(floor(2·L/5 + 2) · Pot · A / D) / 50)``; quemado y
    físico → ``floor(base/2)``; pantalla sin crítico → ``floor(base/2)``;
    ``+2``; crítico ×2; azar ``floor(x · roll/100)``; STAB ×1,5; eficacia.
    Mínimo 1 salvo inmunidad (0). ``screen`` indica que el defensor tiene la
    pantalla que corresponde a la clase del movimiento (Reflejo o Pantalla
    Luz); el crítico la ignora, igual que el escalón negativo de Ataque del
    atacante y el positivo de Defensa del defensor. Forcejeo no tiene tipo:
    ni STAB ni eficacia.
    """
    if not move.is_damaging:
        raise ValueError(f"{move.slug} no es un movimiento de daño")
    power = move.power if power_override is None else power_override
    if power is None or power < 1:
        raise ValueError(f"{move.slug} no tiene potencia: usa fixed_damage o power_override")
    if not MIN_ROLL <= roll <= MAX_ROLL:
        raise ValueError(f"roll debe estar entre {MIN_ROLL} y {MAX_ROLL}")
    multiplier = move_effectiveness(move, defender.types)
    if multiplier == 0:
        return 0
    physical = move.damage_class is DamageClass.PHYSICAL
    attack_key, defense_key = ("atk", "def") if physical else ("spa", "spd")
    attack = effective_stat(attacker, attack_key, ignore_drops=critical)
    defense = effective_stat(defender, defense_key, ignore_boosts=critical)
    damage = (2 * attacker.level // 5 + 2) * power * attack // defense // 50
    if physical and attacker.status is Status.BURN:
        damage //= 2
    if screen and not critical:
        damage //= 2
    damage += 2
    if critical:
        damage *= 2
    damage = damage * roll // 100
    if move.slug not in _TYPELESS_MOVES and move.type in attacker.types:
        damage = damage * 3 // 2
    return max(1, math.floor(damage * multiplier))


def fixed_damage(attacker: Combatant, defender: Combatant, move: Move) -> int | None:
    """Daño de los movimientos que no miran stats; ``None`` si no es uno de ellos.

    Furia Dragón y Bomba Sónica quitan PS fijos, Sísmico y Tinieblas el nivel
    del usuario, Superdiente la mitad de los PS actuales (mínimo 1). Respetan
    la inmunidad de tipo (0), pero no STAB, crítico, azar ni resistencias.
    """
    if not is_fixed_damage(move):
        return None
    if effectiveness(move.type, defender.types) == 0:
        return 0
    if move.slug in FIXED_DAMAGE:
        return FIXED_DAMAGE[move.slug]
    if move.slug in LEVEL_DAMAGE:
        return attacker.level
    return max(1, defender.hp // 2)


def confusion_damage(c: Combatant, roll: int = MAX_ROLL) -> int:
    """Daño que se hace a sí mismo un Pokémon confuso (sin crítico ni pantalla)."""
    return calculate_damage(c, c, _CONFUSION_HIT, critical=False, roll=roll, screen=False)


# --- Tiradas de azar --------------------------------------------------------


def roll_critical(attacker: Combatant, move: Move, rng: RandomSource) -> bool:
    """Una tirada ``rng.random()`` contra el escalón del movimiento (+2 con Foco Energía)."""
    stage = max(0, move.crit_rate)
    if attacker.volatile.focus_energy:
        stage += FOCUS_ENERGY_CRIT_STAGES
    return rng.random() < crit_chance(stage)


def roll_hits(attacker: Combatant, defender: Combatant, move: Move, rng: RandomSource) -> bool:
    """¿Acierta el movimiento?

    Sin precisión acierta siempre y no consume azar. Los fulminantes (``ohko``)
    fallan sin tirar si el defensor tiene más nivel y, si no, aciertan con
    ``30 + (L_atacante − L_defensor)`` % sin mirar escalones. El resto tira una
    vez contra ``precisión × escalón(precisión − evasión)``, con el neto
    acotado a ±6. La semi-invulnerabilidad la decide el motor.
    """
    if move.category == "ohko":
        if defender.level > attacker.level:
            return False
        return rng.random() * 100 < 30 + attacker.level - defender.level
    if move.accuracy is None:
        return True
    numerator, denominator = _stage_ratio(attacker.stages.accuracy - defender.stages.evasion, 3)
    return rng.random() * 100 < move.accuracy * numerator / denominator


def roll_hit_count(move: Move, rng: RandomSource) -> int:
    """Golpes de este uso: fijo si mín. = máx. (sin azar), 2–5 con el reparto
    de Gen 3 (37,5/37,5/12,5/12,5 %) y cualquier otro rango, uniforme."""
    low, high = hit_range(move)
    if low == high:
        return low
    if (low, high) == (2, 5):
        return _HITS_2_TO_5[rng.randint(0, len(_HITS_2_TO_5) - 1)]
    return rng.randint(low, high)


# --- Orden del turno --------------------------------------------------------


def _action_rank(c: Combatant, action: Action) -> tuple[int, int]:
    """(franja, prioridad): cambios, huida y objetos van antes que cualquier movimiento."""
    if not isinstance(action, UseMove):
        return 1, 0
    # Un golpe en carga o una recarga comprometen el turno: prioridad 0, la de
    # todos los movimientos de carga.
    if c.volatile.charging is not None or c.volatile.recharging:
        return 0, 0
    if not any(slot.pp > 0 for slot in c.moves):
        return 0, STRUGGLE.priority
    if 0 <= action.slot < len(c.moves):
        return 0, c.moves[action.slot].move.priority
    return 0, 0


def turn_order(
    state: BattleState,
    player_action: Action,
    opponent_action: Action,
    rng: RandomSource,
) -> tuple[SideId, SideId]:
    """Quién actúa primero: franja, luego prioridad, luego velocidad efectiva.

    Solo un empate total consume azar (una tirada ``rng.random()``).
    """
    player = state.player.current
    opponent = state.opponent.current
    player_key = (*_action_rank(player, player_action), effective_speed(player))
    opponent_key = (*_action_rank(opponent, opponent_action), effective_speed(opponent))
    if player_key == opponent_key:
        player_first = rng.random() < 0.5
    else:
        player_first = player_key > opponent_key
    return ("player", "opponent") if player_first else ("opponent", "player")


# --- Captura, huida y experiencia -------------------------------------------


def battle_catch_multiplier(c: Combatant) -> float:
    """Ayuda del combate a la captura: ``PS × estado``.

    ``PS = (3·máx − 2·actual) / máx`` vale 1 con los PS llenos y casi 3 con
    1 PS; el estado multiplica ×2 (dormido o congelado) o ×1,5 (el resto).
    """
    max_hp = max(1, c.max_hp)
    hp = max(0, min(c.hp, max_hp))
    return (3 * max_hp - 2 * hp) / max_hp * STATUS_CATCH_BONUS[c.status]


def escape_succeeds(player_speed: int, wild_speed: int, attempts: int, rng: RandomSource) -> bool:
    """Huida de Gen 3. ``attempts`` son los intentos fallidos previos.

    Con más o igual velocidad se huye sin tirar. Si no, ``F = (A·128 // B +
    30·C) % 256`` con ``C = attempts + 1`` y se huye si ``rand(0..255) < F``.
    El módulo es el de los juegos: con muchos intentos ``F`` puede dar la vuelta.
    """
    if player_speed >= wild_speed:
        return True
    odds = (player_speed * 128 // max(1, wild_speed) + 30 * (attempts + 1)) % 256
    return rng.randint(0, 255) < odds


def experience_yield(base_experience: int, level: int, participants: int, *, trainer: bool) -> int:
    """EXP de Gen 3 para cada participante: ``floor(floor(b·L/7) / s)``, ×1,5
    contra entrenador (truncado). Nunca menos de 1: ganar siempre da algo."""
    gained = max(1, base_experience) * max(1, level) // 7 // max(1, participants)
    if trainer:
        gained = gained * 3 // 2
    return max(1, gained)


def can_receive_status(c: Combatant, status: Status) -> bool:
    """Uno a la vez, nunca a un debilitado y respetando las inmunidades de tipo."""
    if status is Status.NONE or c.fainted or c.status is not Status.NONE:
        return False
    immune = _STATUS_IMMUNE_TYPES.get(status, frozenset())
    return immune.isdisjoint(c.types)
