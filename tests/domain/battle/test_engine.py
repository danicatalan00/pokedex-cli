"""Motor de combate: escenarios deterministas con azar guionizado.

Cada prueba fija las tiradas exactas que consume el turno (``play`` exige
gastarlas todas), así que también protege el orden de consumo del azar.
"""

from __future__ import annotations

import random
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import TypeVar

import pytest

from pokedex_cli.domain.battle.combatants import build_combatant, level_up
from pokedex_cli.domain.battle.engine import (
    action_error,
    available_moves,
    begin,
    can_run,
    choose_replacement,
    finish,
    locked_in,
    resolve_turn,
    switch_targets,
)
from pokedex_cli.domain.battle.mechanics import (
    calculate_damage,
    confusion_damage,
    experience_yield,
)
from pokedex_cli.domain.battle.model import (
    MAX_TURNS,
    Action,
    BallThrown,
    BattleEnded,
    BattleEvent,
    BattleKind,
    BattleStarted,
    BattleState,
    Charging,
    Combatant,
    Confused,
    DamageCause,
    DamageClass,
    Damaged,
    EscapeAttempted,
    ExperienceGained,
    FailReason,
    Fainted,
    ForcedOut,
    HealCause,
    Healed,
    HitCount,
    ImmobileReason,
    Immobilized,
    LearnsetEntry,
    LeveledUp,
    Move,
    MoveFailed,
    MoveLearned,
    MoveMissed,
    MoveSlot,
    MoveUsed,
    NoEffect,
    NoMovesLeft,
    Outcome,
    Phase,
    Protected,
    ProtectionRaised,
    PumpedUp,
    Recalled,
    Released,
    ReplacementNeeded,
    Run,
    ScreenFaded,
    ScreenRaised,
    Seeded,
    SentOut,
    Side,
    SpendTurn,
    Stages,
    StatChange,
    StatChanged,
    Stats,
    StatsReset,
    StatUnchanged,
    Status,
    StatusBlocked,
    StatusInflicted,
    SwitchTo,
    Teleported,
    Trapped,
    TurnResult,
    TurnStarted,
    UseMove,
    Volatile,
)
from pokedex_cli.domain.battle.moves import STRUGGLE, TACKLE

PHYSICAL = DamageClass.PHYSICAL
SPECIAL = DamageClass.SPECIAL
STATUS = DamageClass.STATUS

_E = TypeVar("_E")

# Tiradas guionizadas: precisión que siempre acierta, crítico sí o no.
HIT = 0.0
NO_CRIT = 0.99
CRIT = 0.0


def _move(
    slug: str,
    name: str,
    type_: str,
    damage_class: DamageClass,
    power: int | None,
    accuracy: int | None,
    pp: int,
    **extra: object,
) -> Move:
    return Move(
        slug=slug,
        name=name,
        type=type_,
        damage_class=damage_class,
        power=power,
        accuracy=accuracy,
        pp=pp,
        **extra,  # type: ignore[arg-type]
    )


# --- Catálogo escrito a mano (todas las categorías del motor) ---------------

QUICK_ATTACK = _move("quick-attack", "Ataque Rápido", "normal", PHYSICAL, 40, 100, 30, priority=1)
# Prioridad +3 y retroceso seguro: solo vale en el primer turno en el campo.
FAKE_OUT = _move(
    "fake-out", "Sorpresa", "normal", PHYSICAL, 40, 100, 10, priority=3, flinch_chance=100
)
EMBER = _move(
    "ember",
    "Ascuas",
    "fire",
    SPECIAL,
    40,
    100,
    25,
    category="damage-ailment",
    ailment="burn",
    ailment_chance=10,
)
WATER_GUN = _move("water-gun", "Pistola Agua", "water", SPECIAL, 40, 100, 25)
THUNDER_SHOCK = _move(
    "thunder-shock",
    "Impactrueno",
    "electric",
    SPECIAL,
    40,
    100,
    30,
    category="damage-ailment",
    ailment="paralysis",
    ailment_chance=10,
)
ICE_BEAM = _move(
    "ice-beam",
    "Rayo Hielo",
    "ice",
    SPECIAL,
    90,
    100,
    10,
    category="damage-ailment",
    ailment="freeze",
    ailment_chance=10,
)
BITE = _move("bite", "Mordisco", "dark", PHYSICAL, 60, 100, 25, flinch_chance=30)
ROCK_SMASH = _move(
    "rock-smash",
    "Golpe Roca",
    "fighting",
    PHYSICAL,
    40,
    100,
    15,
    category="damage-lower",
    stat_changes=(StatChange("def", -1),),
    stat_chance=50,
)
METAL_CLAW = _move(
    "metal-claw",
    "Garra Metal",
    "steel",
    PHYSICAL,
    50,
    95,
    35,
    category="damage-raise",
    stat_changes=(StatChange("atk", 1),),
    stat_chance=10,
)
GIGA_DRAIN = _move(
    "giga-drain", "Gigadrenado", "grass", SPECIAL, 75, 100, 10, category="damage-heal", drain=50
)
DOUBLE_EDGE = _move("double-edge", "Doble Filo", "normal", PHYSICAL, 120, 100, 15, drain=-33)
FURY_ATTACK = _move(
    "fury-attack", "Ataque Furia", "normal", PHYSICAL, 15, 85, 20, min_hits=2, max_hits=5
)
DOUBLE_KICK = _move(
    "double-kick", "Doble Patada", "fighting", PHYSICAL, 30, 100, 30, min_hits=2, max_hits=2
)
SLASH = _move("slash", "Cuchillada", "normal", PHYSICAL, 70, 100, 20, crit_rate=1)
FALSE_SWIPE_MOVE = _move("false-swipe", "Falso Tortazo", "normal", PHYSICAL, 40, 100, 40)
WRAP = _move(
    "wrap",
    "Constricción",
    "normal",
    PHYSICAL,
    15,
    90,
    20,
    category="damage-ailment",
    ailment="trap",
    ailment_chance=100,
    min_turns=2,
    max_turns=4,
)
CONFUSION = _move(
    "confusion",
    "Confusión",
    "psychic",
    SPECIAL,
    50,
    100,
    25,
    category="damage-ailment",
    ailment="confusion",
    ailment_chance=10,
)
DRAGON_RAGE = _move("dragon-rage", "Furia Dragón", "dragon", SPECIAL, None, 100, 10)
SONIC_BOOM = _move("sonic-boom", "Bomba Sónica", "normal", SPECIAL, None, 90, 20)
SEISMIC_TOSS = _move("seismic-toss", "Sísmico", "fighting", PHYSICAL, None, 100, 20)
NIGHT_SHADE = _move("night-shade", "Tinieblas", "ghost", SPECIAL, None, 100, 15)
SUPER_FANG = _move("super-fang", "Superdiente", "normal", PHYSICAL, None, 90, 10)
FISSURE = _move("fissure", "Fisura", "ground", PHYSICAL, None, 30, 5, category="ohko")
SOLAR_BEAM = _move("solar-beam", "Rayo Solar", "grass", SPECIAL, 120, 100, 10)
FLY = _move("fly", "Vuelo", "flying", PHYSICAL, 90, 95, 15)
HYPER_BEAM = _move("hyper-beam", "Hiperrayo", "normal", SPECIAL, 150, 90, 5)
EXPLOSION = _move("explosion", "Explosión", "normal", PHYSICAL, 250, 100, 5)
DREAM_EATER = _move(
    "dream-eater", "Comesueños", "psychic", SPECIAL, 100, 100, 15, category="damage-heal", drain=50
)
DRAGON_TAIL = _move(
    "dragon-tail",
    "Cola Dragón",
    "dragon",
    PHYSICAL,
    60,
    90,
    10,
    priority=-6,
    category="force-switch",
)
SWIFT = _move("swift", "Meteoros", "normal", SPECIAL, 60, None, 20)
# PokeAPI sin ``meta``: la caché lo guarda como daño plano.
HEADLONG_RUSH = _move("headlong-rush", "Arremetida", "ground", PHYSICAL, 120, 100, 5)
THUNDER_WAVE = _move(
    "thunder-wave",
    "Onda Trueno",
    "electric",
    STATUS,
    None,
    90,
    20,
    category="ailment",
    ailment="paralysis",
)
HYPNOSIS = _move(
    "hypnosis", "Hipnosis", "psychic", STATUS, None, 60, 20, category="ailment", ailment="sleep"
)
TOXIC = _move(
    "toxic", "Tóxico", "poison", STATUS, None, 90, 10, category="ailment", ailment="poison"
)
WILL_O_WISP = _move(
    "will-o-wisp", "Fuego Fatuo", "fire", STATUS, None, 85, 15, category="ailment", ailment="burn"
)
CONFUSE_RAY = _move(
    "confuse-ray",
    "Rayo Confuso",
    "ghost",
    STATUS,
    None,
    100,
    10,
    category="ailment",
    ailment="confusion",
)
LEECH_SEED = _move(
    "leech-seed",
    "Drenadoras",
    "grass",
    STATUS,
    None,
    90,
    10,
    category="ailment",
    ailment="leech-seed",
)
SWORDS_DANCE = _move(
    "swords-dance",
    "Danza Espada",
    "normal",
    STATUS,
    None,
    None,
    20,
    target="user",
    category="net-good-stats",
    stat_changes=(StatChange("atk", 2),),
)
GROWL = _move(
    "growl",
    "Gruñido",
    "normal",
    STATUS,
    None,
    100,
    40,
    target="all-opponents",
    category="net-good-stats",
    stat_changes=(StatChange("atk", -1),),
)
DOUBLE_TEAM = _move(
    "double-team",
    "Doble Equipo",
    "normal",
    STATUS,
    None,
    None,
    15,
    target="user",
    category="net-good-stats",
    stat_changes=(StatChange("evasion", 1),),
)
RECOVER = _move(
    "recover",
    "Recuperación",
    "normal",
    STATUS,
    None,
    None,
    5,
    target="user",
    category="heal",
    healing=50,
)
HEAL_PULSE = _move(
    "heal-pulse", "Pulso Cura", "psychic", STATUS, None, None, 10, category="heal", healing=50
)
AROMATIC_MIST = _move(
    "aromatic-mist",
    "Niebla Aromática",
    "fairy",
    STATUS,
    None,
    None,
    20,
    target="ally",
    category="net-good-stats",
    stat_changes=(StatChange("spd", 1),),
)
SWAGGER = _move(
    "swagger",
    "Contoneo",
    "normal",
    STATUS,
    None,
    85,
    15,
    category="swagger",
    ailment="confusion",
    stat_changes=(StatChange("atk", 2),),
)
PROTECT = _move(
    "protect",
    "Protección",
    "normal",
    STATUS,
    None,
    None,
    10,
    priority=4,
    target="user",
    category="unique",
    ailment="protect",
)
REST = _move("rest", "Descanso", "psychic", STATUS, None, None, 5, target="user", category="unique")
REFLECT = _move(
    "reflect",
    "Reflejo",
    "psychic",
    STATUS,
    None,
    None,
    20,
    target="users-field",
    category="field-effect",
)
LIGHT_SCREEN = _move(
    "light-screen",
    "Pantalla Luz",
    "psychic",
    STATUS,
    None,
    None,
    30,
    target="users-field",
    category="field-effect",
)
HAZE = _move(
    "haze",
    "Niebla",
    "ice",
    STATUS,
    None,
    None,
    30,
    target="entire-field",
    category="whole-field-effect",
)
FOCUS_ENERGY = _move(
    "focus-energy",
    "Foco Energía",
    "normal",
    STATUS,
    None,
    None,
    30,
    target="user",
    category="unique",
)
SPLASH = _move(
    "splash", "Salpicadura", "normal", STATUS, None, None, 40, target="user", category="unique"
)
TELEPORT = _move(
    "teleport",
    "Teletransporte",
    "psychic",
    STATUS,
    None,
    None,
    20,
    priority=-6,
    target="user",
    category="unique",
)
ROAR = _move(
    "roar", "Rugido", "normal", STATUS, None, None, 20, priority=-6, category="force-switch"
)
# Sin efecto implementado: nunca deben romper el turno.
TRANSFORM = _move(
    "transform", "Transformación", "normal", STATUS, None, None, 10, category="unique"
)
LOW_KICK = _move("low-kick", "Patada Baja", "fighting", PHYSICAL, None, 100, 20)
MEAN_LOOK = _move(
    "mean-look", "Mal de Ojo", "normal", STATUS, None, None, 5, category="ailment", ailment="trap"
)
TIDY_UP = _move("tidy-up", "Limpieza", "normal", STATUS, None, None, 10, target="user")

