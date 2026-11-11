"""IA del combate: salvajes al azar, entrenadores que puntúan y relevos con ventaja."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pokedex_cli.domain.battle.ai import (
    BEST_MOVE_CHANCE,
    UNSUPPORTED_SCORE,
    choose_action,
    choose_replacement_index,
    move_score,
)
from pokedex_cli.domain.battle.model import (
    BattleKind,
    BattleState,
    Combatant,
    Move,
    Stages,
    Status,
    UseMove,
    Volatile,
)
from pokedex_cli.domain.battle.moves import TACKLE
from tests.domain.battle.test_engine import (
    DREAM_EATER,
    EMBER,
    FAKE_OUT,
    HEAL_PULSE,
    HYPER_BEAM,
    HYPNOSIS,
    PROTECT,
    QUICK_ATTACK,
    RECOVER,
    SOLAR_BEAM,
    SPLASH,
    SWORDS_DANCE,
    THUNDER_WAVE,
    TOXIC,
    TRANSFORM,
    WATER_GUN,
    ScriptedRandom,
    duel,
    mon,
    with_pp,
)

# Un rival con muchos PS: un Placaje apenas le quita un 7 %.
TANK = 300


def trainer_battle(me: Combatant, foe: Combatant) -> BattleState:
    return duel(me, foe, kind=BattleKind.TRAINER)


def best(me: Combatant, foe: Combatant) -> int:
    """Hueco que elige un entrenador cuando va a por el mejor (tirada 0,0)."""
    rng = ScriptedRandom(floats=(0.0,))
    action = choose_action(trainer_battle(me, foe), "player", rng)
    assert isinstance(action, UseMove)
    assert rng.exhausted
    return action.slot


# --- Salvajes y casos sin elección ------------------------------------------


def test_a_wild_pokemon_picks_at_random_among_moves_with_pp() -> None:
    wild = with_pp(mon("o0", TACKLE, EMBER, WATER_GUN), 0, 0)
    state = duel(mon("p0"), wild)
    rng = ScriptedRandom(ints=(1,))
    assert choose_action(state, "opponent", rng) == UseMove(2)
    assert rng.exhausted


def test_trivial_choices_spend_no_randomness() -> None:
    state = duel(mon("p0", SPLASH, TACKLE), with_pp(mon("o0", TACKLE, EMBER), 1, 0))
    assert choose_action(state, "opponent", ScriptedRandom()) == UseMove(0)
    tired = duel(mon("p0"), with_pp(mon("o0"), 0, 0))
    assert choose_action(tired, "opponent", ScriptedRandom()) == UseMove(0)
    for volatile in (Volatile(charging="solar-beam"), Volatile(recharging=True)):
        locked = duel(mon("p0", SPLASH, TACKLE, volatile=volatile), mon("o0"))
        assert choose_action(locked, "player", ScriptedRandom()) == UseMove(0)


def test_trainers_take_the_best_move_four_times_out_of_five() -> None:
    me = mon("p0", TACKLE, WATER_GUN)
    foe = mon("o0", types=("fire",))
    assert BEST_MOVE_CHANCE == 0.8
    assert best(me, foe) == 1
    rng = ScriptedRandom(floats=(0.85,), ints=(0,))
    assert choose_action(trainer_battle(me, foe), "player", rng) == UseMove(0)
    assert rng.exhausted


# --- Daño -------------------------------------------------------------------


def test_super_effective_beats_stab_and_immunity_is_worthless() -> None:
    me = mon("p0", TACKLE, WATER_GUN)
    assert best(me, mon("o0", types=("fire",))) == 1
    ghost_hunter = mon("p0", TACKLE, EMBER, types=("normal",))
    assert best(ghost_hunter, mon("o0", types=("ghost",))) == 1
    state = trainer_battle(ghost_hunter, mon("o0", types=("ghost",)))
    assert move_score(state, "player", TACKLE) == 0


def test_a_sure_knockout_wins_over_raw_power() -> None:
    me = mon("p0", HYPER_BEAM, QUICK_ATTACK)
    assert best(me, mon("o0", hp=10)) == 1
    assert best(me, mon("o0", max_hp=TANK)) == 0


def test_two_turn_moves_and_unready_targets_score_less() -> None:
    me = mon("p0", SOLAR_BEAM, TACKLE)
    state = trainer_battle(me, mon("o0"))
    assert move_score(state, "player", SOLAR_BEAM) < 2 * move_score(state, "player", TACKLE)
    flying = trainer_battle(
        me, mon("o0", volatile=Volatile(charging="fly", semi_invulnerable=True))
    )
    assert move_score(flying, "player", TACKLE) == 0
    awake = trainer_battle(mon("p0", DREAM_EATER), mon("o0"))
    asleep = trainer_battle(mon("p0", DREAM_EATER), mon("o0", status=Status.SLEEP))
    assert move_score(awake, "player", DREAM_EATER) == 0
    assert move_score(asleep, "player", DREAM_EATER) > 0


def test_fake_out_is_worthless_after_the_first_turn() -> None:
    me = mon("p0", FAKE_OUT, TACKLE)
    fresh = trainer_battle(me, mon("o0", max_hp=TANK))
    assert move_score(fresh, "player", FAKE_OUT) > 0
    seasoned = replace(me, volatile=Volatile(last_move="tackle"))
    later = trainer_battle(seasoned, mon("o0", max_hp=TANK))
    assert move_score(later, "player", FAKE_OUT) == 0
    assert best(seasoned, mon("o0", max_hp=TANK)) == 1


# --- Estado -----------------------------------------------------------------


def test_status_moves_only_against_a_healthy_and_vulnerable_target() -> None:
    me = mon("p0", TACKLE, HYPNOSIS)
    assert best(me, mon("o0", max_hp=TANK)) == 1
    assert best(me, mon("o0", max_hp=TANK, status=Status.POISON)) == 0
    zapper = mon("p0", TACKLE, THUNDER_WAVE)
    assert best(zapper, mon("o0", max_hp=TANK)) == 1
    assert best(zapper, mon("o0", max_hp=TANK, types=("ground",))) == 0
    assert best(zapper, mon("o0", max_hp=TANK, types=("electric",))) == 0
    poisoner = mon("p0", TACKLE, TOXIC)
    assert best(poisoner, mon("o0", max_hp=TANK, types=("steel",))) == 0


def test_boosts_early_with_high_hp_and_heals_when_low() -> None:
    dancer = mon("p0", TACKLE, SWORDS_DANCE)
    assert best(dancer, mon("o0", max_hp=TANK)) == 1
    assert best(replace(dancer, hp=50), mon("o0", max_hp=TANK)) == 0
    assert best(replace(dancer, stages=Stages(atk=2)), mon("o0", max_hp=TANK)) == 0
    healer = mon("p0", TACKLE, RECOVER)
    assert best(replace(healer, hp=30), mon("o0", max_hp=TANK)) == 1
    assert best(replace(healer, hp=80), mon("o0", max_hp=TANK)) == 0


@pytest.mark.parametrize("useless", [TRANSFORM, HEAL_PULSE], ids=lambda move: move.slug)
def test_unsupported_moves_and_healing_the_foe_are_never_the_best(useless: Move) -> None:
    me = mon("p0", useless, SPLASH, hp=20)
    state = trainer_battle(me, mon("o0"))
    assert move_score(state, "player", useless) < 0
    assert best(me, mon("o0")) == 1
    assert move_score(state, "player", TRANSFORM) == UNSUPPORTED_SCORE


def test_protect_is_only_worth_starting_a_chain() -> None:
    state = trainer_battle(mon("p0", PROTECT), mon("o0"))
    chained = trainer_battle(mon("p0", PROTECT, volatile=Volatile(protect_streak=1)), mon("o0"))
    assert move_score(state, "player", PROTECT) > 0
    assert move_score(chained, "player", PROTECT) == 0


# --- Relevos ----------------------------------------------------------------


def test_replacement_prefers_type_advantage_and_breaks_ties_in_team_order() -> None:
    fainted = mon("p0", hp=0)
    grass = mon("p1", types=("grass",))
    water = mon("p2", WATER_GUN, types=("water",))
    state = duel(fainted, mon("o0", EMBER, types=("fire",)), bench=(grass, water))
    assert choose_replacement_index(state, "player", ScriptedRandom()) == 2
    even = duel(fainted, mon("o0"), bench=(mon("p1"), mon("p2")))
    assert choose_replacement_index(even, "player", ScriptedRandom()) == 1
    alone = duel(mon("p0"), mon("o0"))
    with pytest.raises(ValueError):
        choose_replacement_index(alone, "player", ScriptedRandom())
