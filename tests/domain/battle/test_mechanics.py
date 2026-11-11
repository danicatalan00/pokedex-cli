"""Reglas numéricas del combate: daño de Gen 3, azar inyectado, orden y premios."""

from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pokedex_cli.domain.battle.mechanics import (
    STATUS_CATCH_BONUS,
    accuracy_stage_multiplier,
    battle_catch_multiplier,
    calculate_damage,
    can_receive_status,
    confusion_damage,
    crit_chance,
    effective_speed,
    effective_stat,
    escape_succeeds,
    experience_yield,
    fixed_damage,
    move_effectiveness,
    roll_critical,
    roll_hit_count,
    roll_hits,
    stage_multiplier,
    turn_order,
)
from pokedex_cli.domain.battle.model import (
    MAX_STAGE,
    MIN_STAGE,
    Action,
    BattleKind,
    BattleState,
    Combatant,
    DamageClass,
    Move,
    MoveSlot,
    Run,
    Side,
    SpendTurn,
    Stages,
    Stats,
    Status,
    SwitchTo,
    UseMove,
    Volatile,
)
from pokedex_cli.domain.battle.moves import STRUGGLE, TACKLE
from pokedex_cli.domain.battle.typechart import TYPES, effectiveness

PHYSICAL = DamageClass.PHYSICAL
SPECIAL = DamageClass.SPECIAL
STATUS = DamageClass.STATUS
EVEN = Stats(hp=100, atk=100, defense=100, spa=100, spd=100, spe=100)

FLAMETHROWER = Move(
    slug="flamethrower",
    name="Lanzallamas",
    type="fire",
    damage_class=SPECIAL,
    power=90,
    accuracy=100,
    pp=15,
    category="damage-ailment",
    ailment="burn",
    ailment_chance=10,
)
DOUBLE_EDGE = Move(
    slug="double-edge",
    name="Doble Filo",
    type="normal",
    damage_class=PHYSICAL,
    power=120,
    accuracy=100,
    pp=15,
    drain=-33,
)
EMBER = Move(
    slug="ember",
    name="Ascuas",
    type="fire",
    damage_class=SPECIAL,
    power=40,
    accuracy=100,
    pp=25,
)
SLASH = Move(
    slug="slash",
    name="Cuchillada",
    type="normal",
    damage_class=PHYSICAL,
    power=70,
    accuracy=100,
    pp=20,
    crit_rate=1,
)
HYPNOSIS = Move(
    slug="hypnosis",
    name="Hipnosis",
    type="psychic",
    damage_class=STATUS,
    power=None,
    accuracy=60,
    pp=20,
    category="ailment",
    ailment="sleep",
)
SWIFT = Move(
    slug="swift",
    name="Meteoros",
    type="normal",
    damage_class=SPECIAL,
    power=60,
    accuracy=None,
    pp=20,
)
FISSURE = Move(
    slug="fissure",
    name="Fisura",
    type="ground",
    damage_class=PHYSICAL,
    power=None,
    accuracy=30,
    pp=5,
    category="ohko",
)
FURY_ATTACK = Move(
    slug="fury-attack",
    name="Ataque Furia",
    type="normal",
    damage_class=PHYSICAL,
    power=15,
    accuracy=85,
    pp=20,
    min_hits=2,
    max_hits=5,
)
DOUBLE_KICK = Move(
    slug="double-kick",
    name="Doble Patada",
    type="fighting",
    damage_class=PHYSICAL,
    power=30,
    accuracy=100,
    pp=30,
    min_hits=2,
    max_hits=2,
)
DRAGON_RAGE = Move(
    slug="dragon-rage",
    name="Furia Dragón",
    type="dragon",
    damage_class=SPECIAL,
    power=None,
    accuracy=100,
    pp=10,
)
SONIC_BOOM = Move(
    slug="sonic-boom",
    name="Bomba Sónica",
    type="normal",
    damage_class=SPECIAL,
    power=None,
    accuracy=90,
    pp=20,
)
SEISMIC_TOSS = Move(
    slug="seismic-toss",
    name="Sísmico",
    type="fighting",
    damage_class=PHYSICAL,
    power=None,
    accuracy=100,
    pp=20,
)
NIGHT_SHADE = Move(
    slug="night-shade",
    name="Tinieblas",
    type="ghost",
    damage_class=SPECIAL,
    power=None,
    accuracy=100,
    pp=15,
)
SUPER_FANG = Move(
    slug="super-fang",
    name="Superdiente",
    type="normal",
    damage_class=PHYSICAL,
    power=None,
    accuracy=90,
    pp=10,
)
QUICK_ATTACK = Move(
    slug="quick-attack",
    name="Ataque Rápido",
    type="normal",
    damage_class=PHYSICAL,
    power=40,
    accuracy=100,
    pp=30,
    priority=1,
)
PROTECT = Move(
    slug="protect",
    name="Protección",
    type="normal",
    damage_class=STATUS,
    power=None,
    accuracy=None,
    pp=10,
    priority=4,
    target="user",
    category="unique",
    ailment="protect",
)


