"""Contrato de los movimientos: qué ejecuta el motor, cómo se guardan y reservas."""

import json
from dataclasses import fields, replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pokedex_cli.domain.battle.model import STAGE_KEYS, DamageClass, Move, StatChange
from pokedex_cli.domain.battle.moves import (
    CHARGE_MOVES,
    FALLBACK_BY_TYPE,
    FIXED_DAMAGE,
    SCREENS,
    SEMI_INVULNERABLE,
    STRUGGLE,
    SUPPORTED_AILMENTS,
    SUPPORTED_CATEGORIES,
    TACKLE,
    hit_range,
    is_fixed_damage,
    is_supported,
    move_from_record,
    move_to_record,
)
from pokedex_cli.domain.battle.typechart import TYPES

PHYSICAL = DamageClass.PHYSICAL
SPECIAL = DamageClass.SPECIAL
STATUS = DamageClass.STATUS

# Datos reales de PokeAPI (/move/{slug}) copiados a mano: los tests no leen red
# ni ficheros de ejemplo.
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
    min_turns=2,
    max_turns=4,
)
SWORDS_DANCE = Move(
    slug="swords-dance",
    name="Danza Espada",
    type="normal",
    damage_class=STATUS,
    power=None,
    accuracy=None,
    pp=20,
    target="user",
    category="net-good-stats",
    stat_changes=(StatChange("atk", 2),),
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
GIGA_DRAIN = Move(
    slug="giga-drain",
    name="Gigadrenado",
    type="grass",
    damage_class=SPECIAL,
    power=75,
    accuracy=100,
    pp=10,
    category="damage-heal",
    drain=50,
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
DRAGON_RAGE = Move(
    slug="dragon-rage",
    name="Furia Dragón",
    type="dragon",
    damage_class=SPECIAL,
    power=None,
    accuracy=100,
    pp=10,
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
SUPER_FANG = Move(
    slug="super-fang",
    name="Superdiente",
    type="normal",
    damage_class=PHYSICAL,
    power=None,
    accuracy=90,
    pp=10,
)
SOLAR_BEAM = Move(
    slug="solar-beam",
    name="Rayo Solar",
    type="grass",
    damage_class=SPECIAL,
    power=120,
    accuracy=100,
    pp=10,
)
HYPER_BEAM = Move(
    slug="hyper-beam",
    name="Hiperrayo",
    type="normal",
    damage_class=SPECIAL,
    power=150,
    accuracy=90,
    pp=5,
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
REST = Move(
    slug="rest",
    name="Descanso",
    type="psychic",
    damage_class=STATUS,
    power=None,
    accuracy=None,
    pp=5,
    target="user",
    category="unique",
)
REFLECT = Move(
    slug="reflect",
    name="Reflejo",
    type="psychic",
    damage_class=STATUS,
    power=None,
    accuracy=None,
    pp=20,
    target="users-field",
    category="field-effect",
)
HAZE = Move(
    slug="haze",
    name="Niebla",
    type="ice",
    damage_class=STATUS,
    power=None,
    accuracy=None,
    pp=30,
    target="entire-field",
    category="whole-field-effect",
)
ROAR = Move(
    slug="roar",
    name="Rugido",
    type="normal",
    damage_class=STATUS,
    power=None,
    accuracy=None,
    pp=20,
    priority=-6,
    category="force-switch",
)
RECOVER = Move(
    slug="recover",
    name="Recuperación",
    type="normal",
    damage_class=STATUS,
    power=None,
    accuracy=None,
    pp=5,
    target="user",
    category="heal",
    healing=50,
)
SUPPORTED = (
    FLAMETHROWER,
    HYPNOSIS,
    SWORDS_DANCE,
    DOUBLE_EDGE,
    GIGA_DRAIN,
    FURY_ATTACK,
    FISSURE,
    DRAGON_RAGE,
    SEISMIC_TOSS,
    SUPER_FANG,
    SOLAR_BEAM,
    HYPER_BEAM,
    PROTECT,
    REST,
    REFLECT,
    HAZE,
    ROAR,
    RECOVER,
    STRUGGLE,
    TACKLE,
)

# Efectos que el motor no implementa (o datos que no hacen nada).
COUNTER = Move(
    slug="counter",
    name="Contraataque",
    type="fighting",
    damage_class=PHYSICAL,
    power=None,
    accuracy=100,
    pp=20,
    priority=-5,
)
SPIKES = Move(
    slug="spikes",
    name="Púas",
    type="ground",
    damage_class=STATUS,
    power=None,
    accuracy=None,
    pp=20,
    target="opponents-field",
    category="field-effect",
)
TRANSFORM = Move(
    slug="transform",
    name="Transformación",
    type="normal",
    damage_class=STATUS,
    power=None,
    accuracy=None,
    pp=10,
    category="unique",
)
RAIN_DANCE = Move(
    slug="rain-dance",
    name="Danza Lluvia",
    type="water",
    damage_class=STATUS,
    power=None,
    accuracy=None,
    pp=5,
    target="entire-field",
    category="whole-field-effect",
)
ATTRACT = Move(
    slug="attract",
    name="Atracción",
    type="normal",
    damage_class=STATUS,
    power=None,
    accuracy=100,
    pp=15,
    category="ailment",
    ailment="infatuation",
)
# PokeAPI sin ``meta``: la caché lo guarda como categoría ``damage``.
STATUS_WITHOUT_META = Move(
    slug="tidy-up",
    name="Limpieza",
    type="normal",
    damage_class=STATUS,
    power=None,
    accuracy=None,
    pp=10,
    target="user",
)
HEALS_NOTHING = replace(RECOVER, slug="heal-nothing", healing=0)
RAISES_NOTHING = replace(SWORDS_DANCE, slug="raise-nothing", stat_changes=())
AILMENT_NONE = replace(HYPNOSIS, slug="ailment-none", ailment="none")
UNKNOWN_CATEGORY = replace(TACKLE, slug="mystery", category="mystery")
# Atrapar sin daño (Mal de Ojo) solo impide huir o cambiar: el motor no lo
# modela y no debe parecer soportado. Constricción, que sí daña, lo está.
TRAP_WITHOUT_DAMAGE = replace(HYPNOSIS, slug="mean-look", accuracy=None, ailment="trap")
UNSUPPORTED = (
    COUNTER,
    SPIKES,
    TRANSFORM,
    RAIN_DANCE,
    ATTRACT,
    STATUS_WITHOUT_META,
    HEALS_NOTHING,
    RAISES_NOTHING,
    AILMENT_NONE,
    UNKNOWN_CATEGORY,
    TRAP_WITHOUT_DAMAGE,
)


@pytest.mark.parametrize("move", SUPPORTED, ids=lambda move: move.slug)
def test_engine_supports_the_canonical_moves(move: Move) -> None:
    assert is_supported(move)


@pytest.mark.parametrize("move", UNSUPPORTED, ids=lambda move: move.slug)
def test_moves_without_an_implemented_effect_are_unsupported(move: Move) -> None:
    assert not is_supported(move)


def test_special_slugs_are_supported_whatever_their_category() -> None:
    """El motor ejecuta Protección, Descanso o Foco Energía por slug: la
    categoría ``meta`` de PokeAPI no puede dejarlos fuera."""
    focus = replace(STATUS_WITHOUT_META, slug="focus-energy", category="net-good-stats")
    assert is_supported(focus)
    assert is_supported(replace(PROTECT, category="mystery"))


def test_fixed_damage_moves_are_the_only_damaging_ones_allowed_without_power() -> None:
    assert [is_fixed_damage(move) for move in (DRAGON_RAGE, SEISMIC_TOSS, SUPER_FANG)] == [
        True,
        True,
        True,
    ]
    assert not is_fixed_damage(TACKLE)
    assert not is_fixed_damage(FISSURE)
    assert not is_supported(replace(TACKLE, power=None))
    assert not is_supported(replace(TACKLE, power=0))


def test_rule_tables_are_read_only_and_consistent() -> None:
    assert SEMI_INVULNERABLE <= CHARGE_MOVES
    assert dict(SCREENS) == {"reflect": "reflect", "light-screen": "light-screen"}
    assert {"unique", "field-effect", "whole-field-effect"}.isdisjoint(SUPPORTED_CATEGORIES)
    assert {"none", "unknown", "trap", "leech-seed"} <= SUPPORTED_AILMENTS
    with pytest.raises(TypeError):
        FIXED_DAMAGE["psywave"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        FALLBACK_BY_TYPE["shadow"] = TACKLE  # type: ignore[index]


@pytest.mark.parametrize(
    ("move", "expected"),
    [
        (TACKLE, (1, 1)),
        (FURY_ATTACK, (2, 5)),
        (replace(TACKLE, min_hits=2, max_hits=2), (2, 2)),
        (replace(TACKLE, min_hits=3, max_hits=None), (3, 3)),
        (replace(TACKLE, min_hits=0, max_hits=0), (1, 1)),
    ],
)
def test_hit_range_normalises_missing_and_degenerate_values(
    move: Move, expected: tuple[int, int]
) -> None:
    assert hit_range(move) == expected


# --- Registros de caché -----------------------------------------------------

MINIMAL = {"slug": "tackle", "type": "normal", "damage_class": "physical", "pp": 35}


def test_full_record_builds_the_exact_move() -> None:
    record = {
        "slug": "flamethrower",
        "name": "Lanzallamas",
        "type": "fire",
        "damage_class": "special",
        "power": 90,
        "accuracy": 100,
        "pp": 15,
        "priority": 0,
        "target": "selected-pokemon",
        "category": "damage-ailment",
        "ailment": "burn",
        "ailment_chance": 10,
        "min_hits": None,
        "max_hits": None,
        "min_turns": None,
        "max_turns": None,
        "drain": 0,
        "healing": 0,
        "crit_rate": 0,
        "flinch_chance": 0,
        "stat_changes": [],
        "stat_chance": 0,
        "description": "Ataca con una intensa llamarada.",
    }
    expected = replace(FLAMETHROWER, description="Ataca con una intensa llamarada.")
    assert move_from_record(record) == expected


def test_stat_changes_arrive_as_a_list_of_mappings() -> None:
    record = {**move_to_record(SWORDS_DANCE), "stat_changes": [{"stat": "atk", "change": 2}]}
    assert move_from_record(record) == SWORDS_DANCE


def test_minimal_record_takes_defaults_and_derives_the_name() -> None:
    record = {"slug": "dragon-rage", "type": "dragon", "damage_class": "special", "pp": 10}
    assert move_from_record(record) == Move(
        slug="dragon-rage",
        name="Dragon Rage",
        type="dragon",
        damage_class=SPECIAL,
        power=None,
        accuracy=None,
        pp=10,
    )


def test_null_optional_fields_and_unknown_keys_are_tolerated() -> None:
    record = {
        **MINIMAL,
        "name": None,
        "power": 40,
        "accuracy": 100,
        "priority": None,
        "category": None,
        "ailment": "",
        "drain": None,
        "stat_changes": None,
        "flavor": "ignorado",
    }
    assert move_from_record(record) == replace(TACKLE, name="Tackle")


@pytest.mark.parametrize("missing", ["slug", "type", "damage_class", "pp"])
def test_records_without_a_required_field_are_rejected(missing: str) -> None:
    record = {key: value for key, value in MINIMAL.items() if key != missing}
    assert move_from_record(record) is None


@pytest.mark.parametrize(
    "patch",
    [
        {"slug": None},
        {"slug": "  "},
        {"slug": 3},
        {"type": 7},
        {"damage_class": "magic"},
        {"damage_class": None},
        {"pp": None},
        {"pp": "35"},
        {"pp": True},
        {"pp": 0},
        {"name": 5},
        {"power": "40"},
        {"power": True},
        {"power": -1},
        {"accuracy": 0},
        {"accuracy": 101},
        {"accuracy": 99.5},
        {"priority": "1"},
        {"target": ["user"]},
        {"category": 1},
        {"ailment_chance": 101},
        {"flinch_chance": -1},
        {"min_hits": 5, "max_hits": 2},
        {"min_turns": 4, "max_turns": 1},
        {"drain": 150},
        {"healing": -101},
        {"crit_rate": -1},
        {"stat_changes": "atk+1"},
        {"stat_changes": ["atk"]},
        {"stat_changes": [{"stat": "attack", "change": 1}]},
        {"stat_changes": [{"stat": "atk", "change": "1"}]},
        {"stat_changes": [{"stat": "atk", "change": 0}]},
        {"stat_changes": [{"stat": "atk", "change": False}]},
        {"description": 3},
    ],
    ids=repr,
)
def test_invalid_types_or_impossible_values_are_rejected(patch: dict[str, object]) -> None:
    assert move_from_record({**MINIMAL, **patch}) is None


@pytest.mark.parametrize("raw", [None, "tackle", ["tackle"], 3])
def test_non_mapping_records_are_rejected(raw: object) -> None:
    assert move_from_record(raw) is None  # type: ignore[arg-type]


def test_record_keys_are_exactly_the_move_fields() -> None:
    assert list(move_to_record(TACKLE)) == [field.name for field in fields(Move)]


@pytest.mark.parametrize(
    "move", SUPPORTED + tuple(FALLBACK_BY_TYPE.values()), ids=lambda move: move.slug
)
def test_records_survive_a_json_round_trip(move: Move) -> None:
    assert move_from_record(json.loads(json.dumps(move_to_record(move)))) == move


@st.composite
def _ranges(draw: st.DrawFn) -> tuple[int | None, int | None]:
    if draw(st.booleans()):
        return None, None
    low = draw(st.integers(min_value=0, max_value=10))
    return low, draw(st.integers(min_value=low, max_value=15))


@st.composite
def valid_moves(draw: st.DrawFn) -> Move:
    min_hits, max_hits = draw(_ranges())
    min_turns, max_turns = draw(_ranges())
    percent = st.integers(min_value=0, max_value=100)
    return Move(
        slug=draw(st.from_regex(r"[a-z]{1,8}(-[a-z]{1,8}){0,2}", fullmatch=True)),
        name=draw(st.text(min_size=1, max_size=20).filter(str.strip)),
        type=draw(st.sampled_from(TYPES)),
        damage_class=draw(st.sampled_from(DamageClass)),
        power=draw(st.none() | st.integers(min_value=0, max_value=250)),
        accuracy=draw(st.none() | st.integers(min_value=1, max_value=100)),
        pp=draw(st.integers(min_value=1, max_value=64)),
        priority=draw(st.integers(min_value=-7, max_value=5)),
        target=draw(st.sampled_from(["selected-pokemon", "user", "all-opponents"])),
        category=draw(st.sampled_from(sorted(SUPPORTED_CATEGORIES | {"unique"}))),
        ailment=draw(st.sampled_from(sorted(SUPPORTED_AILMENTS))),
        ailment_chance=draw(percent),
        min_hits=min_hits,
        max_hits=max_hits,
        min_turns=min_turns,
        max_turns=max_turns,
        drain=draw(st.integers(min_value=-100, max_value=100)),
        healing=draw(st.integers(min_value=-100, max_value=100)),
        crit_rate=draw(st.integers(min_value=0, max_value=6)),
        flinch_chance=draw(percent),
        stat_changes=tuple(
            draw(
                st.lists(
                    st.builds(
                        StatChange,
                        stat=st.sampled_from(STAGE_KEYS),
                        change=st.integers(min_value=-3, max_value=3).filter(bool),
                    ),
                    max_size=3,
                )
            )
        ),
        stat_chance=draw(percent),
        description=draw(st.none() | st.text(max_size=40)),
    )


@given(valid_moves())
def test_any_valid_move_round_trips_through_json(move: Move) -> None:
    assert move_from_record(json.loads(json.dumps(move_to_record(move)))) == move


# --- Movimientos de reserva -------------------------------------------------


def test_struggle_and_tackle_use_the_current_canonical_data() -> None:
    assert STRUGGLE == Move(
        slug="struggle",
        name="Forcejeo",
        type="normal",
        damage_class=PHYSICAL,
        power=50,
        accuracy=None,
        pp=1,
        target="random-opponent",
        healing=-25,
    )
    assert TACKLE == Move(
        slug="tackle",
        name="Placaje",
        type="normal",
        damage_class=PHYSICAL,
        power=40,
        accuracy=100,
        pp=35,
    )


@pytest.mark.parametrize(
    ("type_name", "slug", "name", "damage_class", "power", "accuracy", "pp"),
    [
        ("fire", "ember", "Ascuas", SPECIAL, 40, 100, 25),
        ("water", "water-gun", "Pistola Agua", SPECIAL, 40, 100, 25),
        ("grass", "vine-whip", "Látigo Cepa", PHYSICAL, 45, 100, 25),
        ("electric", "thunder-shock", "Impactrueno", SPECIAL, 40, 100, 30),
        ("ice", "powder-snow", "Nieve Polvo", SPECIAL, 40, 100, 25),
        ("fighting", "rock-smash", "Golpe Roca", PHYSICAL, 40, 100, 15),
        ("poison", "poison-sting", "Picotazo Ven", PHYSICAL, 15, 100, 35),
        ("ground", "mud-slap", "Bofetón Lodo", SPECIAL, 20, 100, 10),
        ("flying", "gust", "Tornado", SPECIAL, 40, 100, 35),
        ("psychic", "confusion", "Confusión", SPECIAL, 50, 100, 25),
        ("bug", "bug-bite", "Picadura", PHYSICAL, 60, 100, 20),
        ("rock", "rock-throw", "Lanzarrocas", PHYSICAL, 50, 90, 15),
        ("ghost", "lick", "Lengüetazo", PHYSICAL, 30, 100, 30),
        ("dragon", "dragon-breath", "Dragoaliento", SPECIAL, 60, 100, 20),
        ("dark", "bite", "Mordisco", PHYSICAL, 60, 100, 25),
        ("steel", "metal-claw", "Garra Metal", PHYSICAL, 50, 95, 35),
        ("fairy", "fairy-wind", "Viento Feérico", SPECIAL, 40, 100, 30),
        ("normal", "tackle", "Placaje", PHYSICAL, 40, 100, 35),
    ],
)
def test_fallback_by_type_uses_canonical_spanish_data(
    type_name: str,
    slug: str,
    name: str,
    damage_class: DamageClass,
    power: int,
    accuracy: int,
    pp: int,
) -> None:
    move = FALLBACK_BY_TYPE[type_name]
    assert (move.slug, move.name, move.type) == (slug, name, type_name)
    assert (move.damage_class, move.power, move.accuracy, move.pp) == (
        damage_class,
        power,
        accuracy,
        pp,
    )
    assert is_supported(move)


def test_every_type_has_a_supported_damaging_fallback() -> None:
    assert set(FALLBACK_BY_TYPE) == set(TYPES)
    assert FALLBACK_BY_TYPE["normal"] is TACKLE
    assert all(move.is_damaging and is_supported(move) for move in FALLBACK_BY_TYPE.values())