CATALOG: tuple[Move, ...] = (
    TACKLE,
    QUICK_ATTACK,
    FAKE_OUT,
    EMBER,
    WATER_GUN,
    THUNDER_SHOCK,
    ICE_BEAM,
    BITE,
    ROCK_SMASH,
    METAL_CLAW,
    GIGA_DRAIN,
    DOUBLE_EDGE,
    FURY_ATTACK,
    DOUBLE_KICK,
    SLASH,
    FALSE_SWIPE_MOVE,
    WRAP,
    CONFUSION,
    DRAGON_RAGE,
    SONIC_BOOM,
    SEISMIC_TOSS,
    NIGHT_SHADE,
    SUPER_FANG,
    FISSURE,
    SOLAR_BEAM,
    FLY,
    HYPER_BEAM,
    EXPLOSION,
    DREAM_EATER,
    DRAGON_TAIL,
    SWIFT,
    HEADLONG_RUSH,
    THUNDER_WAVE,
    HYPNOSIS,
    TOXIC,
    WILL_O_WISP,
    CONFUSE_RAY,
    LEECH_SEED,
    SWORDS_DANCE,
    GROWL,
    DOUBLE_TEAM,
    RECOVER,
    HEAL_PULSE,
    AROMATIC_MIST,
    SWAGGER,
    PROTECT,
    REST,
    REFLECT,
    LIGHT_SCREEN,
    HAZE,
    FOCUS_ENERGY,
    SPLASH,
    TELEPORT,
    ROAR,
    TRANSFORM,
    LOW_KICK,
    MEAN_LOOK,
    TIDY_UP,
)


# --- Dobles y constructores -------------------------------------------------


class ScriptedRandom:
    """Azar guionizado: entrega valores fijados y falla si se pide uno de más."""

    def __init__(self, *, floats: Sequence[float] = (), ints: Sequence[int] = ()) -> None:
        self.floats = list(floats)
        self.ints = list(ints)

    def random(self) -> float:
        assert self.floats, "se pidió un random() no guionizado"
        return self.floats.pop(0)

    def randint(self, low: int, high: int) -> int:
        assert self.ints, f"se pidió un randint({low}, {high}) no guionizado"
        value = self.ints.pop(0)
        assert low <= value <= high, (low, high, value)
        return value

    @property
    def exhausted(self) -> bool:
        return not self.floats and not self.ints


def mon(
    key: str = "p0",
    *moves: Move,
    level: int = 50,
    types: tuple[str, ...] = ("normal",),
    speed: int | None = None,
    max_hp: int = 100,
    hp: int | None = None,
    status: Status = Status.NONE,
    status_turns: int = 0,
    stages: Stages = Stages(),
    volatile: Volatile = Volatile(),
    base_experience: int = 64,
) -> Combatant:
    """Combatiente con stats de 100 y PS a medida.

    Si no se indica velocidad, el jugador (``p…``) va a 120 y el rival a 80:
    el jugador actúa primero salvo que la prueba diga otra cosa.
    """
    spe = speed if speed is not None else (120 if key.startswith("p") else 80)
    stats = Stats(hp=max_hp, atk=100, defense=100, spa=100, spd=100, spe=spe)
    chosen = moves or (TACKLE,)
    return Combatant(
        key=key,
        species=f"especie-{key}",
        form="default",
        name=key.upper(),
        level=level,
        types=types,
        base_stats=stats,
        ivs=Stats(hp=0, atk=0, defense=0, spa=0, spd=0, spe=0),
        nature="hardy",
        stats=stats,
        hp=max_hp if hp is None else hp,
        moves=tuple(MoveSlot(move=move, pp=move.pp, max_pp=move.pp) for move in chosen),
        status=status,
        status_turns=status_turns,
        stages=stages,
        volatile=volatile,
        base_experience=base_experience,
    )


def with_pp(c: Combatant, slot: int, pp: int) -> Combatant:
    slots = list(c.moves)
    slots[slot] = replace(slots[slot], pp=pp)
    return replace(c, moves=tuple(slots))


def duel(
    player: Combatant,
    opponent: Combatant,
    *,
    bench: Sequence[Combatant] = (),
    foes: Sequence[Combatant] = (),
    kind: BattleKind = BattleKind.WILD,
    turn: int = 0,
) -> BattleState:
    """Combate ya empezado: el activo del jugador ha visto al rival activo."""
    trainer = "Montañero Anselmo" if kind is BattleKind.TRAINER else None
    return BattleState(
        kind=kind,
        player=Side(team=(player, *bench)),
        opponent=Side(team=(opponent, *foes), trainer=trainer),
        turn=turn,
        participants=((opponent.key, (player.key,)),),
        award_experience=kind is not BattleKind.EXHIBITION,
    )


def play(
    state: BattleState,
    *,
    player: Action = UseMove(0),
    opponent: Action | None = UseMove(0),
    floats: Sequence[float] = (),
    ints: Sequence[int] = (),
) -> TurnResult:
    """Un turno con tiradas guionizadas que deben gastarse todas."""
    rng = ScriptedRandom(floats=floats, ints=ints)
    result = resolve_turn(state, player, rng, opponent_action=opponent)
    assert rng.exhausted, f"sobran tiradas: {rng.floats} {rng.ints}"
    return result


def kinds(events: Sequence[BattleEvent]) -> list[str]:
    return [type(event).__name__ for event in events]


def only(events: Sequence[BattleEvent], kind: type[_E]) -> list[_E]:
    return [event for event in events if isinstance(event, kind)]


def damage_of(
    attacker: Combatant,
    defender: Combatant,
    move: Move,
    *,
    roll: int = 100,
    critical: bool = False,
    screen: bool = False,
) -> int:
    return calculate_damage(attacker, defender, move, critical=critical, roll=roll, screen=screen)