class ScriptedRandom:
    """Azar guionizado: entrega valores fijados y falla si se pide uno de más."""

    def __init__(self, *, floats: tuple[float, ...] = (), ints: tuple[int, ...] = ()) -> None:
        self.floats = list(floats)
        self.ints = list(ints)

    def random(self) -> float:
        return self.floats.pop(0)

    def randint(self, low: int, high: int) -> int:
        value = self.ints.pop(0)
        assert low <= value <= high
        return value

    @property
    def exhausted(self) -> bool:
        return not self.floats and not self.ints


def fighter(
    *,
    level: int = 50,
    types: tuple[str, ...] = ("normal",),
    stats: Stats = EVEN,
    hp: int | None = None,
    status: Status = Status.NONE,
    stages: Stages = Stages(),
    volatile: Volatile = Volatile(),
    moves: tuple[Move, ...] = (TACKLE,),
    key: str = "p0",
) -> Combatant:
    return Combatant(
        key=key,
        species="prueba",
        form="prueba",
        name="Prueba",
        level=level,
        types=types,
        base_stats=stats,
        ivs=Stats(hp=0, atk=0, defense=0, spa=0, spd=0, spe=0),
        nature="hardy",
        stats=stats,
        hp=stats.hp if hp is None else hp,
        moves=tuple(MoveSlot(move=move, pp=move.pp, max_pp=move.pp) for move in moves),
        status=status,
        stages=stages,
        volatile=volatile,
    )


def with_speed(speed: int, **extra: object) -> Combatant:
    stats = Stats(hp=100, atk=100, defense=100, spa=100, spd=100, spe=speed)
    return fighter(stats=stats, **extra)  # type: ignore[arg-type]


def battle(player: Combatant, opponent: Combatant) -> BattleState:
    return BattleState(
        kind=BattleKind.WILD,
        player=Side(team=(player,)),
        opponent=Side(team=(opponent,)),
    )


# --- Escalones --------------------------------------------------------------


def test_stage_multipliers_follow_the_gen3_tables() -> None:
    expected = [
        2 / 8,
        2 / 7,
        2 / 6,
        2 / 5,
        2 / 4,
        2 / 3,
        1,
        3 / 2,
        4 / 2,
        5 / 2,
        6 / 2,
        7 / 2,
        8 / 2,
    ]
    assert [stage_multiplier(stage) for stage in range(-6, 7)] == pytest.approx(expected)
    accuracy = [
        3 / 9,
        3 / 8,
        3 / 7,
        3 / 6,
        3 / 5,
        3 / 4,
        1,
        4 / 3,
        5 / 3,
        6 / 3,
        7 / 3,
        8 / 3,
        9 / 3,
    ]
    assert [accuracy_stage_multiplier(stage) for stage in range(-6, 7)] == pytest.approx(accuracy)
    assert stage_multiplier(9) == stage_multiplier(6)
    assert accuracy_stage_multiplier(-9) == accuracy_stage_multiplier(-6)


