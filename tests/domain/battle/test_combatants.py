"""Combatientes: stats reales, subidas de nivel y moveset automático."""

from collections.abc import Sequence
from dataclasses import replace

from pokedex_cli.domain.battle.combatants import (
    build_combatant,
    build_stats,
    level_up,
    refresh_auto_moveset,
)
from pokedex_cli.domain.battle.model import (
    Combatant,
    DamageClass,
    LearnsetEntry,
    Move,
    MoveSlot,
    Stages,
    StatChange,
    Stats,
    Status,
    Volatile,
)
from pokedex_cli.domain.battle.moves import FALLBACK_BY_TYPE, TACKLE

BULBASAUR_BASE = Stats(hp=45, atk=49, defense=49, spa=65, spd=65, spe=45)
IVS = Stats(hp=31, atk=20, defense=10, spa=31, spd=0, spe=15)

SCRATCH = Move(
    slug="scratch",
    name="Arañazo",
    type="normal",
    damage_class=DamageClass.PHYSICAL,
    power=40,
    accuracy=100,
    pp=35,
)
GROWL = Move(
    slug="growl",
    name="Gruñido",
    type="normal",
    damage_class=DamageClass.STATUS,
    power=None,
    accuracy=100,
    pp=40,
    target="all-opponents",
    category="net-good-stats",
    stat_changes=(StatChange("atk", -1),),
)
SLASH = Move(
    slug="slash",
    name="Cuchillada",
    type="normal",
    damage_class=DamageClass.PHYSICAL,
    power=70,
    accuracy=100,
    pp=20,
    crit_rate=1,
)
FLAMETHROWER = Move(
    slug="flamethrower",
    name="Lanzallamas",
    type="fire",
    damage_class=DamageClass.SPECIAL,
    power=90,
    accuracy=100,
    pp=15,
    category="damage-ailment",
    ailment="burn",
    ailment_chance=10,
)
EMBER = FALLBACK_BY_TYPE["fire"]
METAL_CLAW = FALLBACK_BY_TYPE["steel"]
VINE_WHIP = FALLBACK_BY_TYPE["grass"]
POISON_STING = FALLBACK_BY_TYPE["poison"]

CHARMELEON_BASE = Stats(hp=58, atk=64, defense=58, spa=80, spd=65, spe=80)
CHARMELEON_LEARNSET = (
    LearnsetEntry(level=1, move=SCRATCH),
    LearnsetEntry(level=1, move=GROWL),
    LearnsetEntry(level=7, move=EMBER),
    LearnsetEntry(level=13, move=METAL_CLAW),
    LearnsetEntry(level=25, move=SLASH),
    LearnsetEntry(level=31, move=FLAMETHROWER),
)


def bulbasaur(*, level: int = 50, moves: Sequence[Move] = (TACKLE,)) -> Combatant:
    return build_combatant(
        key="p0",
        species="bulbasaur",
        form="bulbasaur",
        name="Bulbasaur",
        level=level,
        types=["grass", "poison"],
        base_stats=BULBASAUR_BASE,
        ivs=IVS,
        nature="modest",
        moves=moves,
        capture_id=7,
        base_experience=64,
        growth_rate="medium-slow",
        experience=117_360,
    )


def charmeleon(
    level: int,
    *,
    moves: Sequence[Move] = (),
    learnset: Sequence[LearnsetEntry] = CHARMELEON_LEARNSET,
    custom_moves: bool = False,
) -> Combatant:
    return build_combatant(
        key="p1",
        species="charmeleon",
        form="charmeleon",
        name="Charmeleon",
        level=level,
        types=("fire",),
        base_stats=CHARMELEON_BASE,
        ivs=IVS,
        nature="hardy",
        moves=moves,
        learnset=learnset,
        custom_moves=custom_moves,
    )


# --- Stats ------------------------------------------------------------------


def test_build_stats_uses_the_gen3_formula_with_nature() -> None:
    """Bulbasaur Nv 50, Modesta (+AtEsp −Ataque), IV 31/20/10/31/0/15, EV 0:

    PS     floor((2·45+31)·50/100) + 50 + 10      = 60 + 60 = 120
    Ataque floor((2·49+20)·50/100) + 5 = 64 → ×0,9 = 57
    Def.   floor((2·49+10)·50/100) + 5            = 59
    AtEsp  floor((2·65+31)·50/100) + 5 = 85 → ×1,1 = 93
    DefEsp floor((2·65+0)·50/100) + 5             = 70
    Veloc. floor((2·45+15)·50/100) + 5            = 57
    """
    expected = Stats(hp=120, atk=57, defense=59, spa=93, spd=70, spe=57)
    assert build_stats(BULBASAUR_BASE, IVS, 50, "modest") == expected
    assert build_stats(BULBASAUR_BASE, IVS, 50, "Modest") == expected


def test_unknown_nature_is_neutral_and_level_is_clamped() -> None:
    neutral = Stats(hp=120, atk=64, defense=59, spa=85, spd=70, spe=57)
    assert build_stats(BULBASAUR_BASE, IVS, 50, "hardy") == neutral
    assert build_stats(BULBASAUR_BASE, IVS, 50, "desconocida") == neutral
    assert build_stats(BULBASAUR_BASE, IVS, 0, "hardy") == build_stats(
        BULBASAUR_BASE, IVS, 1, "hardy"
    )
    assert build_stats(BULBASAUR_BASE, IVS, 150, "hardy") == build_stats(
        BULBASAUR_BASE, IVS, 100, "hardy"
    )