def player_of(result: TurnResult, index: int | None = None) -> Combatant:
    side = result.state.player
    return side.current if index is None else side.team[index]


def opponent_of(result: TurnResult, index: int | None = None) -> Combatant:
    side = result.state.opponent
    return side.current if index is None else side.team[index]


# --- Apertura y consultas ---------------------------------------------------


def test_begin_announces_both_sides_and_registers_the_first_matchup() -> None:
    state = BattleState(
        kind=BattleKind.TRAINER,
        player=Side(team=(mon("p0"),)),
        opponent=Side(team=(mon("o0"),), trainer="Pescador Dámaso"),
    )
    result = begin(state)
    assert result.events == (
        BattleStarted(kind=BattleKind.TRAINER, trainer="Pescador Dámaso"),
        SentOut("opponent", "o0", "O0", "especie-o0", "default", False, 50),
        SentOut("player", "p0", "P0", "especie-p0", "default", False, 50),
    )
    assert result.state.participants == (("o0", ("p0",)),)
    assert result.state.turn == 0


def test_queries_for_the_interface() -> None:
    tired = with_pp(mon("p0", TACKLE, EMBER), 0, 0)
    state = duel(tired, mon("o0"), bench=(mon("p1"), mon("p2", hp=0)))
    assert available_moves(tired) == (1,)
    assert switch_targets(state, "player") == (1,)
    assert switch_targets(state, "opponent") == ()
    assert can_run(state)
    assert not can_run(replace(state, kind=BattleKind.TRAINER))
    assert not can_run(replace(state, kind=BattleKind.EXHIBITION))


@pytest.mark.parametrize(
    ("action", "reason"),
    [
        (UseMove(4), "Ese movimiento no existe."),
        (UseMove(0), "¡No quedan PP para este movimiento!"),
        (SwitchTo(0), "¡P0 ya está combatiendo!"),
        (SwitchTo(2), "¡P2 no tiene fuerzas para luchar!"),
        (SwitchTo(7), "Ese Pokémon no está en el equipo."),
    ],
)
def test_invalid_actions_raise_with_a_spanish_reason(action: Action, reason: str) -> None:
    state = duel(
        with_pp(mon("p0", TACKLE, EMBER), 0, 0), mon("o0"), bench=(mon("p1"), mon("p2", hp=0))
    )
    assert action_error(state, action) == reason
    with pytest.raises(ValueError, match=re.escape(reason)):
        resolve_turn(state, action, ScriptedRandom(), opponent_action=UseMove(0))


def test_actions_outside_the_choice_phase_are_rejected() -> None:
    state = duel(mon("p0"), mon("o0"), bench=(mon("p1"),))
    finished = replace(state, phase=Phase.FINISHED, outcome=Outcome.WON)
    replacing = replace(state, phase=Phase.CHOOSE_REPLACEMENT)
    assert action_error(finished, UseMove(0)) == "El combate ya ha terminado."
    assert action_error(replacing, UseMove(0)) == "Antes tienes que sacar a otro Pokémon."
    for bad in (finished, replacing):
        with pytest.raises(ValueError):
            resolve_turn(bad, UseMove(0), ScriptedRandom(), opponent_action=UseMove(0))
    with pytest.raises(ValueError, match="rival"):
        resolve_turn(state, UseMove(0), ScriptedRandom(), opponent_action=Run())


# --- Orden y daño básico ----------------------------------------------------


def test_a_basic_exchange_tells_the_whole_turn() -> None:
    player, opponent = mon("p0"), mon("o0")
    result = play(duel(player, opponent), floats=(HIT, NO_CRIT, HIT, NO_CRIT), ints=(100, 85))
    first = damage_of(player, opponent, TACKLE, roll=100)
    second = damage_of(opponent, player, TACKLE, roll=85)
    assert result.events == (
        TurnStarted(turn=1),
        MoveUsed("player", "p0", "P0", "tackle", "Placaje", "normal", PHYSICAL),
        Damaged("opponent", "o0", "O0", first, 100, 100 - first, 100),
        MoveUsed("opponent", "o0", "O0", "tackle", "Placaje", "normal", PHYSICAL),
        Damaged("player", "p0", "P0", second, 100, 100 - second, 100),
    )
    assert result.state.turn == 1
    assert player_of(result).moves[0].pp == TACKLE.pp - 1
    assert opponent_of(result).hp == 100 - first


def test_priority_beats_speed_and_a_tie_is_decided_by_one_roll() -> None:
    state = duel(mon("p0", QUICK_ATTACK, speed=50), mon("o0", TACKLE, speed=200))
    result = play(state, floats=(HIT, NO_CRIT, HIT, NO_CRIT), ints=(100, 100))
    assert [e.side for e in only(result.events, MoveUsed)] == ["player", "opponent"]
    tie = duel(mon("p0", speed=90), mon("o0", speed=90))
    # 0,7 ≥ 0,5: empieza el rival.
    result = play(tie, floats=(0.7, HIT, NO_CRIT, HIT, NO_CRIT), ints=(100, 100))
    assert [e.side for e in only(result.events, MoveUsed)] == ["opponent", "player"]


def test_a_miss_and_a_critical_hit() -> None:
    player, opponent = mon("p0", HYPER_BEAM), mon("o0", SLASH)
    result = play(duel(player, opponent), floats=(0.95, HIT, CRIT), ints=(90,))
    assert MoveMissed("player", "p0", "P0") in result.events
    (hit,) = only(result.events, Damaged)
    assert hit == Damaged(
        "player",
        "p0",
        "P0",
        damage_of(opponent, player, SLASH, roll=90, critical=True),
        100,
        100 - damage_of(opponent, player, SLASH, roll=90, critical=True),
        100,
        critical=True,
    )
    # Hiperrayo fallido no obliga a recargar.
    assert not player_of(result).volatile.recharging


def test_effectiveness_and_immunity_reach_the_events() -> None:
    player = mon("p0", WATER_GUN, TACKLE)
    result = play(
        duel(player, mon("o0", SPLASH, types=("fire",))), floats=(HIT, NO_CRIT), ints=(100,)
    )
    (hit,) = only(result.events, Damaged)
    assert hit.effectiveness == 2.0
    ghost = duel(player, mon("o0", SPLASH, types=("ghost",)))
    result = play(ghost, player=UseMove(1))
    assert NoEffect("opponent", "o0", "O0") in result.events
    assert not only(result.events, Damaged)


# --- Forcejeo, cambios y huida ----------------------------------------------


def test_struggle_without_pp_is_typeless_and_hurts_the_user() -> None:
    player = with_pp(mon("p0"), 0, 0)
    ghost = mon("o0", SPLASH, types=("ghost",))
    result = play(duel(player, ghost), floats=(NO_CRIT,), ints=(100,))
    hit = damage_of(player, ghost, STRUGGLE)
    assert kinds(result.events) == [
        "TurnStarted",
        "NoMovesLeft",
        "MoveUsed",
        "Damaged",
        "Damaged",
        "MoveUsed",
        "MoveFailed",
    ]
    assert result.events[1] == NoMovesLeft("player", "p0", "P0")
    assert only(result.events, MoveUsed)[0].move == "struggle"
    assert result.events[3] == Damaged("opponent", "o0", "O0", hit, 100, 100 - hit, 100)
    assert result.events[4] == Damaged("player", "p0", "P0", 25, 100, 75, 100, DamageCause.RECOIL)
    assert player_of(result).moves[0].pp == 0


def test_switching_goes_first_resets_the_volatile_state_and_counts_for_experience() -> None:
    worn = mon(
        "p0",
        status=Status.TOXIC,
        status_turns=4,
        stages=Stages(atk=2),
        volatile=Volatile(confusion_turns=3, focus_energy=True),
    )
    fresh = mon("p1", speed=10)
    state = duel(worn, mon("o0", speed=200), bench=(fresh,))
    result = play(state, player=SwitchTo(1), floats=(HIT, NO_CRIT), ints=(100,))
    assert kinds(result.events)[:4] == ["TurnStarted", "Recalled", "SentOut", "MoveUsed"]
    assert result.events[1] == Recalled("player", "p0", "P0")
    assert only(result.events, Damaged)[0].key == "p1"
    benched = player_of(result, 0)
    assert benched.stages == Stages() and benched.volatile == Volatile()
    assert benched.status is Status.TOXIC and benched.status_turns == 1
    assert result.state.player.active == 1
    assert result.state.participants == (("o0", ("p0", "p1")),)


def test_running_from_a_slower_wild_pokemon_always_works() -> None:
    result = play(duel(mon("p0"), mon("o0")), player=Run())
    assert result.events == (
        TurnStarted(turn=1),
        EscapeAttempted(success=True),
        BattleEnded(outcome=Outcome.ESCAPED),
    )
    assert result.state.finished and result.state.outcome is Outcome.ESCAPED