def test_critical_chance_by_stage() -> None:
    assert [crit_chance(stage) for stage in range(-1, 7)] == pytest.approx(
        [1 / 16, 1 / 16, 1 / 8, 1 / 4, 1 / 3, 1 / 2, 1 / 2, 1 / 2]
    )


def test_effective_stat_applies_stages_with_floor_and_optional_ignores() -> None:
    boosted = fighter(stages=Stages(atk=2, defense=-1, spa=-2, spe=6))
    assert effective_stat(boosted, "atk") == 200
    assert effective_stat(boosted, "def") == 66  # 100 × 2/3 = 66,6…
    assert effective_stat(boosted, "spa") == 50
    assert effective_stat(boosted, "spe") == 400
    assert effective_stat(boosted, "spa", ignore_drops=True) == 100
    assert effective_stat(boosted, "atk", ignore_drops=True) == 200
    assert effective_stat(boosted, "atk", ignore_boosts=True) == 100
    assert effective_stat(boosted, "def", ignore_boosts=True) == 66
    tiny = fighter(stats=Stats(hp=1, atk=1, defense=1, spa=1, spd=1, spe=1), stages=Stages(atk=-6))
    assert effective_stat(tiny, "atk") == 1


def test_effective_speed_includes_stage_and_paralysis() -> None:
    assert effective_speed(with_speed(100, stages=Stages(spe=1))) == 150
    paralyzed = with_speed(100, stages=Stages(spe=1), status=Status.PARALYSIS)
    assert effective_speed(paralyzed) == 37  # 150 × 0,25 = 37,5
    assert effective_speed(with_speed(3, status=Status.PARALYSIS)) == 1


@given(st.integers(min_value=-50, max_value=50))
def test_stage_multipliers_stay_bounded(stage: int) -> None:
    assert 0.25 <= stage_multiplier(stage) <= 4.0
    assert 1 / 3 <= accuracy_stage_multiplier(stage) <= 3.0


@given(st.integers(min_value=MIN_STAGE, max_value=MAX_STAGE))
def test_stage_multipliers_are_symmetric(stage: int) -> None:
    assert stage_multiplier(stage) * stage_multiplier(-stage) == pytest.approx(1.0)
    assert accuracy_stage_multiplier(stage) * accuracy_stage_multiplier(-stage) == pytest.approx(
        1.0
    )