# --- Construcción -----------------------------------------------------------


def test_build_combatant_starts_ready_to_fight() -> None:
    combatant = bulbasaur(moves=(TACKLE, VINE_WHIP))
    assert combatant.stats == Stats(hp=120, atk=57, defense=59, spa=93, spd=70, spe=57)
    assert combatant.hp == combatant.max_hp == 120
    assert combatant.types == ("grass", "poison")
    assert combatant.moves == (
        MoveSlot(move=TACKLE, pp=35, max_pp=35),
        MoveSlot(move=VINE_WHIP, pp=25, max_pp=25),
    )
    assert (combatant.status, combatant.stages, combatant.volatile) == (
        Status.NONE,
        Stages(),
        Volatile(),
    )
    assert (combatant.capture_id, combatant.growth_rate, combatant.experience) == (
        7,
        "medium-slow",
        117_360,
    )
    assert combatant.learnset == ()
    assert combatant.custom_moves is False


def test_build_combatant_keeps_at_most_four_distinct_moves() -> None:
    moves = (TACKLE, VINE_WHIP, TACKLE, GROWL, POISON_STING, SLASH)
    combatant = bulbasaur(moves=moves)
    assert [slot.move for slot in combatant.moves] == [TACKLE, VINE_WHIP, GROWL, POISON_STING]


# --- Subidas de nivel -------------------------------------------------------


def test_level_up_recalculates_stats_and_raises_current_hp_by_the_same_amount() -> None:
    """Nv 51 con la misma Modesta: PS 122 (+2), Ataque floor(65·0,9) = 58 (+1),
    Def. 60 (+1), AtEsp floor(87·1,1) = 95 (+2), DefEsp 71 (+1), Veloc. 58 (+1)."""
    hurt = replace(bulbasaur(), hp=100)
    grown, deltas = level_up(hurt, 51)
    assert deltas == (("hp", 2), ("atk", 1), ("def", 1), ("spa", 2), ("spd", 1), ("spe", 1))
    assert grown.level == 51
    assert grown.stats == Stats(hp=122, atk=58, defense=60, spa=95, spd=71, spe=58)
    assert (grown.hp, grown.max_hp) == (102, 122)
    assert grown.moves == hurt.moves


def test_level_up_does_not_revive_a_fainted_pokemon() -> None:
    grown, _ = level_up(replace(bulbasaur(), hp=0), 60)
    assert grown.hp == 0
    assert grown.stats == build_stats(BULBASAUR_BASE, IVS, 60, "modest")


# --- Moveset automático -----------------------------------------------------


def test_empty_auto_moveset_learns_the_smart_choice() -> None:
    # Nv 20: Ascuas 40 × 1,5 = 60, Garra Metal 50 × 0,95 = 47,5, Arañazo 40;
    # sin estado útil, Gruñido rellena el cuarto hueco.
    refreshed, learned = refresh_auto_moveset(charmeleon(20))
    assert [slot.move for slot in refreshed.moves] == [EMBER, METAL_CLAW, SCRATCH, GROWL]
    assert learned == (
        ("Ascuas", None),
        ("Garra Metal", None),
        ("Arañazo", None),
        ("Gruñido", None),
    )


def test_new_moves_take_the_forgotten_slot_and_others_keep_their_pp() -> None:
    start, _ = refresh_auto_moveset(charmeleon(20))
    tired = replace(start, moves=(replace(start.moves[0], pp=5), *start.moves[1:]))

    # Nv 25: Cuchillada (70) entra; Arañazo queda ×0,6 por repetir Normal pero
    # aún gana a Gruñido, que se olvida.
    at_25, _ = level_up(tired, 25)
    refreshed, learned = refresh_auto_moveset(at_25)
    assert learned == (("Cuchillada", "Gruñido"),)
    assert refreshed.moves == (
        MoveSlot(move=EMBER, pp=5, max_pp=25),
        MoveSlot(move=METAL_CLAW, pp=35, max_pp=35),
        MoveSlot(move=SCRATCH, pp=35, max_pp=35),
        MoveSlot(move=SLASH, pp=20, max_pp=20),
    )

    # Nv 31: Lanzallamas (135) desplaza a Arañazo (24) en su mismo hueco.
    at_31, _ = level_up(refreshed, 31)
    final, learned = refresh_auto_moveset(at_31)
    assert learned == (("Lanzallamas", "Arañazo"),)
    assert [slot.move for slot in final.moves] == [EMBER, METAL_CLAW, FLAMETHROWER, SLASH]
    assert final.moves[0].pp == 5


def test_refresh_without_changes_returns_the_same_combatant() -> None:
    settled, _ = refresh_auto_moveset(charmeleon(20))
    assert refresh_auto_moveset(settled) == (settled, ())


def test_refresh_respects_custom_movesets_and_missing_learnsets() -> None:
    custom = charmeleon(40, moves=(SCRATCH,), custom_moves=True)
    assert refresh_auto_moveset(custom) == (custom, ())
    without_learnset = charmeleon(40, moves=(SCRATCH,), learnset=())
    assert refresh_auto_moveset(without_learnset) == (without_learnset, ())
    too_young = charmeleon(5, moves=(TACKLE,), learnset=(LearnsetEntry(level=7, move=EMBER),))
    assert refresh_auto_moveset(too_young) == (too_young, ())