def test_a_failed_escape_counts_the_attempt_and_the_wild_pokemon_attacks() -> None:
    state = duel(mon("p0", speed=50), mon("o0", speed=100), turn=3)
    # F = 50·128 // 100 + 30·1 = 94; 200 no es menor: no escapa.
    result = play(state, player=Run(), floats=(HIT, NO_CRIT), ints=(200, 100))
    assert EscapeAttempted(success=False) in result.events
    assert result.state.escape_attempts == 1
    assert only(result.events, MoveUsed)[0].side == "opponent"
    # El segundo intento suma 30: F = 124 y 100 ya basta.
    again = play(result.state, player=Run(), ints=(100,))
    assert again.state.outcome is Outcome.ESCAPED


def test_there_is_no_running_from_a_trainer_and_no_turn_is_spent() -> None:
    state = duel(mon("p0"), mon("o0"), kind=BattleKind.TRAINER)
    result = play(state, player=Run(), opponent=None)
    assert result.state is state
    assert result.events == (EscapeAttempted(success=False, forbidden=True),)


# --- Desmayos, experiencia y relevos ----------------------------------------


def _trainee(key: str = "p0", *, level: int = 10, moves: Sequence[Move] = (TACKLE,)) -> Combatant:
    """Squirtle de nivel 10 con EXP exacta y learnset: aprende Pistola Agua al 11."""
    return build_combatant(
        key=key,
        species="squirtle",
        form="squirtle",
        name="Squirtle",
        level=level,
        types=("water",),
        base_stats=Stats(hp=80, atk=80, defense=80, spa=80, spd=80, spe=150),
        ivs=Stats(hp=10, atk=10, defense=10, spa=10, spd=10, spe=10),
        nature="hardy",
        moves=moves,
        experience=1000,
        learnset=(LearnsetEntry(1, TACKLE), LearnsetEntry(11, WATER_GUN)),
        capture_id=7,
    )


def test_defeating_the_wild_pokemon_gives_experience_levels_and_moves() -> None:
    trainee = _trainee()
    wild = mon("o0", SPLASH, level=10, hp=1, base_experience=255, speed=1)
    result = play(duel(trainee, wild), floats=(HIT, NO_CRIT), ints=(100,))
    amount = experience_yield(255, 10, 1, trainer=False)
    grown, deltas = level_up(replace(trainee, experience=1000 + amount), 11)
    assert kinds(result.events) == [
        "TurnStarted",
        "MoveUsed",
        "Damaged",
        "Fainted",
        "ExperienceGained",
        "LeveledUp",
        "MoveLearned",
        "BattleEnded",
    ]
    assert result.events[3] == Fainted("opponent", "o0", "O0")
    assert result.events[4] == ExperienceGained("p0", "Squirtle", amount)
    assert result.events[5] == LeveledUp("p0", "Squirtle", 11, deltas)
    assert result.events[6] == MoveLearned("p0", "Squirtle", "Pistola Agua", None)
    assert result.events[7] == BattleEnded(Outcome.WON)
    final = player_of(result)
    assert (final.level, final.experience) == (11, 1000 + amount)
    assert final.max_hp == grown.max_hp
    assert [slot.move.slug for slot in final.moves] == ["tackle", "water-gun"]


def test_experience_is_split_among_living_participants_and_trainers_pay_more() -> None:
    one, two, out = _trainee("p0"), _trainee("p1"), _trainee("p2", level=12)
    foe = mon("o0", SPLASH, level=20, hp=1, base_experience=100)
    state = replace(
        duel(one, foe, bench=(two, replace(out, hp=0)), kind=BattleKind.TRAINER),
        participants=(("o0", ("p0", "p1", "p2")),),
    )
    result = play(state, floats=(HIT, NO_CRIT), ints=(100,))
    share = experience_yield(100, 20, 2, trainer=True)
    assert [(e.key, e.amount) for e in only(result.events, ExperienceGained)] == [
        ("p0", share),
        ("p1", share),
    ]
    assert share == 100 * 20 // 7 // 2 * 3 // 2


def _mid_turn_level_up(learnset: tuple[LearnsetEntry, ...]) -> BattleState:
    """El rival, más rápido y confuso a 1 PS, se hiere y se debilita antes de
    que actúe el jugador, que sube al 11 y recalcula su moveset automático."""
    trainee = replace(_trainee(moves=(TACKLE, EMBER, WATER_GUN, GROWL)), learnset=learnset)
    dazed = mon("o0", SPLASH, hp=1, speed=99, base_experience=255, level=10,
                volatile=Volatile(confusion_turns=3))  # fmt: skip
    return duel(trainee, dazed, foes=(mon("o1", SPLASH),), kind=BattleKind.TRAINER)


def test_a_move_forgotten_mid_turn_is_not_used() -> None:
    # Al 11 solo puede aprender Placaje: Gruñido (hueco 3) se olvida.
    state = _mid_turn_level_up((LearnsetEntry(1, TACKLE),))
    result = play(state, player=UseMove(3), floats=(0.1,))
    assert kinds(result.events) == [
        "TurnStarted",
        "ConfusionCheck",
        "ConfusionHurt",
        "Damaged",
        "Fainted",
        "ExperienceGained",
        "LeveledUp",
        "SentOut",
    ]
    assert [slot.move.slug for slot in player_of(result).moves] == ["tackle"]


def test_a_move_that_shifts_slot_mid_turn_is_still_the_one_used() -> None:
    # Al 11 se queda con Placaje y Pistola Agua, que pasa del hueco 2 al 1.
    state = _mid_turn_level_up((LearnsetEntry(1, TACKLE), LearnsetEntry(1, WATER_GUN)))
    result = play(state, player=UseMove(2), floats=(0.1,))
    used = only(result.events, MoveUsed)
    assert [event.move for event in used] == ["water-gun"]
    # El rival ya no está: «¡Pero falló!», pero gasta el PP del movimiento elegido.
    assert MoveFailed("player", "p0", "Squirtle", FailReason.GENERIC) in result.events
    moves = player_of(result).moves
    assert [(slot.move.slug, slot.pp) for slot in moves] == [
        ("tackle", TACKLE.pp),
        ("water-gun", WATER_GUN.pp - 1),
    ]


def test_exhibitions_award_no_experience() -> None:
    state = duel(_trainee(), mon("o0", SPLASH, hp=1), kind=BattleKind.EXHIBITION)
    result = play(state, floats=(HIT, NO_CRIT), ints=(100,))
    assert not only(result.events, ExperienceGained)
    assert result.state.outcome is Outcome.WON
    assert player_of(result).experience == 1000


def test_a_trainer_sends_the_next_pokemon_at_the_end_of_the_turn() -> None:
    state = duel(mon("p0"), mon("o0", hp=1), foes=(mon("o1"),), kind=BattleKind.TRAINER)
    result = play(state, floats=(HIT, NO_CRIT), ints=(100,))
    assert kinds(result.events)[-2:] == ["ExperienceGained", "SentOut"]
    assert result.events[-1] == SentOut("opponent", "o1", "O1", "especie-o1", "default", False, 50)
    assert result.state.phase is Phase.CHOOSE_ACTION
    assert result.state.opponent.active == 1
    assert dict(result.state.participants)["o1"] == ("p0",)
    final = play(
        replace(result.state, opponent=result.state.opponent.with_current(mon("o1", hp=1))),
        floats=(HIT, NO_CRIT),
        ints=(100,),
    )
    assert final.events[-1] == BattleEnded(Outcome.WON)


def test_a_fainted_player_pokemon_asks_for_a_replacement() -> None:
    state = duel(mon("p0", hp=1, speed=10), mon("o0", speed=99), bench=(mon("p1"),))
    result = play(state, floats=(HIT, NO_CRIT), ints=(100,))
    assert kinds(result.events)[-2:] == ["Fainted", "ReplacementNeeded"]
    assert only(result.events, MoveUsed)[0].side == "opponent"
    assert result.state.phase is Phase.CHOOSE_REPLACEMENT
    with pytest.raises(ValueError):
        resolve_turn(result.state, UseMove(0), ScriptedRandom(), opponent_action=UseMove(0))
    with pytest.raises(ValueError, match="no tiene fuerzas"):
        choose_replacement(result.state, 0, ScriptedRandom())
    chosen = choose_replacement(result.state, 1, ScriptedRandom())
    assert chosen.events == (SentOut("player", "p1", "P1", "especie-p1", "default", False, 50),)
    assert chosen.state.phase is Phase.CHOOSE_ACTION
    assert chosen.state.turn == result.state.turn
    assert chosen.state.participants == (("o0", ("p0", "p1")),)
    with pytest.raises(ValueError, match="no hace falta"):
        choose_replacement(chosen.state, 0, ScriptedRandom())


def test_losing_the_last_pokemon_ends_the_battle() -> None:
    state = duel(mon("p0", hp=1, speed=10), mon("o0", speed=99))
    result = play(state, floats=(HIT, NO_CRIT), ints=(100,))
    assert kinds(result.events)[-2:] == ["Fainted", "BattleEnded"]
    assert result.state.outcome is Outcome.LOST