@given(
    stat=st.integers(min_value=1, max_value=999),
    stage=st.integers(min_value=MIN_STAGE, max_value=MAX_STAGE),
)
def test_effective_stat_stays_between_a_quarter_and_four_times(stat: int, stage: int) -> None:
    stats = Stats(hp=stat, atk=stat, defense=stat, spa=stat, spd=stat, spe=stat)
    value = effective_stat(fighter(stats=stats, stages=Stages(atk=stage)), "atk")
    assert max(1, stat // 4) <= value <= stat * 4


# --- Fórmula de daño --------------------------------------------------------


def test_damage_example_stab_super_effective_under_light_screen() -> None:
    """Charizard-like (Fuego/Volador, Nv 50, AtEsp 120, quemado) usa Lanzallamas
    (Fuego, especial, 90) contra Planta/Veneno (DefEsp 100) con Pantalla Luz.

    nivel   = floor(2·50/5 + 2)              = 22
    base    = floor(floor(22·90·120/100)/50) = floor(2376/50) = 47
    quemado = no aplica: el ataque es especial  → 47
    pantalla (sin crítico) floor(47/2)       = 23
    +2                                       = 25
    crítico = no                             → 25
    azar    r=100: floor(25·100/100) = 25 · r=85: floor(25·85/100) = 21
    STAB    floor(25·1,5) = 37               · floor(21·1,5) = 31
    eficacia Fuego→Planta ×2, →Veneno ×1 = ×2: 74 · 62
    """
    attacker = fighter(
        types=("fire", "flying"),
        stats=Stats(hp=150, atk=80, defense=80, spa=120, spd=90, spe=100),
        status=Status.BURN,
    )
    defender = fighter(types=("grass", "poison"), key="o0")
    damage = calculate_damage(
        attacker, defender, FLAMETHROWER, critical=False, roll=100, screen=True
    )
    assert damage == 74
    low = calculate_damage(attacker, defender, FLAMETHROWER, critical=False, roll=85, screen=True)
    assert low == 62


def test_damage_example_burned_critical_ignores_screen_and_stages() -> None:
    """Normal quemado (Nv 30, Ataque 80, escalón −1) usa Doble Filo (Normal,
    físico, 120) contra Agua (Defensa 70, escalón +2) tras Reflejo, roll 90.

    Con crítico se ignoran el −1 propio, el +2 rival y el Reflejo:
    nivel   = floor(2·30/5 + 2)               = 14
    base    = floor(floor(14·120·80/70)/50)   = floor(1920/50) = 38
    quemado y físico floor(38/2)              = 19
    pantalla: ignorada por el crítico         → 19
    +2                                        = 21
    crítico ×2                                = 42
    azar    floor(42·90/100)                  = 37
    STAB    floor(37·1,5)                     = 55
    eficacia Normal→Agua ×1                   = 55

    Sin crítico: Ataque floor(80·2/3) = 53, Defensa 70·4/2 = 140:
    base floor(floor(14·120·53/140)/50) = floor(636/50) = 12 → quemado 6 →
    Reflejo 3 → +2 = 5 → azar floor(5·90/100) = 4 → STAB floor(4·1,5) = 6.
    """
    attacker = fighter(
        level=30,
        stats=Stats(hp=90, atk=80, defense=60, spa=50, spd=60, spe=40),
        status=Status.BURN,
        stages=Stages(atk=-1),
    )
    defender = fighter(
        level=30,
        types=("water",),
        stats=Stats(hp=90, atk=60, defense=70, spa=60, spd=60, spe=60),
        stages=Stages(defense=2),
        key="o0",
    )
    critical = calculate_damage(
        attacker, defender, DOUBLE_EDGE, critical=True, roll=90, screen=True
    )
    assert critical == 55
    regular = calculate_damage(
        attacker, defender, DOUBLE_EDGE, critical=False, roll=90, screen=True
    )
    assert regular == 6


def test_damage_example_minimum_one_and_type_immunity() -> None:
    """Normal (Nv 2, AtEsp 5) usa Ascuas (Fuego, especial, 40) contra Agua/Roca
    (DefEsp 200), roll 85:

    nivel   = floor(2·2/5 + 2)              = 2
    base    = floor(floor(2·40·5/200)/50)   = floor(2/50) = 0
    +2                                      = 2
    azar    floor(2·85/100)                 = 1
    STAB    no (el usuario es Normal)       → 1
    eficacia ×½ · ×½ = ×¼: floor(1·0,25)    = 0 → mínimo 1

    Inmunidad: Placaje contra un Fantasma es 0 aunque sea crítico.
    """
    weak = fighter(level=2, stats=Stats(hp=12, atk=5, defense=5, spa=5, spd=5, spe=5))
    wall = fighter(
        types=("water", "rock"),
        stats=Stats(hp=200, atk=50, defense=200, spa=50, spd=200, spe=50),
        key="o0",
    )
    assert calculate_damage(weak, wall, EMBER, critical=False, roll=85, screen=False) == 1
    ghost = fighter(types=("ghost", "poison"), key="o0")
    assert calculate_damage(fighter(), ghost, TACKLE, critical=True, roll=100, screen=False) == 0


@pytest.mark.parametrize("defender_types", [("normal",), ("ghost",), ("rock", "steel")])
def test_struggle_is_typeless_without_stab_or_type_chart(defender_types: tuple[str, ...]) -> None:
    """Forcejeo no tiene tipo desde Gen 2 (en Gen 3 ``typecalc`` lo salta): un
    Pokémon Normal no recibe STAB y golpea igual a un Fantasma o a Roca/Acero.

    Nv 50, Ataque y Defensa 100, potencia 50:
    floor(floor(22·50·100/100)/50) = 22 → +2 = 24 → azar 100 = 24, sin más factores.
    """
    target = fighter(types=defender_types, key="o0")
    damage = calculate_damage(fighter(), target, STRUGGLE, critical=False, roll=100, screen=False)
    assert damage == 24


def test_move_effectiveness_matches_the_damage_rule() -> None:
    # El motor la usa para «No afecta…»/«¡Es muy eficaz!»: Forcejeo siempre ×1.
    assert move_effectiveness(STRUGGLE, ("ghost",)) == 1.0
    assert move_effectiveness(TACKLE, ("ghost",)) == 0.0
    assert move_effectiveness(FLAMETHROWER, ("grass", "steel")) == 4.0


def test_power_override_replaces_the_move_power() -> None:
    attacker, defender = fighter(), fighter(key="o0")
    as_tackle = calculate_damage(attacker, defender, TACKLE, critical=False, roll=100, screen=False)
    overridden = calculate_damage(
        attacker, defender, SLASH, critical=False, roll=100, screen=False, power_override=40
    )
    assert overridden == as_tackle


@pytest.mark.parametrize(
    ("move", "roll", "power_override"),
    [(HYPNOSIS, 100, 40), (DRAGON_RAGE, 100, None), (TACKLE, 84, None), (TACKLE, 101, None)],
)
def test_damage_formula_rejects_impossible_inputs(
    move: Move, roll: int, power_override: int | None
) -> None:
    with pytest.raises(ValueError):
        calculate_damage(
            fighter(),
            fighter(key="o0"),
            move,
            critical=False,
            roll=roll,
            screen=False,
            power_override=power_override,
        )


def test_fixed_damage_ignores_stats_but_respects_type_immunity() -> None:
    attacker = fighter(level=37)
    assert fixed_damage(attacker, fighter(key="o0"), DRAGON_RAGE) == 40
    assert fixed_damage(attacker, fighter(types=("fairy",), key="o0"), DRAGON_RAGE) == 0
    assert fixed_damage(attacker, fighter(key="o0"), SONIC_BOOM) == 20
    assert fixed_damage(attacker, fighter(key="o0"), SEISMIC_TOSS) == 37
    assert fixed_damage(attacker, fighter(types=("ghost",), key="o0"), SEISMIC_TOSS) == 0
    assert fixed_damage(attacker, fighter(key="o0"), NIGHT_SHADE) == 0
    assert fixed_damage(attacker, fighter(hp=75, key="o0"), SUPER_FANG) == 37
    assert fixed_damage(attacker, fighter(hp=1, key="o0"), SUPER_FANG) == 1
    assert fixed_damage(attacker, fighter(types=("ghost",), key="o0"), SUPER_FANG) == 0
    assert fixed_damage(attacker, fighter(key="o0"), TACKLE) is None


def test_confusion_hit_is_typeless_physical_forty_without_stab() -> None:
    # floor(floor(22·40·100/100)/50) = 17 → +2 = 19; con quemadura 8 → +2 = 10.
    assert confusion_damage(fighter()) == 19
    assert confusion_damage(fighter(types=("ghost",))) == 19
    assert confusion_damage(fighter(status=Status.BURN)) == 10
    assert confusion_damage(fighter(), 85) == 16


stat_values = st.integers(min_value=1, max_value=999)
stage_values = st.integers(min_value=MIN_STAGE, max_value=MAX_STAGE)
combatants = st.builds(
    fighter,
    level=st.integers(min_value=1, max_value=100),
    types=st.lists(st.sampled_from(TYPES), min_size=1, max_size=2, unique=True).map(tuple),
    stats=st.builds(
        Stats,
        hp=st.integers(min_value=1, max_value=714),
        atk=stat_values,
        defense=stat_values,
        spa=stat_values,
        spd=stat_values,
        spe=stat_values,
    ),
    status=st.sampled_from(Status),
    stages=st.builds(
        Stages, atk=stage_values, defense=stage_values, spa=stage_values, spd=stage_values
    ),
)
damaging_moves = st.builds(
    Move,
    slug=st.just("probe"),
    name=st.just("Sonda"),
    type=st.sampled_from(TYPES),
    damage_class=st.sampled_from([PHYSICAL, SPECIAL]),
    power=st.integers(min_value=1, max_value=250),
    accuracy=st.just(100),
    pp=st.just(10),
)


@given(
    attacker=combatants,
    defender=combatants,
    move=damaging_moves,
    critical=st.booleans(),
    roll=st.integers(min_value=85, max_value=100),
    screen=st.booleans(),
)
def test_damage_is_at_least_one_unless_immune(
    attacker: Combatant,
    defender: Combatant,
    move: Move,
    critical: bool,
    roll: int,
    screen: bool,
) -> None:
    damage = calculate_damage(attacker, defender, move, critical=critical, roll=roll, screen=screen)
    if effectiveness(move.type, defender.types) == 0:
        assert damage == 0
    else:
        assert damage >= 1


@given(
    attacker=combatants,
    defender=combatants,
    move=damaging_moves,
    critical=st.booleans(),
    roll=st.integers(min_value=85, max_value=100),
    powers=st.tuples(
        st.integers(min_value=1, max_value=250), st.integers(min_value=1, max_value=250)
    ),
)
def test_damage_never_decreases_with_power(
    attacker: Combatant,
    defender: Combatant,
    move: Move,
    critical: bool,
    roll: int,
    powers: tuple[int, int],
) -> None:
    weaker, stronger = sorted(powers)

    def hit(power: int) -> int:
        return calculate_damage(
            attacker,
            defender,
            move,
            critical=critical,
            roll=roll,
            screen=False,
            power_override=power,
        )

    assert hit(weaker) <= hit(stronger)


# --- Tiradas de azar --------------------------------------------------------


@pytest.mark.parametrize(
    ("move", "focus", "value", "expected"),
    [
        (TACKLE, False, 0.0624, True),  # escalón 0: 1/16 = 0,0625
        (TACKLE, False, 0.0626, False),
        (TACKLE, True, 0.249, True),  # Foco Energía: +2 → 1/4
        (TACKLE, True, 0.251, False),
        (SLASH, True, 0.333, True),  # crit_rate 1 + Foco Energía 2 → 1/3
        (SLASH, True, 0.334, False),
    ],
)
def test_roll_critical_adds_move_and_focus_energy_stages(
    move: Move, focus: bool, value: float, expected: bool
) -> None:
    attacker = fighter(volatile=Volatile(focus_energy=focus))
    rng = ScriptedRandom(floats=(value,))
    assert roll_critical(attacker, move, rng) is expected
    assert rng.exhausted


def test_moves_without_accuracy_always_hit_without_spending_randomness() -> None:
    rng = ScriptedRandom()
    evasive = fighter(stages=Stages(evasion=6), key="o0")
    assert roll_hits(fighter(stages=Stages(accuracy=-6)), evasive, SWIFT, rng)


@pytest.mark.parametrize(
    ("accuracy_stage", "evasion_stage", "chance"),
    [
        (0, 0, 60),
        (1, 0, 80),  # 60 × 4/3
        (0, 1, 45),  # 60 × 3/4
        (2, 1, 80),  # neto +1
        (-6, 6, 20),  # neto −12 acotado a −6: 60 × 3/9
        (6, 0, 180),
    ],
)
def test_accuracy_uses_net_stage_against_evasion(
    accuracy_stage: int, evasion_stage: int, chance: int
) -> None:
    attacker = fighter(stages=Stages(accuracy=accuracy_stage))
    defender = fighter(stages=Stages(evasion=evasion_stage), key="o0")
    below = min(chance - 0.5, 99.9) / 100
    assert roll_hits(attacker, defender, HYPNOSIS, ScriptedRandom(floats=(below,)))
    if chance < 100:
        above = (chance + 0.5) / 100
        assert not roll_hits(attacker, defender, HYPNOSIS, ScriptedRandom(floats=(above,)))


def test_one_hit_ko_accuracy_depends_on_the_level_gap() -> None:
    strong = fighter(level=55, stages=Stages(accuracy=6))
    weak = fighter(level=50, key="o0")
    # 30 + (55 − 50) = 35 %, sin escalones de precisión.
    assert roll_hits(strong, weak, FISSURE, ScriptedRandom(floats=(0.349,)))
    assert not roll_hits(strong, weak, FISSURE, ScriptedRandom(floats=(0.351,)))
    rng = ScriptedRandom()
    assert not roll_hits(weak, strong, FISSURE, rng)  # nivel rival superior: falla sin tirar


def test_hit_count_follows_the_gen3_distribution() -> None:
    rng = ScriptedRandom(ints=tuple(range(8)))
    assert [roll_hit_count(FURY_ATTACK, rng) for _ in range(8)] == [2, 2, 2, 3, 3, 3, 4, 5]
    no_rolls = ScriptedRandom()
    assert roll_hit_count(DOUBLE_KICK, no_rolls) == 2
    assert roll_hit_count(TACKLE, no_rolls) == 1
    wide = Move(
        slug="population-bomb",
        name="Proliferación",
        type="normal",
        damage_class=PHYSICAL,
        power=20,
        accuracy=90,
        pp=10,
        min_hits=1,
        max_hits=10,
    )
    assert roll_hit_count(wide, ScriptedRandom(ints=(7,))) == 7


# --- Orden del turno --------------------------------------------------------


def turn(
    player: Combatant,
    opponent: Combatant,
    player_action: Action,
    opponent_action: Action,
    *floats: float,
) -> tuple[str, str]:
    rng = ScriptedRandom(floats=floats)
    order = turn_order(battle(player, opponent), player_action, opponent_action, rng)
    assert rng.exhausted
    return order


def test_faster_pokemon_moves_first() -> None:
    slow, fast = with_speed(50), with_speed(80, key="o0")
    assert turn(slow, fast, UseMove(0), UseMove(0)) == ("opponent", "player")
    assert turn(fast, slow, UseMove(0), UseMove(0)) == ("player", "opponent")


def test_priority_beats_speed() -> None:
    slow = with_speed(50, moves=(TACKLE, QUICK_ATTACK))
    fast = with_speed(80, key="o0")
    assert turn(slow, fast, UseMove(1), UseMove(0)) == ("player", "opponent")


@pytest.mark.parametrize("action", [SwitchTo(1), Run(), SpendTurn()])
def test_switching_running_and_items_go_before_any_move(action: Action) -> None:
    slow = with_speed(10)
    fast_protect = with_speed(200, moves=(PROTECT,), key="o0")
    assert turn(slow, fast_protect, action, UseMove(0)) == ("player", "opponent")


def test_opponent_switch_goes_before_a_priority_move() -> None:
    fast_protect = with_speed(200, moves=(PROTECT,))
    assert turn(fast_protect, with_speed(10, key="o0"), UseMove(0), SwitchTo(1)) == (
        "opponent",
        "player",
    )


def test_paralysis_quarters_speed_for_turn_order() -> None:
    paralyzed = with_speed(100, status=Status.PARALYSIS)  # 25
    assert turn(paralyzed, with_speed(30, key="o0"), UseMove(0), UseMove(0)) == (
        "opponent",
        "player",
    )


def test_speed_ties_are_decided_by_the_injected_randomness() -> None:
    left, right = with_speed(70), with_speed(70, key="o0")
    assert turn(left, right, UseMove(0), UseMove(0), 0.3) == ("player", "opponent")
    assert turn(left, right, UseMove(0), UseMove(0), 0.7) == ("opponent", "player")


def test_struggle_and_charging_moves_have_no_priority() -> None:
    exhausted = replace(with_speed(50), moves=(MoveSlot(move=QUICK_ATTACK, pp=0, max_pp=30),))
    fast = with_speed(80, key="o0")
    assert turn(exhausted, fast, UseMove(0), UseMove(0)) == ("opponent", "player")
    charging = with_speed(
        50, moves=(TACKLE, QUICK_ATTACK), volatile=Volatile(charging="solar-beam")
    )
    assert turn(charging, fast, UseMove(1), UseMove(0)) == ("opponent", "player")


# --- Captura, huida y experiencia -------------------------------------------


@pytest.mark.parametrize(
    ("hp", "status", "expected"),
    [
        (100, Status.NONE, 1.0),
        (1, Status.NONE, 2.98),  # (300 − 2)/100
        (0, Status.NONE, 3.0),
        (100, Status.SLEEP, 2.0),
        (100, Status.FREEZE, 2.0),
        (100, Status.PARALYSIS, 1.5),
        (100, Status.BURN, 1.5),
        (100, Status.POISON, 1.5),
        (100, Status.TOXIC, 1.5),
        (50, Status.BURN, 3.0),  # (300 − 100)/100 × 1,5
    ],
)
def test_battle_catch_multiplier_rewards_low_hp_and_status(
    hp: int, status: Status, expected: float
) -> None:
    assert battle_catch_multiplier(fighter(hp=hp, status=status)) == pytest.approx(expected)


@given(max_hp=st.integers(min_value=1, max_value=714), data=st.data())
def test_catch_multiplier_stays_within_one_to_three_times_status(
    max_hp: int, data: st.DataObject
) -> None:
    hp = data.draw(st.integers(min_value=0, max_value=max_hp))
    status = data.draw(st.sampled_from(Status))
    stats = Stats(hp=max_hp, atk=50, defense=50, spa=50, spd=50, spe=50)
    multiplier = battle_catch_multiplier(fighter(stats=stats, hp=hp, status=status))
    bonus = STATUS_CATCH_BONUS[status]
    assert bonus * (1 - 1e-9) <= multiplier <= 3 * bonus * (1 + 1e-9)


def test_faster_pokemon_always_escape_without_rolling() -> None:
    rng = ScriptedRandom()
    assert escape_succeeds(80, 80, 0, rng)
    assert escape_succeeds(120, 80, 3, rng)


@pytest.mark.parametrize(
    ("player", "wild", "attempts", "odds"),
    [
        (50, 100, 0, 94),  # 50·128//100 = 64, + 30·1
        (50, 100, 2, 154),  # 64 + 30·3
        (99, 100, 4, 20),  # (126 + 30·5) % 256 = 276 % 256
    ],
)
def test_escape_odds_follow_the_gen3_formula(
    player: int, wild: int, attempts: int, odds: int
) -> None:
    assert escape_succeeds(player, wild, attempts, ScriptedRandom(ints=(odds - 1,)))
    assert not escape_succeeds(player, wild, attempts, ScriptedRandom(ints=(odds,)))


@pytest.mark.parametrize(
    ("participants", "trainer", "expected"),
    [
        (1, False, 91),  # floor(64·10/7) = 91
        (2, False, 45),
        (1, True, 136),  # floor(91 × 1,5)
        (3, True, 45),  # floor(91/3) = 30 → floor(30 × 1,5)
    ],
)
def test_experience_yield_splits_and_rewards_trainer_battles(
    participants: int, trainer: bool, expected: int
) -> None:
    assert experience_yield(64, 10, participants, trainer=trainer) == expected


def test_experience_yield_never_drops_to_zero() -> None:
    assert experience_yield(1, 1, 6, trainer=False) == 1


# --- Estados ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("types", "status", "expected"),
    [
        (("fire",), Status.BURN, False),
        (("water",), Status.BURN, True),
        (("electric",), Status.PARALYSIS, False),
        (("poison",), Status.POISON, False),
        (("steel", "flying"), Status.TOXIC, False),
        (("grass",), Status.TOXIC, True),
        (("ice",), Status.FREEZE, False),
        (("ice",), Status.SLEEP, True),
        (("normal",), Status.NONE, False),
    ],
)
def test_status_type_immunities(types: tuple[str, ...], status: Status, expected: bool) -> None:
    assert can_receive_status(fighter(types=types), status) is expected


def test_status_is_one_at_a_time_and_never_on_fainted_pokemon() -> None:
    assert not can_receive_status(fighter(status=Status.BURN), Status.PARALYSIS)
    assert not can_receive_status(fighter(hp=0), Status.SLEEP)
