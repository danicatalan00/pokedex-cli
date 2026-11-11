"""Propiedades del motor: combates completos IA contra IA generados con Hypothesis.

Cada combate sale de un catálogo escrito a mano que cubre todas las
categorías del motor. Tras cada paso se comprueban los invariantes del
estado, que los eventos bastan para reconstruir los PS y que el codec
devuelve exactamente el mismo estado.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pokedex_cli.domain.battle.ai import choose_action, choose_replacement_index
from pokedex_cli.domain.battle.codec import state_from_dict, state_to_dict
from pokedex_cli.domain.battle.combatants import build_combatant
from pokedex_cli.domain.battle.engine import (
    action_error,
    begin,
    choose_replacement,
    resolve_turn,
)
from pokedex_cli.domain.battle.model import (
    MAX_STAGE,
    MAX_TURNS,
    MIN_STAGE,
    SIDES,
    STAGE_KEYS,
    BattleEnded,
    BattleEvent,
    BattleKind,
    BattleState,
    Combatant,
    ConfusionCheck,
    Damaged,
    Fainted,
    Healed,
    Immobilized,
    LearnsetEntry,
    LeveledUp,
    MoveUsed,
    NoMovesLeft,
    Phase,
    SentOut,
    Side,
    Stats,
    TurnResult,
    TurnStarted,
)
from pokedex_cli.domain.battle.typechart import TYPES
from pokedex_cli.domain.individuality import NATURES
from pokedex_cli.domain.progression import experience_for_level
from tests.domain.battle.test_engine import CATALOG

GROWTH_RATES = ("medium", "fast", "slow", "medium-slow", "erratic", "fluctuating")
# Turnos más relevos: un combate de IA nunca debería necesitar más pasos.
MAX_STEPS = MAX_TURNS + 3 * 6


@st.composite
def fighters(draw: st.DrawFn, key: str) -> Combatant:
    level = draw(st.integers(min_value=1, max_value=100))
    growth = draw(st.sampled_from(GROWTH_RATES))
    moves = draw(
        st.lists(st.sampled_from(CATALOG), min_size=1, max_size=4, unique_by=lambda m: m.slug)
    )
    # Movimientos aprendibles cerca de su nivel: unos ya, otros al subir.
    learned_at = st.integers(min_value=1, max_value=min(100, level + 8))
    learnset = [
        LearnsetEntry(level=learned, move=move)
        for learned, move in draw(
            st.lists(st.tuples(learned_at, st.sampled_from(CATALOG)), max_size=6)
        )
    ]
    return build_combatant(
        key=key,
        species=f"especie-{key}",
        form="default",
        name=key.upper(),
        level=level,
        types=draw(st.lists(st.sampled_from(TYPES), min_size=1, max_size=2, unique=True)),
        base_stats=Stats(*(draw(st.integers(min_value=5, max_value=180)) for _ in range(6))),
        ivs=Stats(*(draw(st.integers(min_value=0, max_value=31)) for _ in range(6))),
        nature=draw(st.sampled_from(NATURES)).name,
        moves=moves,
        base_experience=draw(st.integers(min_value=1, max_value=320)),
        growth_rate=growth,
        experience=experience_for_level(growth, level),
        learnset=learnset if key.startswith("p") else (),
        custom_moves=draw(st.booleans()),
    )


@st.composite
def battles(draw: st.DrawFn) -> tuple[BattleState, int]:
    kind = draw(st.sampled_from(BattleKind))
    player = tuple(draw(fighters(f"p{i}")) for i in range(draw(st.integers(1, 3))))
    foes = 1 if kind is BattleKind.WILD else draw(st.integers(1, 3))
    opponent = tuple(draw(fighters(f"o{i}")) for i in range(foes))
    state = BattleState(
        kind=kind,
        player=Side(team=player),
        opponent=Side(
            team=opponent, trainer="Médium Leocadia" if kind is BattleKind.TRAINER else None
        ),
        award_experience=kind is not BattleKind.EXHIBITION,
    )
    return state, draw(st.integers(min_value=0, max_value=2**32 - 1))


def run_battle(state: BattleState, seed: int) -> list[tuple[BattleState, TurnResult]]:
    """Juega un combate entero con la IA a los dos lados; devuelve cada paso."""
    rng = random.Random(seed)
    steps = [(state, begin(state))]
    current = steps[0][1].state
    while not current.finished:
        assert len(steps) <= MAX_STEPS, "el combate no termina"
        if current.phase is Phase.CHOOSE_REPLACEMENT:
            index = choose_replacement_index(current, "player", rng)
            result = choose_replacement(current, index, rng)
        else:
            action = choose_action(current, "player", rng)
            assert action_error(current, action) is None, action
            result = resolve_turn(current, action, rng)
        steps.append((current, result))
        current = result.state
    return steps


def members(state: BattleState) -> list[Combatant]:
    return [member for side in SIDES for member in state.side(side).team]


def check_state(state: BattleState) -> None:
    assert 0 <= state.turn <= MAX_TURNS
    assert (state.phase is Phase.FINISHED) == (state.outcome is not None)
    for member in members(state):
        assert 0 <= member.hp <= member.max_hp
        assert 1 <= member.level <= 100
        assert member.status_turns >= 0
        for slot in member.moves:
            assert 0 <= slot.pp <= slot.max_pp
        for key in STAGE_KEYS:
            assert MIN_STAGE <= member.stages.value(key) <= MAX_STAGE
        volatile = member.volatile
        assert volatile.confusion_turns >= 0 and volatile.trapped_turns >= 0
    if state.phase is Phase.CHOOSE_ACTION:
        assert not state.player.current.fainted and not state.opponent.current.fainted
    if state.phase is Phase.CHOOSE_REPLACEMENT:
        assert state.player.current.fainted and not state.opponent.current.fainted
        assert any(index != state.player.active for index in state.player.able)


def check_events(before: BattleState, result: TurnResult) -> None:
    """Los eventos bastan para seguir los PS y nadie actúa tras debilitarse."""
    shadow = {member.key: [member.hp, member.max_hp] for member in members(before)}
    fainted: set[str] = set()
    for event in result.events:
        if isinstance(event, (MoveUsed, NoMovesLeft, Immobilized, ConfusionCheck)):
            assert event.key not in fainted, f"{event.key} actuó debilitado"
        if isinstance(event, Damaged):
            hp, max_hp = shadow[event.key]
            assert (event.hp_before, event.max_hp) == (hp, max_hp)
            assert 0 <= event.hp_after <= event.hp_before
            assert event.amount == event.hp_before - event.hp_after
            shadow[event.key][0] = event.hp_after
        elif isinstance(event, Healed):
            hp, max_hp = shadow[event.key]
            assert (event.hp_before, event.max_hp) == (hp, max_hp)
            assert 0 < event.amount == event.hp_after - event.hp_before
            assert event.hp_after <= max_hp
            shadow[event.key][0] = event.hp_after
        elif isinstance(event, LeveledUp):
            hp, max_hp = shadow[event.key]
            gained = dict(event.deltas)["hp"]
            shadow[event.key] = [hp if hp == 0 else max(1, min(max_hp + gained, hp + gained)),
                                 max_hp + gained]  # fmt: skip
        elif isinstance(event, Fainted):
            assert shadow[event.key][0] == 0
            fainted.add(event.key)
        elif isinstance(event, SentOut):
            assert event.key in shadow and shadow[event.key][0] > 0
    for member in members(result.state):
        assert shadow[member.key] == [member.hp, member.max_hp], member.key


def check_step(before: BattleState, result: TurnResult) -> None:
    after = result.state
    check_state(after)
    check_events(before, result)
    ended = [event for event in result.events if isinstance(event, BattleEnded)]
    if after.finished:
        assert ended == [BattleEnded(after.outcome)] and result.events[-1] == ended[0]  # type: ignore[arg-type]
    else:
        assert not ended
    if after.turn == before.turn + 1:
        assert result.events[0] == TurnStarted(after.turn)
    else:
        assert after.turn == before.turn
        assert not any(isinstance(event, TurnStarted) for event in result.events)
    for old, new in zip(members(before), members(after), strict=True):
        assert new.experience >= old.experience and new.level >= old.level
        assert not (old.fainted and not new.fainted), f"{old.key} revivió"
    raw = json.loads(json.dumps(state_to_dict(after)))
    assert state_from_dict(raw) == after


@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(battles())
def test_ai_battles_keep_every_invariant_and_always_end(battle: tuple[BattleState, int]) -> None:
    state, seed = battle
    steps = run_battle(state, seed)
    for before, result in steps:
        check_step(before, result)
    final = steps[-1][1].state
    assert final.finished and final.outcome is not None


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(battles())
def test_the_same_seed_replays_the_same_battle(battle: tuple[BattleState, int]) -> None:
    state, seed = battle

    def log(steps: Sequence[tuple[BattleState, TurnResult]]) -> list[BattleEvent]:
        return [event for _, result in steps for event in result.events]

    first, second = run_battle(state, seed), run_battle(state, seed)
    assert log(first) == log(second)
    assert first[-1][1].state == second[-1][1].state