def test_explosion_knocking_out_both_last_pokemon_is_a_draw() -> None:
    bomber = _trainee(moves=(EXPLOSION,))
    state = duel(bomber, mon("o0", SPLASH, hp=5, speed=1))
    result = play(state, floats=(HIT, NO_CRIT), ints=(100,))
    assert kinds(result.events) == [
        "TurnStarted",
        "MoveUsed",
        "Damaged",
        "Damaged",
        "Fainted",
        "Fainted",
        "BattleEnded",
    ]
    assert only(result.events, Damaged)[0].cause is DamageCause.SACRIFICE
    assert [e.side for e in only(result.events, Fainted)] == ["opponent", "player"]
    assert result.state.outcome is Outcome.DRAW
    # El que explota está debilitado: no gana EXP.
    assert not only(result.events, ExperienceGained)


def test_explosion_against_protection_still_knocks_out_the_user() -> None:
    state = duel(mon("p0", EXPLOSION), mon("o0", PROTECT), bench=(mon("p1"),))
    result = play(state)
    assert kinds(result.events) == [
        "TurnStarted",
        "MoveUsed",
        "ProtectionRaised",
        "MoveUsed",
        "Damaged",
        "Protected",
        "Fainted",
        "ReplacementNeeded",
    ]


def test_a_move_without_a_target_fails() -> None:
    reckless = mon("p0", DOUBLE_EDGE, hp=10)
    state = duel(reckless, mon("o0", TACKLE, SWORDS_DANCE), bench=(mon("p1"),))
    result = play(state, floats=(HIT, NO_CRIT), ints=(85,))
    assert Fainted("player", "p0", "P0") in result.events
    assert result.events[-3:] == (
        MoveUsed("opponent", "o0", "O0", "tackle", "Placaje", "normal", PHYSICAL),
        MoveFailed("opponent", "o0", "O0", FailReason.GENERIC),
        ReplacementNeeded("player"),
    )
    boost = play(state, opponent=UseMove(1), floats=(HIT, NO_CRIT), ints=(85,))
    assert StatChanged("opponent", "o0", "O0", "atk", 2) in boost.events


def test_reaching_the_turn_limit_is_a_draw() -> None:
    state = duel(mon("p0", SPLASH), mon("o0", SPLASH), turn=MAX_TURNS - 1)
    result = play(state)
    assert result.state.turn == MAX_TURNS
    assert result.events[-1] == BattleEnded(Outcome.DRAW)
    assert result.state.outcome is Outcome.DRAW


def test_finish_closes_a_battle_decided_outside_the_engine() -> None:
    state = duel(mon("p0"), mon("o0"))
    ball = BallThrown("poke-ball", "Poké Ball", 3, True)
    result = finish(state, Outcome.CAPTURED, (ball,))
    assert result.events == (ball, BattleEnded(Outcome.CAPTURED))
    assert result.state.phase is Phase.FINISHED and result.state.outcome is Outcome.CAPTURED
    with pytest.raises(ValueError):
        finish(result.state, Outcome.WON)


def test_spending_the_turn_lets_only_the_opponent_act() -> None:
    result = play(
        duel(mon("p0"), mon("o0")), player=SpendTurn(), floats=(HIT, NO_CRIT), ints=(100,)
    )
    assert [e.side for e in only(result.events, MoveUsed)] == ["opponent"]


def test_without_an_opponent_action_the_ai_chooses_it() -> None:
    state = duel(mon("p0", SPLASH), mon("o0", SPLASH, TACKLE))
    rng = ScriptedRandom(floats=(HIT, NO_CRIT), ints=(1, 100))
    result = resolve_turn(state, UseMove(0), rng)
    assert rng.exhausted
    assert only(result.events, MoveUsed)[1].move == "tackle"


def test_turns_are_pure_and_reproducible() -> None:
    state = duel(mon("p0", EMBER, BITE, FURY_ATTACK), mon("o0", THUNDER_SHOCK, WRAP, TACKLE))
    first = resolve_turn(state, UseMove(2), random.Random(11))
    second = resolve_turn(state, UseMove(2), random.Random(11))
    assert first == second
    assert state == duel(
        mon("p0", EMBER, BITE, FURY_ATTACK), mon("o0", THUNDER_SHOCK, WRAP, TACKLE)
    )


# --- Problemas de estado ----------------------------------------------------


def test_paralysis_quarters_speed_and_sometimes_stops_the_move() -> None:
    state = duel(mon("p0", status=Status.PARALYSIS, speed=120), mon("o0", SPLASH, speed=80))
    stuck = play(state, floats=(0.1,))
    # 120/4 = 30 < 80: el rival actúa antes.
    assert kinds(stuck.events) == ["TurnStarted", "MoveUsed", "MoveFailed", "Immobilized"]
    assert stuck.events[-1] == Immobilized("player", "p0", "P0", ImmobileReason.PARALYZED)
    assert player_of(stuck).moves[0].pp == TACKLE.pp
    moves = play(state, floats=(0.5, HIT, NO_CRIT), ints=(100,))
    assert only(moves.events, Damaged)


def test_sleep_lasts_its_counter_and_the_sleeper_wakes_up_and_acts() -> None:
    state = duel(mon("p0", speed=10), mon("o0", HYPNOSIS, SPLASH, speed=99))
    asleep = play(state, floats=(HIT,), ints=(1,))
    assert StatusInflicted("player", "p0", "P0", Status.SLEEP) in asleep.events
    assert asleep.events[-1] == Immobilized("player", "p0", "P0", ImmobileReason.ASLEEP)
    assert (player_of(asleep).status, player_of(asleep).status_turns) == (Status.SLEEP, 0)
    awake = play(asleep.state, opponent=UseMove(1), floats=(HIT, NO_CRIT), ints=(100,))
    assert kinds(awake.events)[-3:] == ["StatusCured", "MoveUsed", "Damaged"]
    assert player_of(awake).status is Status.NONE


def test_freeze_thaws_by_chance_by_an_own_fire_move_or_by_fire_damage() -> None:
    frozen = mon("p0", TACKLE, EMBER, status=Status.FREEZE)
    state = duel(frozen, mon("o0", SPLASH))
    still = play(state, floats=(0.5,))
    assert Immobilized("player", "p0", "P0", ImmobileReason.FROZEN) in still.events
    thawed = play(state, floats=(0.1, HIT, NO_CRIT), ints=(100,))
    assert kinds(thawed.events)[1:3] == ["StatusCured", "MoveUsed"]
    by_fire = play(state, player=UseMove(1), floats=(HIT, NO_CRIT, 0.99), ints=(100,))
    assert kinds(by_fire.events)[1:3] == ["StatusCured", "MoveUsed"]
    target = duel(mon("p0", EMBER), mon("o0", SPLASH, status=Status.FREEZE))
    melted = play(target, floats=(HIT, NO_CRIT, 0.99), ints=(100,))
    assert kinds(melted.events)[1:4] == ["MoveUsed", "Damaged", "StatusCured"]
    assert MoveFailed("opponent", "o0", "O0", FailReason.NOTHING_HAPPENED) in melted.events


def test_burn_halves_physical_damage_and_burn_and_poison_hurt_an_eighth() -> None:
    burned = mon("p0", status=Status.BURN)
    result = play(
        duel(burned, mon("o0", SPLASH, status=Status.POISON, max_hp=80)),
        floats=(HIT, NO_CRIT),
        ints=(100,),
    )
    hits = only(result.events, Damaged)
    assert hits[0].amount == damage_of(burned, mon("o0", max_hp=80), TACKLE)
    assert hits[0].amount < damage_of(mon("p0"), mon("o0", max_hp=80), TACKLE)
    assert hits[1] == Damaged("player", "p0", "P0", 12, 100, 88, 100, DamageCause.BURN)
    assert hits[2].cause is DamageCause.POISON and hits[2].amount == 10


def test_toxic_damage_grows_each_turn_and_resets_on_switch() -> None:
    state = duel(mon("p0", SPLASH), mon("o0", TOXIC, SPLASH, max_hp=160))
    poisoned = play(state, floats=(HIT,))
    assert StatusInflicted("player", "p0", "P0", Status.TOXIC) in poisoned.events
    first = only(poisoned.events, Damaged)[-1]
    assert (first.cause, first.amount) == (DamageCause.POISON, 6)
    second = play(poisoned.state, opponent=UseMove(1))
    assert only(second.events, Damaged)[-1].amount == 12
    assert player_of(second).status_turns == 3


@pytest.mark.parametrize(
    ("move", "target_types", "floats", "expected"),
    [
        (WILL_O_WISP, ("fire",), (HIT,), StatusBlocked("opponent", "o0", "O0", Status.BURN, False)),
        (TOXIC, ("steel",), (HIT,), StatusBlocked("opponent", "o0", "O0", Status.TOXIC, False)),
        (
            THUNDER_WAVE,
            ("electric",),
            (HIT,),
            StatusBlocked("opponent", "o0", "O0", Status.PARALYSIS, False),
        ),
        # Gen 3: Onda Trueno respeta la tabla de tipos y no tira precisión.
        (THUNDER_WAVE, ("ground",), (), NoEffect("opponent", "o0", "O0")),
        (LEECH_SEED, ("grass",), (), NoEffect("opponent", "o0", "O0")),
    ],
)
def test_status_immunities(
    move: Move, target_types: tuple[str, ...], floats: tuple[float, ...], expected: BattleEvent
) -> None:
    state = duel(mon("p0", move), mon("o0", SPLASH, types=target_types))
    result = play(state, floats=floats)
    assert expected in result.events
    assert not only(result.events, StatusInflicted)


