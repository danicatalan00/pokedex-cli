"""Movesets deterministas: salvajes auténticos, capturas con criterio y reservas."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pokedex_cli.domain.battle.model import MAX_MOVES, DamageClass, Move, MoveSlot, StatChange
from pokedex_cli.domain.battle.moves import FALLBACK_BY_TYPE, TACKLE, is_supported
from pokedex_cli.domain.battle.movesets import (
    fallback_moveset,
    learnable,
    slots_for,
    smart_moveset,
    wild_moveset,
)

PHYSICAL = DamageClass.PHYSICAL
SPECIAL = DamageClass.SPECIAL
STATUS = DamageClass.STATUS


def attack(
    slug: str,
    type_name: str,
    power: int | None,
    accuracy: int | None = 100,
    *,
    damage_class: DamageClass = PHYSICAL,
    **extra: object,
) -> Move:
    return Move(
        slug=slug,
        name=slug.replace("-", " ").title(),
        type=type_name,
        damage_class=damage_class,
        power=power,
        accuracy=accuracy,
        pp=20,
        **extra,  # type: ignore[arg-type]
    )


def status(
    slug: str, type_name: str = "normal", accuracy: int | None = 100, **extra: object
) -> Move:
    return Move(
        slug=slug,
        name=slug.replace("-", " ").title(),
        type=type_name,
        damage_class=STATUS,
        power=None,
        accuracy=accuracy,
        pp=20,
        **extra,  # type: ignore[arg-type]
    )


EMBER = FALLBACK_BY_TYPE["fire"]
WATER_GUN = FALLBACK_BY_TYPE["water"]
VINE_WHIP = FALLBACK_BY_TYPE["grass"]
POWDER_SNOW = FALLBACK_BY_TYPE["ice"]
GUST = FALLBACK_BY_TYPE["flying"]
CONFUSION = FALLBACK_BY_TYPE["psychic"]
POISON_STING = FALLBACK_BY_TYPE["poison"]

SURF = attack("surf", "water", 90, damage_class=SPECIAL)
BUBBLE_BEAM = attack("bubble-beam", "water", 65, damage_class=SPECIAL)
ICE_BEAM = attack("ice-beam", "ice", 90, damage_class=SPECIAL)
BITE = FALLBACK_BY_TYPE["dark"]
QUICK_ATTACK = attack("quick-attack", "normal", 40, priority=1)
WING_ATTACK = attack("wing-attack", "flying", 60)
PECK = attack("peck", "flying", 35)
FURY_ATTACK = attack("fury-attack", "normal", 15, 85, min_hits=2, max_hits=5)
DOUBLE_KICK = attack("double-kick", "fighting", 30, min_hits=2, max_hits=2)
SEISMIC_TOSS = attack("seismic-toss", "fighting", None)
FISSURE = attack("fissure", "ground", None, 30, category="ohko")
HYPER_BEAM = attack("hyper-beam", "normal", 150, 90, damage_class=SPECIAL)
BODY_SLAM = attack("body-slam", "normal", 85)
COUNTER = attack("counter", "fighting", None, priority=-5)
LICK = FALLBACK_BY_TYPE["ghost"]
SHADOW_PUNCH = attack("shadow-punch", "ghost", 60, None)
NIGHT_SHADE = attack("night-shade", "ghost", None, damage_class=SPECIAL)
DREAM_EATER = attack(
    "dream-eater", "psychic", 100, damage_class=SPECIAL, category="damage-heal", drain=50
)

HYPNOSIS = status("hypnosis", "psychic", 60, category="ailment", ailment="sleep")
THUNDER_WAVE = status("thunder-wave", "electric", 90, category="ailment", ailment="paralysis")
TOXIC = status("toxic", "poison", 90, category="ailment", ailment="poison")
CONFUSE_RAY = status("confuse-ray", "ghost", 100, category="ailment", ailment="confusion")
SWORDS_DANCE = status(
    "swords-dance",
    accuracy=None,
    target="user",
    category="net-good-stats",
    stat_changes=(StatChange("atk", 2),),
)
RECOVER = status("recover", accuracy=None, target="user", category="heal", healing=50)
REST = status("rest", "psychic", None, target="user", category="unique")
GROWL = status(
    "growl",
    target="all-opponents",
    category="net-good-stats",
    stat_changes=(StatChange("atk", -1),),
)
SAND_ATTACK = status(
    "sand-attack", "ground", category="net-good-stats", stat_changes=(StatChange("accuracy", -1),)
)
FEATHER_DANCE = status(
    "feather-dance", "flying", category="net-good-stats", stat_changes=(StatChange("atk", -2),)
)
AGILITY = status(
    "agility",
    "psychic",
    None,
    target="user",
    category="net-good-stats",
    stat_changes=(StatChange("spe", 2),),
)
WHIRLWIND = status("whirlwind", accuracy=None, priority=-6, category="force-switch")
MIRROR_MOVE = status("mirror-move", "flying", None, category="unique")
TELEPORT = status("teleport", "psychic", None, target="user", category="unique")
SPLASH = status("splash", accuracy=None, target="user", category="unique")
TRANSFORM = status("transform", accuracy=None, category="unique")
POISON_POWDER = status("poison-powder", "poison", 75, category="ailment", ailment="poison")
STUN_SPORE = status("stun-spore", "grass", 75, category="ailment", ailment="paralysis")
SLEEP_POWDER = status("sleep-powder", "grass", 75, category="ailment", ailment="sleep")
SUPERSONIC = status("supersonic", accuracy=55, category="ailment", ailment="confusion")


def catalog(*moves: Move) -> dict[str, Move]:
    return {move.slug: move for move in moves}


# --- learnable --------------------------------------------------------------


def test_learnable_orders_by_level_then_original_order() -> None:
    entries = [("ember", 7), ("growl", 1), ("scratch", 1), ("leer", 0), ("flamethrower", 31)]
    # Nivel 0 (al evolucionar) cuenta como 1 y va tras los de nivel 1 anteriores.
    assert learnable(entries, 10) == ["growl", "scratch", "leer", "ember"]
    assert learnable(entries, 1) == ["growl", "scratch", "leer"]
    assert learnable(entries, 0) == []


def test_repeated_slug_keeps_its_last_appearance() -> None:
    entries = [("tackle", 1), ("growl", 1), ("tackle", 15)]
    assert learnable(entries, 10) == ["tackle", "growl"]
    assert learnable(entries, 20) == ["growl", "tackle"]


# --- wild_moveset -----------------------------------------------------------

PIDGEY_ENTRIES = [
    ("tackle", 1),
    ("sand-attack", 5),
    ("gust", 9),
    ("quick-attack", 13),
    ("whirlwind", 19),
    ("wing-attack", 25),
    ("feather-dance", 31),
    ("agility", 39),
    ("mirror-move", 47),
]
PIDGEY_CATALOG = catalog(
    TACKLE,
    SAND_ATTACK,
    GUST,
    QUICK_ATTACK,
    WHIRLWIND,
    WING_ATTACK,
    FEATHER_DANCE,
    AGILITY,
    MIRROR_MOVE,
)


def test_wild_pokemon_know_their_four_latest_moves() -> None:
    assert wild_moveset(PIDGEY_ENTRIES, 20, PIDGEY_CATALOG) == (
        SAND_ATTACK,
        GUST,
        QUICK_ATTACK,
        WHIRLWIND,
    )
    assert wild_moveset(PIDGEY_ENTRIES, 3, PIDGEY_CATALOG) == (TACKLE,)


def test_wild_moveset_skips_unsupported_and_uncached_moves() -> None:
    # Mismo Pokémon a nivel 50: Mov. Espejo no está soportado.
    assert wild_moveset(PIDGEY_ENTRIES, 50, PIDGEY_CATALOG) == (
        WHIRLWIND,
        WING_ATTACK,
        FEATHER_DANCE,
        AGILITY,
    )
    without_wing_attack = {
        slug: move for slug, move in PIDGEY_CATALOG.items() if slug != "wing-attack"
    }
    assert wild_moveset(PIDGEY_ENTRIES, 50, without_wing_attack) == (
        QUICK_ATTACK,
        WHIRLWIND,
        FEATHER_DANCE,
        AGILITY,
    )


def test_wild_moveset_without_damage_brings_back_the_latest_damaging_move() -> None:
    entries = [
        ("confusion", 10),
        ("poison-powder", 13),
        ("stun-spore", 14),
        ("sleep-powder", 15),
        ("supersonic", 18),
    ]
    moves = catalog(CONFUSION, POISON_POWDER, STUN_SPORE, SLEEP_POWDER, SUPERSONIC)
    # Los cuatro últimos son de estado: el más antiguo deja sitio a Confusión.
    assert wild_moveset(entries, 20, moves) == (CONFUSION, STUN_SPORE, SLEEP_POWDER, SUPERSONIC)


def test_wild_moveset_without_any_damaging_move_falls_back_to_tackle() -> None:
    assert wild_moveset([("teleport", 1)], 5, catalog(TELEPORT)) == (TACKLE, TELEPORT)
    entries = [("growl", 1), ("sand-attack", 4), ("hypnosis", 7), ("agility", 9)]
    moves = catalog(GROWL, SAND_ATTACK, HYPNOSIS, AGILITY)
    assert wild_moveset(entries, 10, moves) == (TACKLE, SAND_ATTACK, HYPNOSIS, AGILITY)


def test_wild_moveset_without_data_is_empty() -> None:
    assert wild_moveset([], 50, PIDGEY_CATALOG) == ()
    assert wild_moveset([("wing-attack", 25)], 10, PIDGEY_CATALOG) == ()
    assert wild_moveset(PIDGEY_ENTRIES, 50, {}) == ()


# --- smart_moveset ----------------------------------------------------------


def test_smart_moveset_balances_power_and_type_coverage() -> None:
    """Puntuación = potencia × precisión/100 × 1,5 si STAB; tipo repetido ×0,6.

    Usuario de tipo Agua. Dañinos:
      surf         90 × 1,0 × 1,5 = 135
      bubble-beam  65 × 1,0 × 1,5 = 97,5
      ice-beam     90 × 1,0       = 90
      bite         60 × 1,0       = 60
      water-gun    40 × 1,0 × 1,5 = 60
      tackle       40 × 1,0       = 40
    1.º surf (135). 2.º ice-beam (90) gana a bubble-beam (97,5 × 0,6 = 58,5).
    3.º bite (60) gana a bubble-beam (58,5) y a water-gun (60 × 0,6 = 36).
    Estado útil: thunder-wave (80 × 0,9 = 72) gana a hypnosis (100 × 0,6 = 60).
    """
    candidates = [
        TACKLE,
        WATER_GUN,
        BUBBLE_BEAM,
        SURF,
        ICE_BEAM,
        BITE,
        HYPNOSIS,
        THUNDER_WAVE,
        GROWL,
    ]
    assert smart_moveset(candidates, ["water"]) == (SURF, ICE_BEAM, BITE, THUNDER_WAVE)


def test_smart_moveset_takes_a_fourth_damaging_move_without_useful_status() -> None:
    # Placaje 40 × 1,5 = 60; Látigo Cepa 45; Ascuas y Pistola Agua 40 (empate por slug).
    candidates = [WATER_GUN, EMBER, VINE_WHIP, TACKLE, GROWL]
    assert smart_moveset(candidates, ["normal"]) == (TACKLE, VINE_WHIP, EMBER, WATER_GUN)


def test_fixed_damage_scores_sixty_without_stab_and_hits_multiply() -> None:
    # Doble Patada 30 × 2 golpes × 1,5 = 90; Ascuas 40; Sísmico 60 fijo (sin
    # STAB), ×0,6 por repetir Lucha = 36. Con STAB o sin contar golpes, el
    # orden sería otro.
    candidates = [SEISMIC_TOSS, EMBER, DOUBLE_KICK]
    assert smart_moveset(candidates, ["fighting"]) == (DOUBLE_KICK, EMBER, SEISMIC_TOSS)
    # Fisura: 60 × 0,3 = 18 > Picotazo Ven 15.
    assert smart_moveset([POISON_STING, FISSURE], ["normal"]) == (FISSURE, POISON_STING)


def test_multi_hit_counts_three_average_hits_and_priority_adds_ten_percent() -> None:
    # Ataque Furia 15 × 3 × 0,85 = 38,25 > Picotazo 35.
    assert smart_moveset([PECK, FURY_ATTACK], ["water"]) == (FURY_ATTACK, PECK)
    # Ataque Rápido 40 × 1,1 = 44 > Placaje 40.
    assert smart_moveset([TACKLE, QUICK_ATTACK], ["water"]) == (QUICK_ATTACK, TACKLE)


def test_two_turn_moves_score_half() -> None:
    # Hiperrayo 150 × 0,9 × 1,5 / 2 = 101,25 < Golpe Cuerpo 85 × 1,5 = 127,5.
    assert smart_moveset([HYPER_BEAM, BODY_SLAM], ["normal"]) == (BODY_SLAM, HYPER_BEAM)


def test_useful_status_moves_rank_by_value_and_accuracy() -> None:
    """Dormir 100, paralizar 80, tóxico 75, subida +2 propia 70, curación 65,
    confusión 60, Descanso 50; todo × precisión. Con un solo dañino quedan tres
    huecos para el estado y Gruñido (no útil) queda fuera."""
    candidates = [
        GROWL,
        REST,
        CONFUSE_RAY,
        HYPNOSIS,
        RECOVER,
        TOXIC,
        SWORDS_DANCE,
        THUNDER_WAVE,
        TACKLE,
    ]
    # thunder-wave 72 > swords-dance 70 > toxic 67,5 > recover 65 > …
    assert smart_moveset(candidates, ["normal"]) == (TACKLE, THUNDER_WAVE, SWORDS_DANCE, TOXIC)


def test_healing_the_foe_is_not_a_useful_status_move() -> None:
    """Pulso Cura cura a su objetivo, que en un combate individual es el rival:
    no cuenta como estado útil y deja el cuarto hueco a un dañino."""
    heal_pulse = status("heal-pulse", "psychic", None, category="heal", healing=50)
    damaging = [TACKLE, EMBER, WATER_GUN, VINE_WHIP]
    assert heal_pulse not in smart_moveset([*damaging, heal_pulse], ["normal"])
    assert RECOVER in smart_moveset([*damaging, RECOVER], ["normal"])


def test_smart_moveset_always_has_a_damaging_move() -> None:
    assert smart_moveset([GROWL, HYPNOSIS], ["psychic"]) == (TACKLE, HYPNOSIS, GROWL)
    assert smart_moveset([], ["psychic"]) == (TACKLE,)


def test_smart_moveset_excludes_unsupported_and_useless_moves() -> None:
    assert smart_moveset([COUNTER, TRANSFORM, SPLASH, EMBER], ["fire"]) == (EMBER,)


def test_dream_eater_only_joins_a_moveset_that_can_put_the_target_to_sleep() -> None:
    """Comesueños falla contra un rival despierto (TARGET_AWAKE).

    Gengar Nv 40 sin nada que duerma: Comesueños (100) iría primero y no
    acertaría nunca, así que se elige sin él: Puño Sombra 60 × 1,5 = 90,
    Tinieblas 60 × 0,6 = 36 y Lengüetazo 45 × 0,6² = 16,2, más Rayo Confuso.
    """
    no_sleep = [LICK, NIGHT_SHADE, SHADOW_PUNCH, DREAM_EATER, CONFUSE_RAY]
    assert smart_moveset(no_sleep, ["ghost", "poison"]) == (
        SHADOW_PUNCH,
        NIGHT_SHADE,
        LICK,
        CONFUSE_RAY,
    )
    # Con Hipnosis en el moveset el combo funciona y Comesueños se queda.
    with_sleep = [LICK, NIGHT_SHADE, SHADOW_PUNCH, DREAM_EATER, HYPNOSIS]
    assert smart_moveset(with_sleep, ["ghost", "poison"]) == (
        DREAM_EATER,
        SHADOW_PUNCH,
        NIGHT_SHADE,
        HYPNOSIS,
    )
    # Si es su único dañino y no puede dormir, el dañino de reserva es Placaje.
    assert smart_moveset([DREAM_EATER, CONFUSE_RAY], ["ghost"]) == (TACKLE, CONFUSE_RAY)


def test_smart_moveset_breaks_ties_by_slug() -> None:
    same = [attack(f"move-{letter}", "normal", 40) for letter in "edcba"]
    assert [move.slug for move in smart_moveset(same, ["fire"])] == [
        "move-a",
        "move-b",
        "move-c",
        "move-d",
    ]


POOL = [
    TACKLE,
    EMBER,
    WATER_GUN,
    SURF,
    ICE_BEAM,
    BITE,
    QUICK_ATTACK,
    FURY_ATTACK,
    SEISMIC_TOSS,
    COUNTER,
    DREAM_EATER,
    HYPNOSIS,
    THUNDER_WAVE,
    SWORDS_DANCE,
    RECOVER,
    GROWL,
]


@given(
    order=st.permutations(POOL),
    size=st.integers(min_value=0, max_value=len(POOL)),
    types=st.sampled_from([("water",), ("fire", "flying"), ("normal",), ()]),
)
def test_smart_moveset_is_deterministic_and_well_formed(
    order: list[Move], size: int, types: tuple[str, ...]
) -> None:
    candidates = order[:size]
    moves = smart_moveset(candidates, types)
    assert moves == smart_moveset(sorted(candidates, key=lambda move: move.slug), types)
    assert 1 <= len(moves) <= MAX_MOVES
    assert len({move.slug for move in moves}) == len(moves)
    assert any(move.is_damaging for move in moves)
    assert all(is_supported(move) for move in moves)
    assert set(moves) <= set(candidates) | {TACKLE}
    if DREAM_EATER in moves:
        assert HYPNOSIS in moves


# --- reservas y PP ----------------------------------------------------------


@pytest.mark.parametrize(
    ("types", "expected"),
    [
        (("fire", "flying"), (EMBER, GUST, TACKLE)),
        (("normal",), (TACKLE,)),
        (("normal", "flying"), (TACKLE, GUST)),
        (("water", "water"), (WATER_GUN, TACKLE)),
        ((), (TACKLE,)),
        (("shadow",), (TACKLE,)),
        (("fire", "water", "grass", "ice"), (EMBER, WATER_GUN, VINE_WHIP, POWDER_SNOW)),
    ],
)
def test_fallback_moveset_uses_one_move_per_type_plus_tackle(
    types: tuple[str, ...], expected: tuple[Move, ...]
) -> None:
    assert fallback_moveset(types) == expected


def test_slots_start_with_full_pp() -> None:
    assert slots_for([TACKLE, HYPNOSIS]) == (
        MoveSlot(move=TACKLE, pp=35, max_pp=35),
        MoveSlot(move=HYPNOSIS, pp=20, max_pp=20),
    )
    assert slots_for([]) == ()
