"""Codec del estado: ida y vuelta exacta y validación estricta de lo corrupto."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pokedex_cli.domain.battle.codec import SCHEMA_VERSION, state_from_dict, state_to_dict
from pokedex_cli.domain.battle.combatants import build_combatant
from pokedex_cli.domain.battle.model import (
    BattleKind,
    BattleState,
    Combatant,
    LearnsetEntry,
    MoveSlot,
    Outcome,
    Phase,
    Side,
    Stages,
    Stats,
    Status,
    Volatile,
)
from pokedex_cli.domain.battle.moves import STRUGGLE, TACKLE
from tests.domain.battle.test_engine import EMBER, HYPNOSIS, SWORDS_DANCE, WATER_GUN, WRAP
from tests.domain.battle.test_moves import valid_moves

BASE = Stats(hp=79, atk=83, defense=100, spa=85, spd=105, spe=78)
IVS = Stats(hp=31, atk=0, defense=15, spa=20, spd=7, spe=31)


def sample_state() -> BattleState:
    """Combate a medias con todo lo que el codec debe conservar."""
    lead = build_combatant(
        key="p0",
        species="blastoise",
        form="blastoise",
        name="Caparazón",
        level=36,
        types=("water",),
        base_stats=BASE,
        ivs=IVS,
        nature="modest",
        moves=(WATER_GUN, TACKLE),
        shiny=True,
        gender="female",
        ability="torrent",
        capture_id=12,
        base_experience=239,
        growth_rate="medium-slow",
        experience=36_000,
        learnset=(LearnsetEntry(1, TACKLE), LearnsetEntry(7, WATER_GUN), LearnsetEntry(0, TACKLE)),
    )
    lead = replace(
        lead,
        hp=50,
        status=Status.TOXIC,
        status_turns=3,
        stages=Stages(atk=-1, spe=2, evasion=6),
        volatile=Volatile(
            confusion_turns=2,
            protect_streak=1,
            charging="solar-beam",
            trapped_turns=3,
            trapped_by="Constricción",
            seeded=True,
            focus_energy=True,
            last_move="tackle",
        ),
    )
    bench = build_combatant(
        key="p1",
        species="pikachu",
        form="pikachu-alola-cap",
        name="Pika",
        level=12,
        types=("electric",),
        base_stats=BASE,
        ivs=IVS,
        nature="unknown",
        moves=(STRUGGLE, SWORDS_DANCE, HYPNOSIS, EMBER),
        custom_moves=True,
    )
    foe = build_combatant(
        key="o0",
        species="onix",
        form="onix",
        name="Onix",
        level=14,
        types=("rock", "ground"),
        base_stats=BASE,
        ivs=IVS,
        nature="hardy",
        moves=(WRAP, TACKLE),
    )
    return BattleState(
        kind=BattleKind.TRAINER,
        player=Side(team=(lead, bench), active=0, reflect_turns=3),
        opponent=Side(team=(foe,), light_screen_turns=5, trainer="Montañero Anselmo"),
        turn=17,
        escape_attempts=2,
        participants=(("o0", ("p0", "p1")),),
    )


def raw_sample() -> dict[str, object]:
    return state_to_dict(sample_state())


def test_a_built_state_round_trips_exactly_through_json() -> None:
    state = sample_state()
    raw = state_to_dict(state)
    assert raw["v"] == SCHEMA_VERSION == 1
    assert state_from_dict(raw) == state
    assert state_from_dict(json.loads(json.dumps(raw))) == state


def test_finished_states_keep_their_outcome() -> None:
    for outcome in Outcome:
        state = BattleState(
            kind=BattleKind.WILD,
            player=sample_state().player,
            opponent=Side(team=sample_state().opponent.team),
            phase=Phase.FINISHED,
            outcome=outcome,
            award_experience=False,
        )
        assert state_from_dict(json.loads(json.dumps(state_to_dict(state)))) == state


def test_each_distinct_move_is_stored_once() -> None:
    raw = raw_sample()
    slugs = [record["slug"] for record in raw["moves"]]  # type: ignore[attr-defined]
    assert len(slugs) == len(set(slugs))
    assert set(slugs) == {
        "water-gun",
        "tackle",
        "struggle",
        "swords-dance",
        "hypnosis",
        "ember",
        "wrap",
    }


# --- Corrupción -------------------------------------------------------------

_DELETE = object()


def corrupted(path: tuple[object, ...], value: object = _DELETE) -> dict[str, object]:
    raw = copy.deepcopy(raw_sample())
    node: object = raw
    for step in path[:-1]:
        node = node[step]  # type: ignore[index]
    if value is _DELETE:
        del node[path[-1]]  # type: ignore[attr-defined]
    else:
        node[path[-1]] = value  # type: ignore[index]
    return raw


P0 = ("player", "team", 0)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("v",), 2),
        (("v",), True),
        (("v",), _DELETE),
        (("kind",), "gym"),
        (("phase",), "fighting"),
        (("phase",), "finished"),  # terminado sin resultado
        (("outcome",), "won"),  # resultado sin terminar
        (("turn",), -1),
        (("turn",), "17"),
        (("escape_attempts",), True),
        (("award_experience",), 1),
        (("participants",), [["o9", ["p0"]]]),
        (("participants",), [["o0", ["p7"]]]),
        (("participants",), [["o0"]]),
        (("participants",), [["o0", ["p0", "p0"]]]),
        (("participants",), [["o0", ["p0"]], ["o0", ["p1"]]]),
        (("participants",), "o0"),
        (("moves", 0, "pp"), 0),
        # La caché tolera huecos en un registro; un estado guardado, no.
        (("moves", 0, "priority"), _DELETE),
        (("moves", 0, "power"), _DELETE),
        (("moves", 0, "category"), None),
        (("moves", 0, "name"), ""),
        (("moves", 0, "stat_changes"), None),
        (("moves", 0), "water-gun"),
        (("moves",), {}),
        (("player", "team"), []),
        (("player", "team"), [raw_sample()["opponent"]["team"][0]] * 7),  # type: ignore[index]
        (("player", "active"), 2),
        (("player", "active"), -1),
        (("player", "reflect_turns"), -2),
        (("opponent", "trainer"), 7),
        ((*P0, "key"), "o0"),  # clave repetida entre lados
        ((*P0, "key"), ""),
        ((*P0, "name"), None),
        ((*P0, "level"), 0),
        ((*P0, "level"), 101),
        ((*P0, "types"), "water"),
        ((*P0, "types"), [1]),
        ((*P0, "hp"), 10_000),
        ((*P0, "hp"), -1),
        ((*P0, "stats", "spe"), 0),
        ((*P0, "stats", "def"), _DELETE),
        ((*P0, "ivs", "hp"), -1),
        ((*P0, "moves"), [{"move": 0, "pp": 1, "max_pp": 1}] * 5),
        ((*P0, "moves", 0, "pp"), 99),
        ((*P0, "moves", 0, "max_pp"), 0),
        ((*P0, "moves", 0, "move"), 99),
        ((*P0, "moves", 0, "move"), -1),
        ((*P0, "moves", 0, "move"), False),
        ((*P0, "status"), "sick"),
        ((*P0, "status_turns"), -1),
        ((*P0, "stages", "evasion"), 7),
        ((*P0, "stages", "accuracy"), _DELETE),
        ((*P0, "volatile", "flinched"), 0),
        ((*P0, "volatile", "charging"), 3),
        ((*P0, "volatile", "seeded"), _DELETE),
        ((*P0, "capture_id"), "12"),
        ((*P0, "experience"), -5),
        ((*P0, "learnset"), [[1]]),
        ((*P0, "learnset"), [[101, 0]]),
        ((*P0, "learnset"), [["1", 0]]),
        ((*P0, "custom_moves"), "no"),
        ((*P0, "shiny"), _DELETE),
    ],
)
def test_corrupt_data_is_rejected(path: tuple[object, ...], value: object) -> None:
    assert state_from_dict(corrupted(path, value)) is None


@pytest.mark.parametrize("raw", [None, [], "estado", 3, {"v": 1}])
def test_anything_that_is_not_a_state_is_rejected(raw: object) -> None:
    assert state_from_dict(raw) is None


def _paths(node: object, prefix: tuple[object, ...] = ()) -> Iterator[tuple[object, ...]]:
    if isinstance(node, dict):
        for key, child in node.items():
            yield (*prefix, key)
            yield from _paths(child, (*prefix, key))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield (*prefix, index)
            yield from _paths(child, (*prefix, index))


_ALL_PATHS = list(_paths(raw_sample()))
_JUNK = st.sampled_from([_DELETE, None, -1, 0, 10**9, True, "x", "", [], {}, [0], 1.5])


@settings(max_examples=150, deadline=None)
@given(path=st.sampled_from(_ALL_PATHS), junk=_JUNK)
def test_any_single_mutation_never_raises(path: tuple[object, ...], junk: object) -> None:
    raw = copy.deepcopy(raw_sample())
    node: object = raw
    for step in path[:-1]:
        node = node[step]  # type: ignore[index]
    if junk is _DELETE:
        if isinstance(node, dict):
            del node[path[-1]]
        else:
            node.pop(path[-1])  # type: ignore[attr-defined]
    else:
        node[path[-1]] = junk  # type: ignore[index]
    decoded = state_from_dict(raw)
    assert decoded is None or isinstance(decoded, BattleState)


_JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(max_size=5),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=20,
)


@settings(max_examples=100, deadline=None)
@given(_JSON)
def test_arbitrary_json_never_raises(raw: object) -> None:
    decoded = state_from_dict(raw)
    assert decoded is None or isinstance(decoded, BattleState)


# --- Estados aleatorios -----------------------------------------------------

_TEXT = st.text(min_size=1, max_size=8)


@st.composite
def _stats(draw: st.DrawFn, low: int) -> Stats:
    values = [draw(st.integers(min_value=low, max_value=400)) for _ in range(6)]
    return Stats(*values)


@st.composite
def _combatants(draw: st.DrawFn, key: str) -> Combatant:
    stats = draw(_stats(1))
    slots = []
    for move in draw(st.lists(valid_moves(), max_size=4)):
        max_pp = draw(st.integers(min_value=1, max_value=64))
        slots.append(MoveSlot(move=move, pp=draw(st.integers(0, max_pp)), max_pp=max_pp))
    small = st.integers(min_value=0, max_value=20)
    maybe_text = st.none() | _TEXT
    return Combatant(
        key=key,
        species=draw(_TEXT),
        form=draw(st.text(max_size=8)),
        name=draw(_TEXT),
        level=draw(st.integers(1, 100)),
        types=tuple(draw(st.lists(_TEXT, max_size=2))),
        base_stats=draw(_stats(1)),
        ivs=draw(_stats(0)),
        nature=draw(st.text(max_size=8)),
        stats=stats,
        hp=draw(st.integers(0, stats.hp)),
        moves=tuple(slots),
        shiny=draw(st.booleans()),
        gender=draw(maybe_text),
        ability=draw(maybe_text),
        status=draw(st.sampled_from(Status)),
        status_turns=draw(small),
        stages=Stages(*(draw(st.integers(-6, 6)) for _ in range(7))),
        volatile=Volatile(
            confusion_turns=draw(small),
            flinched=draw(st.booleans()),
            protected=draw(st.booleans()),
            protect_streak=draw(small),
            charging=draw(maybe_text),
            semi_invulnerable=draw(st.booleans()),
            recharging=draw(st.booleans()),
            trapped_turns=draw(small),
            trapped_by=draw(maybe_text),
            seeded=draw(st.booleans()),
            focus_energy=draw(st.booleans()),
            last_move=draw(maybe_text),
        ),
        capture_id=draw(st.none() | st.integers(0, 10**6)),
        base_experience=draw(st.integers(0, 400)),
        growth_rate=draw(st.text(max_size=12)),
        experience=draw(st.integers(0, 2_000_000)),
        learnset=tuple(
            LearnsetEntry(level=draw(st.integers(0, 100)), move=move)
            for move in draw(st.lists(valid_moves(), max_size=3))
        ),
        custom_moves=draw(st.booleans()),
    )


@st.composite
def states(draw: st.DrawFn) -> BattleState:
    player = tuple(draw(_combatants(f"p{i}")) for i in range(draw(st.integers(1, 3))))
    opponent = tuple(draw(_combatants(f"o{i}")) for i in range(draw(st.integers(1, 3))))
    phase = draw(st.sampled_from(Phase))
    player_keys = [member.key for member in player]
    participants = tuple(
        (foe.key, tuple(draw(st.lists(st.sampled_from(player_keys), unique=True))))
        for foe in opponent
        if draw(st.booleans())
    )
    return BattleState(
        kind=draw(st.sampled_from(BattleKind)),
        player=Side(
            team=player,
            active=draw(st.integers(0, len(player) - 1)),
            reflect_turns=draw(st.integers(0, 5)),
            light_screen_turns=draw(st.integers(0, 5)),
        ),
        opponent=Side(
            team=opponent,
            active=draw(st.integers(0, len(opponent) - 1)),
            trainer=draw(st.none() | _TEXT),
        ),
        turn=draw(st.integers(0, 300)),
        phase=phase,
        outcome=draw(st.sampled_from(Outcome)) if phase is Phase.FINISHED else None,
        escape_attempts=draw(st.integers(0, 10)),
        participants=participants,
        award_experience=draw(st.booleans()),
    )


@settings(max_examples=40, deadline=None)
@given(states())
def test_any_valid_state_round_trips_exactly_through_json(state: BattleState) -> None:
    raw = json.loads(json.dumps(state_to_dict(state)))
    assert state_from_dict(raw) == state