def test_only_one_status_at_a_time_and_secondary_effects_stay_silent() -> None:
    state = duel(mon("p0", HYPNOSIS, EMBER), mon("o0", SPLASH, status=Status.POISON))
    result = play(state, floats=(HIT,))
    assert StatusBlocked("opponent", "o0", "O0", Status.SLEEP, True) in result.events
    quiet = play(state, player=UseMove(1), floats=(HIT, NO_CRIT, 0.0), ints=(100,))
    assert not only(quiet.events, StatusBlocked) and not only(quiet.events, StatusInflicted)


def test_confusion_lasts_its_counter_and_can_hurt_the_user() -> None:
    # El rival actúa antes: aún no está confuso cuando se mueve.
    state = duel(mon("p0", CONFUSE_RAY), mon("o0", SPLASH, speed=200))
    confused = play(state, floats=(HIT,), ints=(2,))
    assert Confused("opponent", "o0", "O0") in confused.events
    assert opponent_of(confused).volatile.confusion_turns == 2
    hurt = play(
        duel(mon("p0", volatile=Volatile(confusion_turns=3)), mon("o0", SPLASH)), floats=(0.3,)
    )
    assert kinds(hurt.events)[1:4] == ["ConfusionCheck", "ConfusionHurt", "Damaged"]
    self_hit = hurt.events[3]
    assert self_hit == Damaged(
        "player",
        "p0",
        "P0",
        confusion_damage(mon("p0")),
        100,
        100 - confusion_damage(mon("p0")),
        100,
        DamageCause.CONFUSION,
    )
    assert player_of(hurt).volatile.confusion_turns == 2
    lucky = play(
        duel(mon("p0", volatile=Volatile(confusion_turns=3)), mon("o0", SPLASH)),
        floats=(0.7, HIT, NO_CRIT),
        ints=(100,),
    )
    assert kinds(lucky.events)[1:3] == ["ConfusionCheck", "MoveUsed"]
    over = play(
        duel(mon("p0", volatile=Volatile(confusion_turns=1)), mon("o0", SPLASH)),
        floats=(HIT, NO_CRIT),
        ints=(100,),
    )
    assert kinds(over.events)[1:3] == ["ConfusionEnded", "MoveUsed"]
    # Ahora sí: el rival comprueba su confusión (0,9: actúa) y el segundo
    # Rayo Confuso falla porque ya está confuso.
    again = play(confused.state, floats=(0.9, HIT))
    assert MoveFailed("player", "p0", "P0", FailReason.GENERIC) in again.events


def test_flinching_only_happens_when_the_user_moves_first() -> None:
    state = duel(mon("p0", BITE), mon("o0"))
    result = play(state, floats=(HIT, NO_CRIT, 0.1), ints=(100,))
    assert result.events[-1] == Immobilized("opponent", "o0", "O0", ImmobileReason.FLINCHED)
    assert not opponent_of(result).volatile.flinched
    slower = duel(mon("p0", BITE, speed=10), mon("o0", speed=99))
    late = play(slower, floats=(HIT, NO_CRIT, HIT, NO_CRIT), ints=(100, 100))
    assert not only(late.events, Immobilized)


def test_fake_out_only_works_on_the_first_turn_on_the_field() -> None:
    # Sin esta regla, su prioridad +3 y su retroceso seguro bloquearían al
    # rival turno tras turno.
    state = duel(
        mon("p0", FAKE_OUT, speed=10),
        mon("o0", max_hp=300, speed=200),
        bench=(mon("p1", FAKE_OUT, speed=10),),
    )
    first = play(state, floats=(HIT, NO_CRIT), ints=(100,))
    assert first.events[-1] == Immobilized("opponent", "o0", "O0", ImmobileReason.FLINCHED)
    second = play(first.state, floats=(HIT, NO_CRIT), ints=(100,))
    assert second.events[1:3] == (
        MoveUsed("player", "p0", "P0", "fake-out", "Sorpresa", "normal", PHYSICAL),
        MoveFailed("player", "p0", "P0", FailReason.GENERIC),
    )
    assert not only(second.events, Immobilized)
    assert player_of(second).moves[0].pp == FAKE_OUT.pp - 2
    # Quien acaba de entrar sí puede usarla en su primer turno.
    fresh = play(second.state, player=SwitchTo(1), floats=(HIT, NO_CRIT), ints=(100,))
    again = play(fresh.state, floats=(HIT, NO_CRIT), ints=(100,))
    assert again.events[-1] == Immobilized("opponent", "o0", "O0", ImmobileReason.FLINCHED)


# --- Movimientos de dos turnos, recarga y protección -------------------------


def test_charge_moves_take_two_turns_and_lock_the_user_in() -> None:
    state = duel(mon("p0", SOLAR_BEAM, TACKLE), mon("o0", SPLASH), bench=(mon("p1"),))
    first = play(state)
    assert kinds(first.events) == ["TurnStarted", "MoveUsed", "Charging", "MoveUsed", "MoveFailed"]
    assert first.events[2] == Charging("player", "p0", "P0", "solar-beam", "Rayo Solar")
    assert player_of(first).moves[0].pp == SOLAR_BEAM.pp - 1
    assert locked_in(first.state, "player")
    assert action_error(first.state, SwitchTo(1)) == "¡P0 no puede hacer otra cosa este turno!"
    with pytest.raises(ValueError):
        resolve_turn(first.state, Run(), ScriptedRandom(), opponent_action=UseMove(0))
    second = play(first.state, player=UseMove(1), floats=(HIT, NO_CRIT), ints=(100,))
    used = only(second.events, MoveUsed)[0]
    assert used.move == "solar-beam"
    assert only(second.events, Damaged)
    assert player_of(second).moves[0].pp == SOLAR_BEAM.pp - 1
    assert not locked_in(second.state, "player")


def test_flying_up_makes_every_attack_miss_even_those_that_never_miss() -> None:
    state = duel(mon("p0", FLY), mon("o0", TACKLE, SWIFT))
    airborne = play(state)
    assert player_of(airborne).volatile.semi_invulnerable
    assert airborne.events[-1] == MoveMissed("opponent", "o0", "O0")
    swift = play(state, opponent=UseMove(1))
    assert swift.events[-1] == MoveMissed("opponent", "o0", "O0")
    landing = play(airborne.state, floats=(HIT, NO_CRIT, HIT, NO_CRIT), ints=(100, 100))
    assert not player_of(landing).volatile.semi_invulnerable
    assert len(only(landing.events, Damaged)) == 2


def test_a_charged_move_forgotten_on_level_up_is_not_turned_into_struggle() -> None:
    # Si el rival cae mientras carga (p. ej. por veneno al final del turno),
    # el jugador sube de nivel y su moveset automático puede olvidar el golpe.
    charging = Volatile(charging="solar-beam")
    learnt = replace(mon("p0", TACKLE, volatile=charging), learnset=(LearnsetEntry(1, SOLAR_BEAM),))
    landed = play(duel(learnt, mon("o0", SPLASH, max_hp=300)), floats=(HIT, NO_CRIT), ints=(100,))
    assert [event.move for event in only(landed.events, MoveUsed)] == ["solar-beam", "splash"]
    assert only(landed.events, Damaged)[0].amount == damage_of(
        mon("p0"), mon("o0", max_hp=300), SOLAR_BEAM
    )
    assert player_of(landed).moves[0].pp == TACKLE.pp
    assert not locked_in(landed.state, "player")
    # Sin rastro del golpe, pierde la carga en vez de forcejear sin motivo.
    lost = play(duel(mon("p0", TACKLE, volatile=charging), mon("o0", SPLASH)))
    assert [event.side for event in only(lost.events, MoveUsed)] == ["opponent"]
    assert not only(lost.events, Damaged) and not only(lost.events, NoMovesLeft)
    assert not locked_in(lost.state, "player")


def test_the_ai_is_not_asked_while_the_opponent_is_locked_in() -> None:
    state = duel(mon("p0", SPLASH), mon("o0", SOLAR_BEAM, TACKLE))
    charging = play(state)
    rng = ScriptedRandom(floats=(HIT, NO_CRIT), ints=(100,))
    result = resolve_turn(charging.state, UseMove(0), rng)
    assert rng.exhausted
    assert only(result.events, MoveUsed)[1].move == "solar-beam"


def test_recharge_costs_the_next_turn() -> None:
    state = duel(mon("p0", HYPER_BEAM), mon("o0", SPLASH, max_hp=300))
    blast = play(state, floats=(HIT, NO_CRIT), ints=(100,))
    assert player_of(blast).volatile.recharging
    rest = play(blast.state)
    assert rest.events[1] == Immobilized("player", "p0", "P0", ImmobileReason.RECHARGING)
    assert not player_of(rest).volatile.recharging
    assert player_of(rest).moves[0].pp == HYPER_BEAM.pp - 1


def test_protect_blocks_and_chains_with_halving_odds() -> None:
    state = duel(mon("p0", PROTECT), mon("o0", speed=200))
    guarded = play(state)
    assert guarded.events[1:] == (
        MoveUsed("player", "p0", "P0", "protect", "Protección", "normal", STATUS),
        ProtectionRaised("player", "p0", "P0"),
        MoveUsed("opponent", "o0", "O0", "tackle", "Placaje", "normal", PHYSICAL),
        Protected("player", "p0", "P0"),
    )
    assert player_of(guarded).volatile == Volatile(protect_streak=1, last_move="protect")
    chained = play(guarded.state, floats=(0.6, HIT, NO_CRIT), ints=(100,))
    assert MoveFailed("player", "p0", "P0", FailReason.GENERIC) in chained.events
    assert player_of(chained).volatile.protect_streak == 0
    lucky = play(guarded.state, floats=(0.4,))
    assert ProtectionRaised("player", "p0", "P0") in lucky.events
    assert player_of(lucky).volatile.protect_streak == 2


def test_protect_fails_when_nobody_is_left_to_move() -> None:
    state = duel(mon("p0", PROTECT, speed=10), mon("o0", PROTECT, speed=99))
    result = play(state)
    assert result.events[-1] == MoveFailed("player", "p0", "P0", FailReason.GENERIC)


# --- Efectos únicos ---------------------------------------------------------


def test_rest_heals_everything_and_sleeps_two_turns() -> None:
    state = duel(mon("p0", REST, TACKLE, hp=10, status=Status.POISON), mon("o0", SPLASH))
    rested = play(state)
    assert rested.events[2:4] == (
        Healed("player", "p0", "P0", 90, 10, 100, 100, HealCause.REST),
        StatusInflicted("player", "p0", "P0", Status.SLEEP),
    )
    assert not only(rested.events, Damaged)
    one = play(rested.state)
    two = play(one.state)
    assert [one.events[1], two.events[1]] == [
        Immobilized("player", "p0", "P0", ImmobileReason.ASLEEP),
        Immobilized("player", "p0", "P0", ImmobileReason.ASLEEP),
    ]
    up = play(two.state, player=UseMove(1), floats=(HIT, NO_CRIT), ints=(100,))
    assert kinds(up.events)[1:3] == ["StatusCured", "MoveUsed"]
    full = play(duel(mon("p0", REST), mon("o0", SPLASH)))
    assert MoveFailed("player", "p0", "P0", FailReason.GENERIC) in full.events


def test_screens_halve_damage_for_five_turns_unless_critical() -> None:
    state = duel(mon("p0", REFLECT, SPLASH, max_hp=300), mon("o0", TACKLE, speed=10))
    walled = play(state, floats=(HIT, NO_CRIT), ints=(100,))
    assert ScreenRaised("player", "reflect") in walled.events
    hit = only(walled.events, Damaged)[0]
    assert hit.amount == damage_of(mon("o0"), mon("p0"), TACKLE, screen=True)
    assert walled.state.player.reflect_turns == 4
    again = play(walled.state, floats=(HIT, CRIT), ints=(100,))
    assert MoveFailed("player", "p0", "P0", FailReason.GENERIC) in again.events
    crit = only(again.events, Damaged)[0]
    assert crit.amount == damage_of(mon("o0"), mon("p0", hp=crit.hp_before), TACKLE, critical=True)
    state = again.state
    for _ in range(2):
        state = play(state, player=UseMove(1), floats=(HIT, NO_CRIT), ints=(85,)).state
    last = play(state, player=UseMove(1), floats=(HIT, NO_CRIT), ints=(85,))
    assert last.events[-1] == ScreenFaded("player", "reflect")
    assert last.state.player.reflect_turns == 0


def test_light_screen_only_covers_special_moves() -> None:
    state = duel(mon("p0", LIGHT_SCREEN), mon("o0", WATER_GUN, speed=10))
    result = play(state, floats=(HIT, NO_CRIT), ints=(100,))
    hit = only(result.events, Damaged)[0]
    assert hit.amount == damage_of(mon("o0"), mon("p0"), WATER_GUN, screen=True)


def test_haze_focus_energy_and_splash() -> None:
    boosted = duel(
        mon("p0", HAZE, stages=Stages(atk=3)), mon("o0", FOCUS_ENERGY, stages=Stages(spe=-2))
    )
    result = play(boosted)
    assert StatsReset() in result.events
    assert player_of(result).stages == Stages() and opponent_of(result).stages == Stages()
    assert PumpedUp("opponent", "o0", "O0") in result.events
    focused = duel(mon("p0", SLASH, volatile=Volatile(focus_energy=True)), mon("o0", SPLASH))
    # Cuchillada (+1) y Foco Energía (+2): escalón 3, crítico con 1/3.
    crit = play(focused, floats=(HIT, 0.3), ints=(100,))
    assert only(crit.events, Damaged)[0].critical
    again = play(result.state, opponent=UseMove(0))
    assert MoveFailed("opponent", "o0", "O0", FailReason.GENERIC) in again.events


def test_teleport_escapes_wild_battles_and_fails_against_trainers() -> None:
    mine = play(duel(mon("p0", TELEPORT), mon("o0", SPLASH)))
    assert mine.events[-2:] == (Teleported("player", "p0", "P0"), BattleEnded(Outcome.ESCAPED))
    theirs = play(duel(mon("p0", SPLASH), mon("o0", TELEPORT)))
    assert theirs.events[-2:] == (
        Teleported("opponent", "o0", "O0"),
        BattleEnded(Outcome.OPPONENT_FLED),
    )
    trainer = play(duel(mon("p0", TELEPORT), mon("o0", SPLASH), kind=BattleKind.TRAINER))
    assert MoveFailed("player", "p0", "P0", FailReason.GENERIC) in trainer.events


def test_roar_ends_wild_battles_and_shuffles_trainer_teams() -> None:
    wild = play(duel(mon("p0", ROAR), mon("o0", SPLASH)))
    assert wild.events[-2:] == (
        ForcedOut("opponent", "o0", "O0"),
        BattleEnded(Outcome.OPPONENT_FLED),
    )
    blown = play(duel(mon("p0", SPLASH), mon("o0", ROAR)))
    assert blown.events[-2:] == (ForcedOut("player", "p0", "P0"), BattleEnded(Outcome.ESCAPED))
    team = duel(
        mon("p0", ROAR),
        mon("o0", SPLASH, stages=Stages(atk=2)),
        foes=(mon("o1"), mon("o2")),
        kind=BattleKind.TRAINER,
    )
    shuffled = play(team, ints=(1,))
    assert kinds(shuffled.events)[-3:] == ["MoveUsed", "ForcedOut", "SentOut"]
    assert only(shuffled.events, SentOut)[-1].key == "o2"
    assert opponent_of(shuffled, 0).stages == Stages()
    assert dict(shuffled.state.participants)["o2"] == ("p0",)
    alone = play(duel(mon("p0", ROAR), mon("o0", SPLASH), kind=BattleKind.TRAINER))
    assert alone.events[-1] == MoveFailed("player", "p0", "P0", FailReason.GENERIC)


def test_a_pokemon_dragged_in_mid_turn_does_not_act() -> None:
    # Los dos usan Rugido (prioridad −6): el más rápido echa al otro, y quien
    # entra no hereda la acción elegida para el que se fue (ni su hueco).
    team = duel(
        mon("p0", ROAR), mon("o0", ROAR), foes=(mon("o1", SPLASH),), kind=BattleKind.TRAINER
    )
    result = play(team)
    assert kinds(result.events) == ["TurnStarted", "MoveUsed", "ForcedOut", "SentOut"]
    assert opponent_of(result).key == "o1"


def test_dragon_tail_hits_and_then_forces_a_switch() -> None:
    team = duel(
        mon("p0", DRAGON_TAIL), mon("o0", SPLASH), foes=(mon("o1"),), kind=BattleKind.TRAINER
    )
    result = play(team, floats=(HIT, NO_CRIT), ints=(100,))
    assert kinds(result.events)[-3:] == ["Damaged", "ForcedOut", "SentOut"]


# --- Daño especial ----------------------------------------------------------


def test_multi_hit_moves_roll_their_hits_and_stop_at_a_faint() -> None:
    state = duel(mon("p0", FURY_ATTACK), mon("o0", SPLASH, max_hp=300))
    five = play(state, floats=(HIT,) + (NO_CRIT,) * 5, ints=(7,) + (100,) * 5)
    assert kinds(five.events).count("Damaged") == 5
    assert only(five.events, HitCount) == [HitCount("opponent", "o0", "O0", 5)]
    # Cada golpe quita 12: el segundo lo debilita y ya no hay tercero.
    frail = duel(mon("p0", FURY_ATTACK), mon("o0", SPLASH, hp=20))
    short = play(frail, floats=(HIT, NO_CRIT, NO_CRIT), ints=(7, 100, 100))
    assert only(short.events, HitCount) == [HitCount("opponent", "o0", "O0", 2)]
    assert short.state.outcome is Outcome.WON
    kicks = play(
        duel(mon("p0", DOUBLE_KICK), mon("o0", SPLASH)),
        floats=(HIT, NO_CRIT, NO_CRIT),
        ints=(100, 100),
    )
    assert only(kicks.events, HitCount) == [HitCount("opponent", "o0", "O0", 2)]


def test_drain_heals_half_and_recoil_costs_a_third() -> None:
    drainer = mon("p0", GIGA_DRAIN, hp=50)
    result = play(duel(drainer, mon("o0", SPLASH, max_hp=300)), floats=(HIT, NO_CRIT), ints=(100,))
    dealt = damage_of(drainer, mon("o0", max_hp=300), GIGA_DRAIN)
    assert (
        Healed("player", "p0", "P0", dealt // 2, 50, 50 + dealt // 2, 100, HealCause.DRAIN)
        in result.events
    )
    brute = mon("p0", DOUBLE_EDGE)
    smash = play(duel(brute, mon("o0", SPLASH, max_hp=300)), floats=(HIT, NO_CRIT), ints=(100,))
    hit = damage_of(brute, mon("o0", max_hp=300), DOUBLE_EDGE)
    recoil = only(smash.events, Damaged)[-1]
    assert (recoil.cause, recoil.amount) == (DamageCause.RECOIL, hit * 33 // 100)


@pytest.mark.parametrize(
    ("move", "expected"),
    [(DRAGON_RAGE, 40), (SONIC_BOOM, 20), (SEISMIC_TOSS, 50), (NIGHT_SHADE, 50), (SUPER_FANG, 45)],
)
def test_fixed_damage_ignores_stats_and_resistances(move: Move, expected: int) -> None:
    state = duel(mon("p0", move), mon("o0", SPLASH, hp=90, types=("steel",)))
    result = play(state, floats=(HIT,))
    (hit,) = only(result.events, Damaged)
    assert (hit.amount, hit.effectiveness, hit.critical) == (expected, 1.0, False)


def test_one_hit_knockouts_respect_level_and_immunity() -> None:
    state = duel(mon("p0", FISSURE, level=60), mon("o0", SPLASH, level=50, hp=77))
    ko = play(state, floats=(0.39,))
    assert kinds(ko.events)[2:5] == ["Damaged", "OneHitKO", "Fainted"]
    assert only(ko.events, Damaged)[0].amount == 77
    stronger = duel(mon("p0", FISSURE, level=40), mon("o0", SPLASH, level=50))
    assert MoveFailed("player", "p0", "P0", FailReason.LEVEL_GAP) in play(stronger).events
    flier = duel(mon("p0", FISSURE, level=60), mon("o0", SPLASH, types=("flying",)))
    assert NoEffect("opponent", "o0", "O0") in play(flier).events


def test_false_swipe_leaves_one_hp() -> None:
    state = duel(mon("p0", FALSE_SWIPE_MOVE), mon("o0", SPLASH, hp=3))
    result = play(state, floats=(HIT, NO_CRIT), ints=(100,))
    assert opponent_of(result).hp == 1
    assert not result.state.finished


def test_dream_eater_needs_a_sleeping_target() -> None:
    awake = play(duel(mon("p0", DREAM_EATER), mon("o0", SPLASH)))
    assert MoveFailed("player", "p0", "P0", FailReason.TARGET_AWAKE) in awake.events
    sleeper = mon("o0", SPLASH, status=Status.SLEEP, status_turns=2, max_hp=300)
    eaten = play(duel(mon("p0", DREAM_EATER, hp=40), sleeper), floats=(HIT, NO_CRIT), ints=(100,))
    assert only(eaten.events, Healed)


# --- Cambios de características y efectos secundarios -----------------------


def test_stat_moves_target_by_category_and_report_limits() -> None:
    state = duel(mon("p0", SWORDS_DANCE, GROWL), mon("o0", SPLASH))
    dance = play(state)
    assert StatChanged("player", "p0", "P0", "atk", 2) in dance.events
    growl = play(state, player=UseMove(1), floats=(HIT,))
    assert StatChanged("opponent", "o0", "O0", "atk", -1) in growl.events
    capped = duel(mon("p0", SWORDS_DANCE, stages=Stages(atk=6)), mon("o0", SPLASH))
    assert StatUnchanged("player", "p0", "P0", "atk", True) in play(capped).events


def test_secondary_stat_changes_roll_their_chance() -> None:
    state = duel(mon("p0", ROCK_SMASH, METAL_CLAW), mon("o0", SPLASH, max_hp=300))
    lowered = play(state, floats=(HIT, NO_CRIT, 0.3), ints=(100,))
    assert StatChanged("opponent", "o0", "O0", "def", -1) in lowered.events
    missed = play(state, floats=(HIT, NO_CRIT, 0.7), ints=(100,))
    assert not only(missed.events, StatChanged)
    raised = play(state, player=UseMove(1), floats=(HIT, NO_CRIT, 0.05), ints=(100,))
    assert StatChanged("player", "p0", "P0", "atk", 1) in raised.events
    floored = duel(mon("p0", ROCK_SMASH), mon("o0", SPLASH, max_hp=300, stages=Stages(defense=-6)))
    silent = play(floored, floats=(HIT, NO_CRIT, 0.3), ints=(100,))
    assert not only(silent.events, StatUnchanged)


def test_swagger_raises_attack_and_confuses() -> None:
    # El rival, ya confuso, se mueve después: 0,9 y no se hiere.
    result = play(duel(mon("p0", SWAGGER), mon("o0", SPLASH)), floats=(HIT, 0.9), ints=(3,))
    assert result.events[2:4] == (
        StatChanged("opponent", "o0", "O0", "atk", 2),
        Confused("opponent", "o0", "O0"),
    )


def test_leech_seed_drains_an_eighth_every_turn() -> None:
    state = duel(mon("p0", LEECH_SEED, hp=50), mon("o0", SPLASH, max_hp=160))
    seeded = play(state, floats=(HIT,))
    assert Seeded("opponent", "o0", "O0") in seeded.events
    assert seeded.events[-2:] == (
        Damaged("opponent", "o0", "O0", 20, 160, 140, 160, DamageCause.LEECH_SEED),
        Healed("player", "p0", "P0", 20, 50, 70, 100, HealCause.LEECH_SEED),
    )


def test_wrap_traps_damages_a_sixteenth_and_releases() -> None:
    state = duel(mon("p0", WRAP, SPLASH), mon("o0", SPLASH, max_hp=160))
    wrapped = play(state, floats=(HIT, NO_CRIT), ints=(100, 2))
    assert Trapped("opponent", "o0", "O0", "Constricción") in wrapped.events
    tick = only(wrapped.events, Damaged)[-1]
    assert wrapped.events[-1] == tick
    assert (tick.amount, tick.cause, tick.hp_after) == (10, DamageCause.TRAP, tick.hp_before - 10)
    freed = play(wrapped.state, player=UseMove(1))
    assert freed.events[-1] == Released("opponent", "o0", "O0", "Constricción")
    assert opponent_of(freed).volatile.trapped_turns == 0


def test_heal_moves_heal_their_target() -> None:
    hurt = duel(mon("p0", RECOVER, HEAL_PULSE, AROMATIC_MIST, hp=40), mon("o0", SPLASH, hp=50))
    healed = play(hurt)
    assert Healed("player", "p0", "P0", 50, 40, 90, 100, HealCause.MOVE) in healed.events
    # Pulso Cura cura a su objetivo, que en individual es el rival.
    pulse = play(hurt, player=UseMove(1))
    assert Healed("opponent", "o0", "O0", 50, 50, 100, 100, HealCause.MOVE) in pulse.events
    mist = play(hurt, player=UseMove(2))
    assert MoveFailed("player", "p0", "P0", FailReason.GENERIC) in mist.events
    full = play(duel(mon("p0", RECOVER), mon("o0", SPLASH)))
    assert MoveFailed("player", "p0", "P0", FailReason.GENERIC) in full.events


@pytest.mark.parametrize("move", [TRANSFORM, LOW_KICK, MEAN_LOOK, TIDY_UP], ids=lambda m: m.slug)
def test_unsupported_moves_fail_without_breaking_the_turn(move: Move) -> None:
    result = play(duel(mon("p0", move), mon("o0", SPLASH)))
    assert result.events[1:3] == (
        MoveUsed("player", "p0", "P0", move.slug, move.name, move.type, move.damage_class),
        MoveFailed("player", "p0", "P0", FailReason.UNSUPPORTED),
    )
    assert player_of(result).moves[0].pp == move.pp - 1


def test_a_move_without_meta_deals_plain_damage() -> None:
    result = play(
        duel(mon("p0", HEADLONG_RUSH), mon("o0", SPLASH, max_hp=300)),
        floats=(HIT, NO_CRIT),
        ints=(100,),
    )
    assert only(result.events, Damaged)[0].amount == damage_of(
        mon("p0"), mon("o0", max_hp=300), HEADLONG_RUSH
    )


def test_evasion_boosts_make_moves_miss_more() -> None:
    state = duel(mon("p0", TACKLE), mon("o0", SPLASH, stages=Stages(evasion=6)))
    # 100 × 3/9 ≈ 33,3: 0,4 ya falla.
    result = play(state, floats=(0.4,))
    assert MoveMissed("player", "p0", "P0") in result.events
