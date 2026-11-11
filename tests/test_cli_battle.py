"""CLI del combate: pantalla completa, modo texto, comandos e integraciones.

Todo va con dobles: sesiones con turnos guionizados (o una exhibición real con
azar fijo), un teclado guionizado cuyas esperas vencen solas y consolas de rich
en memoria. Nada toca la terminal real, la BD del HOME, krabby ni la red.
"""

from __future__ import annotations

import argparse
import io
import os
import random
import re
import sqlite3
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from rich.cells import cell_len
from rich.console import Console
from rich.segment import Segment
from rich.text import Text

from pokedex_cli import cli, storage
from pokedex_cli.application.battle import (
    Auto,
    BattleStatus,
    BattleView,
    ExhibitionSession,
    Fight,
    Flee,
    Replace,
    Switch,
    ThrowBall,
    TurnOutcome,
)
from pokedex_cli.application.evolutions import PendingEvolution
from pokedex_cli.application.hook import OpenTerminalResult
from pokedex_cli.application.movesets import (
    LearnableMove,
    MovesetResult,
    MovesetStatus,
    MovesetView,
)
from pokedex_cli.domain.battle.combatants import build_combatant
from pokedex_cli.domain.battle.model import (
    BallThrown,
    BattleEnded,
    BattleKind,
    BattleStarted,
    BattleState,
    DamageClass,
    Damaged,
    ExperienceGained,
    Fainted,
    HitCount,
    LeveledUp,
    Move,
    MoveLearned,
    MoveUsed,
    Outcome,
    Phase,
    SentOut,
    Side,
    StatChange,
    Stats,
    TurnStarted,
    Volatile,
)
from pokedex_cli.domain.battle.moves import FALLBACK_BY_TYPE, TACKLE
from pokedex_cli.domain.identity import normalize_species
from pokedex_cli.presentation import battle_cli
from pokedex_cli.presentation.battle_cli import (
    Bag,
    BattleOptions,
    BattleReport,
    BattleServices,
    CatchEstimator,
    ScreenBattle,
    TerminalKeyboard,
    parse_order,
)

EMBER = FALLBACK_BY_TYPE["fire"]
THUNDER_SHOCK = FALLBACK_BY_TYPE["electric"]
SCRATCH = Move("scratch", "Arañazo", "normal", DamageClass.PHYSICAL, 40, 100, 35)
GROWL = Move(
    "growl",
    "Gruñido",
    "normal",
    DamageClass.STATUS,
    None,
    100,
    40,
    target="all-opponents",
    category="net-good-stats",
    stat_changes=(StatChange("atk", -1),),
)
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
ALT_SCREEN_ON, ALT_SCREEN_OFF = "\x1b[?1049h", "\x1b[?1049l"
SHOW_CURSOR = "\x1b[?25h"
# La consulta real (nunca lanza); el fixture de abajo la sustituye en cada test.
REAL_ACTIVE_WILD_BATTLE_NAME = cli.composition.active_wild_battle_name


@pytest.fixture(autouse=True)
def no_battle_in_progress(monkeypatch):
    """Por defecto no hay combate en pausa (y nadie abre la BD del HOME real)."""
    monkeypatch.setattr(cli.composition, "active_wild_battle_name", lambda: None)


# --- Estados y eventos -----------------------------------------------------------


def fighter(key, species, name, level, types, moves, **extra):
    return build_combatant(
        key=key,
        species=species,
        form="regular",
        name=name,
        level=level,
        types=types,
        base_stats=Stats(50, 50, 50, 50, 50, 50),
        ivs=Stats(31, 31, 31, 31, 31, 31),
        nature="hardy",
        moves=moves,
        **extra,
    )


def battle_state(kind=BattleKind.WILD, *, trainer=None, **changes) -> BattleState:
    player = Side(
        (
            fighter(
                "p0",
                "charmander",
                "Chamusca",
                15,
                ("fire",),
                (EMBER, SCRATCH, GROWL),
                capture_id=1,
                gender="male",
            ),
            fighter("p1", "pikachu", "Chispa", 12, ("electric",), (THUNDER_SHOCK,), capture_id=2),
        )
    )
    opponent = Side(
        (fighter("o0", "pidgey", "Pidgey", 5, ("normal", "flying"), (TACKLE,)),), trainer=trainer
    )
    return BattleState(kind=kind, player=player, opponent=opponent, **changes)


def finished(state: BattleState, outcome: Outcome) -> BattleState:
    foe = replace(state.opponent.current, hp=0)
    return replace(
        state, phase=Phase.FINISHED, outcome=outcome, opponent=state.opponent.with_current(foe)
    )


def opening(state: BattleState):
    foe, ally = state.opponent.current, state.player.current
    return (
        BattleStarted(state.kind, state.opponent.trainer),
        SentOut("opponent", foe.key, foe.name, foe.species, foe.form, foe.shiny, foe.level),
        SentOut("player", ally.key, ally.name, ally.species, ally.form, ally.shiny, ally.level),
    )


def ember_hits(state: BattleState, damage: int = 7):
    foe = state.opponent.current
    return (
        MoveUsed("player", "p0", "Chamusca", "ember", "Ascuas", "fire", DamageClass.SPECIAL),
        Damaged("opponent", "o0", "Pidgey", damage, foe.hp, foe.hp - damage, foe.max_hp),
    )


def applied(state: BattleState, events) -> TurnOutcome:
    foe = state.opponent.current
    hurt = state.opponent.with_current(replace(foe, hp=foe.hp - 7))
    return TurnOutcome(
        BattleStatus.APPLIED, tuple(events), replace(state, turn=state.turn + 1, opponent=hurt)
    )


def victory(state: BattleState, **extra) -> TurnOutcome:
    foe = state.opponent.current
    events = (
        TurnStarted(state.turn + 1),
        MoveUsed("player", "p0", "Chamusca", "ember", "Ascuas", "fire", DamageClass.SPECIAL),
        Damaged("opponent", "o0", "Pidgey", foe.hp, foe.hp, 0, foe.max_hp, effectiveness=2.0),
        Fainted("opponent", "o0", "Pidgey"),
        ExperienceGained("p0", "Chamusca", 35),
        BattleEnded(Outcome.WON),
    )
    return TurnOutcome(
        BattleStatus.FINISHED,
        events,
        replace(finished(state, Outcome.WON), turn=state.turn + 1),
        **extra,
    )


# --- Dobles ----------------------------------------------------------------------


class FakeSession:
    """Sesión con turnos guionizados: cada orden consume el siguiente resultado."""

    def __init__(self, state, outcomes=(), *, opening_events=()) -> None:
        self._state = state
        self.outcomes = list(outcomes)
        self.commands: list = []
        self._opening = tuple(opening_events)

    @property
    def state(self):
        return self._state

    @property
    def kind(self):
        return self._state.kind

    @property
    def trainer(self):
        return self._state.opponent.trainer

    @property
    def opening(self):
        return self._opening

    def take(self, command):
        self.commands.append(command)
        if not self.outcomes:
            raise AssertionError(f"orden inesperada: {command}")
        outcome = self.outcomes.pop(0)
        if callable(outcome):
            outcome = outcome(self._state)
        if outcome.state is not None:
            self._state = outcome.state
        return outcome


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ScriptedKeys:
    """Teclado guionizado: los menús leen ``keys``; las esperas, ``waiting``.

    Una espera sin tecla guionizada vence sola (y adelanta el reloj, si lo hay).
    Cuando el guion de menús se agota, el jugador pulsa Ctrl+C.
    """

    def __init__(self, keys=(), *, waiting=(), clock=None) -> None:
        self.keys = list(keys)
        self.waiting = list(waiting)
        self.clock = clock
        self.entered = 0
        self.exited = 0
        self.timeouts: list[float] = []

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *exc):
        self.exited += 1

    def read(self, timeout):
        if timeout is not None:
            self.timeouts.append(timeout)
            if self.waiting:
                return self.waiting.pop(0)
            if self.clock is not None:
                self.clock.now += timeout
            return None
        if not self.keys:
            raise KeyboardInterrupt
        return self.keys.pop(0)


class Preparer:
    def __init__(self, result) -> None:
        self.result = result
        self.calls: list = []

    def wild(self, encounter):
        self.calls.append(("wild", encounter))
        return self.result

    def trainer(self):
        self.calls.append(("trainer",))
        return self.result


ENCOUNTER = {"species": "pidgey", "form": "regular", "seen_at": "2026-11-11T08:00:00+00:00"}


class Harness:
    """Servicios del combate con dobles que apuntan cada llamada."""

    def __init__(
        self,
        state=None,
        outcomes=(),
        *,
        view=None,
        setup="setup",
        started=None,
        stdin="",
        keys=None,
        inventory=None,
        pending=(),
        exhibition=None,
        record=None,
    ) -> None:
        self.state = state or battle_state()
        self.outcomes = list(outcomes)
        self.view = view
        self.preparer = Preparer(setup)
        self.started = started
        self.stdin = io.StringIO(stdin)
        self.keys = keys or ScriptedKeys()
        self.inventory = inventory or {"balls": {"superball": 2, "ultraball": 0, "masterball": 0}}
        self.pending = list(pending)
        self.exhibition_result = exhibition
        self.record_value = record or {"wild_battles": 3, "wild_wins": 2, "wild_captures": 1}
        self.session: FakeSession | None = None
        self.calls: list = []
        self.renderer = MagicMock()
        self.renderer.capture_sprite.return_value = None

    def services(self) -> BattleServices:
        return BattleServices(
            describe=self.describe,
            prepare=self.prepare,
            start=self.start,
            persisted=self.persisted,
            exhibition=self.exhibition,
            read_encounter=lambda: self.calls.append(("read_encounter",)) or ENCOUNTER,
            sync_inventory=lambda: self.calls.append(("sync",)) or self.inventory,
            balls=cli.composition.ball_catalog,
            stock=cli.composition.stock_count,
            resolve_ball=cli.composition.resolve_ball,
            sprites=lambda species, form, shiny: None,
            sprite_renderer=lambda: self.renderer,
            species_data=lambda species, form: {"capture_rate": 255, "encounter_level": 5},
            pending_evolutions=lambda: tuple(self.pending),
            evolve=self.evolve,
            prepare_evolution=lambda evolution: self.calls.append(("prepare", evolution)),
            record=lambda: self.calls.append(("record",)) or self.record_value,
            keyboard=lambda: self.keys,
            stdin=self.stdin,
            clock=Clock(),
            menu_poll=None,
        )

    def describe(self):
        self.calls.append(("describe",))
        if callable(self.view):
            return self.view()
        return self.view

    def prepare(self, progress):
        self.calls.append(("prepare_battle", progress))
        return self.preparer

    def start(self, setup):
        self.calls.append(("start", setup))
        if self.started is not None:
            return self.started
        return TurnOutcome(BattleStatus.STARTED, opening(self.state), self.state)

    def persisted(self, view, opening_events):
        self.calls.append(("persisted", view, tuple(opening_events)))
        self.session = FakeSession(view.state, self.outcomes, opening_events=opening_events)
        return self.session

    def exhibition(self, left, right, level, progress):
        self.calls.append(("exhibition", left, right, level))
        return self.exhibition_result

    def evolve(self, ids):
        self.calls.append(("evolve", tuple(ids)))
        return tuple(evolution for evolution in self.pending if evolution.capture_id in ids)

    def called(self, name):
        return [call for call in self.calls if call[0] == name]


def combat_args(**overrides):
    values = {"entrenador": False, "auto": False, "velocidad": 0.0, "debug": False}
    values.update(overrides)
    return argparse.Namespace(**values)


def exhibition_args(**overrides):
    values = {"primero": None, "segundo": None, "nivel": None, "auto": True}
    values.update({"velocidad": 0.0, "debug": False})
    values.update(overrides)
    return argparse.Namespace(**values)


def plain_console(width: int = 140) -> Console:
    return Console(file=io.StringIO(), width=width, color_system=None)


def tty_console(width: int = 100, height: int = 30) -> Console:
    return Console(
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        width=width,
        height=height,
        legacy_windows=False,
    )


def output(console: Console) -> str:
    return console.file.getvalue()


def plain(console: Console) -> str:
    """Salida sin códigos ANSI y con los espacios aplanados (Rich ajusta líneas)."""
    return " ".join(ANSI.sub("", output(console)).split())


# --- Parser y teclas ---------------------------------------------------------------


def test_parser_offers_the_battle_commands_and_their_options():
    parser = cli.build_parser()

    combat = parser.parse_args(["combate", "-e", "--auto", "--velocidad", "0,5", "--debug"])
    assert combat.func is cli.cmd_combate
    assert (combat.entrenador, combat.auto, combat.velocidad, combat.debug) == (
        True,
        True,
        0.5,
        True,
    )
    alias = parser.parse_args(["batalla"])
    assert alias.func is cli.cmd_combate
    assert (alias.entrenador, alias.auto, alias.velocidad, alias.debug) == (
        False,
        False,
        1.0,
        False,
    )

    demo = parser.parse_args(["demo-combate", "4", "106", "-n", "30", "--auto"])
    assert demo.func is cli.cmd_demo_combate
    assert (demo.primero, demo.segundo, demo.nivel, demo.auto) == ("4", "106", 30, True)
    assert parser.parse_args(["demo-combate"]).primero is None

    moves = parser.parse_args(["movimientos", "7", "--set", "pistola agua,placaje"])
    assert moves.func is cli.cmd_movimientos
    assert (moves.pokemon, moves.set, moves.auto) == ("7", "pistola agua,placaje", False)
    assert parser.parse_args(["movimientos", "eevee", "--auto"]).auto is True
    # Las opciones también van delante, como en la línea de uso y como lo
    # completan bash y zsh: --set lleva un solo valor y no se come el ID.
    first = parser.parse_args(["movimientos", "--set", "ascuas,placaje", "7"])
    assert (first.pokemon, first.set) == ("7", "ascuas,placaje")


@pytest.mark.parametrize(
    "arguments",
    [
        ["combate", "--velocidad", "-1"],
        ["combate", "--velocidad", "rápido"],
        ["movimientos", "7", "--set", "ascuas", "--auto"],
    ],
)
def test_parser_rejects_impossible_battle_options(arguments, capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(arguments)
    capsys.readouterr()


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("\x1b[A", "up"),
        ("k", "up"),
        ("\x1b[B", "down"),
        ("J", "down"),
        ("\x1b[D", "left"),
        ("h", "left"),
        ("\x1bOC", "right"),
        ("l", "right"),
        ("\r", "enter"),
        (" ", "enter"),
        ("z", "enter"),
        ("\x1b", "cancel"),
        ("x", "cancel"),
        ("q", "quit"),
        ("\x03", "interrupt"),
        ("3", "3"),
        ("0", "other"),
        ("ñ", "other"),
    ],
)
def test_menu_keys_cover_arrows_vi_console_buttons_and_digits(raw, name):
    assert cli._menu_key_name(raw) == name


def test_menu_key_reader_waits_at_most_the_timeout_and_reassembles_arrows(monkeypatch):
    read_end, write_end = os.pipe()
    stream = os.fdopen(read_end, "r")
    try:
        monkeypatch.setattr(cli.sys, "stdin", stream)
        assert cli._read_menu_key(0.01, raw=False) is None
        os.write(write_end, b"\x1b[C")
        assert cli._read_menu_key(0.5, raw=False) == "right"
        os.write(write_end, b"2")
        assert cli._read_menu_key(None, raw=False) == "2"
    finally:
        stream.close()
        os.close(write_end)


def test_terminal_keyboard_keeps_cbreak_for_the_whole_battle_and_restores_it(monkeypatch):
    calls = []
    monkeypatch.setattr(
        battle_cli.termios, "tcgetattr", lambda fd: calls.append(("get", fd)) or ["saved"]
    )
    monkeypatch.setattr(
        battle_cli.termios,
        "tcsetattr",
        lambda fd, when, attributes: calls.append(("set", fd, attributes)),
    )
    monkeypatch.setattr(battle_cli.tty, "setcbreak", lambda fd: calls.append(("cbreak", fd)))
    stream = MagicMock()
    stream.fileno.return_value = 5
    reads = []

    def read_key(timeout, *, raw):
        reads.append((timeout, raw))
        return "enter"

    keyboard = TerminalKeyboard(read_key, stream)
    with pytest.raises(RuntimeError):
        with keyboard:
            assert keyboard.read(0.25) == "enter"
            raise RuntimeError("fallo a mitad de combate")

    assert calls == [("get", 5), ("cbreak", 5), ("set", 5, ["saved"])]
    assert reads == [(0.25, False)]


# --- Órdenes escritas --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2", Fight(1)),
        ("  1  ", Fight(0)),
        ("c 2", Switch(1)),
        ("cambiar 1", Switch(0)),
        ("b", ThrowBall("pokeball", "Pokeball", 1.0)),
        ("b super", ThrowBall("superball", "Superball", 1.5)),
        ("bola ultra", ThrowBall("ultraball", "Ultraball", 2.0)),
        ("h", Flee()),
        ("huir", Flee()),
        ("a", Auto()),
        ("", Auto()),
        ("q", battle_cli._Signal.PAUSE),
    ],
)
def test_written_orders_become_battle_commands(text, expected):
    assert parse_order(text, battle_state(), cli.composition.resolve_ball) == expected


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("c", "«c 2»"),
        ("b piedra", "piedra"),
        ("lanzallamas", "No entiendo"),
        # Dígitos Unicode que ``int`` no acepta: se explican, no rompen el combate.
        ("²", "No entiendo"),
        ("c ²", "«c 2»"),
    ],
)
def test_unreadable_orders_explain_what_is_expected(text, fragment):
    reason = parse_order(text, battle_state(), cli.composition.resolve_ball)
    assert isinstance(reason, str)
    assert fragment in reason


def test_after_a_faint_a_number_picks_the_replacement():
    state = battle_state(phase=Phase.CHOOSE_REPLACEMENT)
    resolve = cli.composition.resolve_ball
    assert parse_order("2", state, resolve) == Replace(1)
    assert parse_order("c 2", state, resolve) == Replace(1)
    assert "sacar a otro" in parse_order("h", state, resolve)


# --- Modo texto ----------------------------------------------------------------------


def test_plain_battle_narrates_each_event_and_reads_orders_by_line():
    harness = Harness(outcomes=[victory], stdin="1\n")
    console = plain_console()

    code = battle_cli.run_combat(
        combat_args(), console=console, services=harness.services(), interactive=False
    )

    assert code == 0
    assert harness.session.commands == [Fight(0)]
    text = plain(console)
    for line in (
        "¡Un Pidgey salvaje apareció!",
        "¡Adelante, Chamusca!",
        "— Turno 1 —",
        "¡Chamusca usó Ascuas!",
        "¡Es muy eficaz!",
        "¡Pidgey salvaje se debilitó!",
        "¡Chamusca ganó 35 puntos de experiencia!",
        "¿Qué debe hacer Chamusca?",
        "[1] Ascuas 25/25",
        "Combate contra Pidgey salvaje",
        "¡Victoria! Pidgey salvaje se debilitó.",
        "+35 EXP",
        "Combates salvajes: 3 · victorias 2 · capturas 1",
    ):
        assert line in text
    # Sin TTY nada de pantalla alternativa: texto plano para tuberías y logs.
    assert ALT_SCREEN_ON not in output(console)
    assert [call[0] for call in harness.calls[:4]] == [
        "sync",
        "describe",
        "read_encounter",
        "prepare_battle",
    ]
    assert harness.preparer.calls == [("wild", ENCOUNTER)]
    assert harness.called("persisted")[0][2] == opening(harness.state)


def test_plain_battle_hands_over_to_the_ai_when_stdin_runs_out():
    state = battle_state()
    harness = Harness(state, [lambda s: applied(s, ember_hits(s)), victory], stdin="")
    console = plain_console()

    assert (
        battle_cli.run_combat(
            combat_args(), console=console, services=harness.services(), interactive=False
        )
        == 0
    )

    assert harness.session.commands == [Auto(), Auto()]
    assert plain(console).count("(Sin más órdenes: decide la IA.)") == 1


def test_auto_flag_never_prompts():
    harness = Harness(outcomes=[victory])
    console = plain_console()

    battle_cli.run_combat(
        combat_args(auto=True), console=console, services=harness.services(), interactive=False
    )

    assert harness.session.commands == [Auto()]
    assert "¿Qué debe hacer" not in plain(console)


def test_plain_battle_explains_unreadable_and_invalid_orders_and_asks_again():
    invalid = TurnOutcome(
        BattleStatus.INVALID_ACTION, (), None, reason="¡No quedan PP para este movimiento!"
    )
    harness = Harness(outcomes=[invalid, victory], stdin="lanzallamas\n3\n1\n")
    console = plain_console()

    battle_cli.run_combat(
        combat_args(), console=console, services=harness.services(), interactive=False
    )

    assert harness.session.commands == [Fight(2), Fight(0)]
    text = plain(console)
    assert "No entiendo «lanzallamas»" in text
    assert "¡No quedan PP para este movimiento!" in text


def test_q_pauses_a_saved_battle_and_says_how_to_resume():
    harness = Harness(stdin="q\n")
    console = plain_console()

    assert (
        battle_cli.run_combat(
            combat_args(), console=console, services=harness.services(), interactive=False
        )
        == 0
    )

    assert harness.session.commands == []
    assert "Combate en pausa: vuelve con `pokedex combate`." in plain(console)
    assert not harness.called("record")


def test_after_a_faint_the_plain_prompt_asks_for_the_replacement():
    state = battle_state(phase=Phase.CHOOSE_REPLACEMENT)
    fainted = state.player.with_current(replace(state.player.current, hp=0))
    state = replace(state, player=fainted)
    harness = Harness(state, [victory], view=BattleView(state.kind, state, None), stdin="2\n")
    console = plain_console()

    battle_cli.run_combat(
        combat_args(), console=console, services=harness.services(), interactive=False
    )

    assert harness.session.commands == [Replace(1)]
    text = plain(console)
    assert "¿A qué Pokémon vas a sacar?" in text
    assert "[1] Chamusca Nv.15 PS 0/" in text
    assert "(debilitado)" in text


def test_debug_adds_damage_and_capture_numbers_in_plain_text():
    state = battle_state()
    thrown = TurnOutcome(
        BattleStatus.APPLIED,
        (
            TurnStarted(1),
            BallThrown("superball", "Superball", 2, caught=False),
            *ember_hits(state, damage=12),
        ),
        state,
    )
    harness = Harness(state, [thrown, victory], stdin="b super\n1\n")
    console = plain_console()

    battle_cli.run_combat(
        combat_args(debug=True), console=console, services=harness.services(), interactive=False
    )

    text = plain(console)
    assert "Superball: 100.0% de captura (combate ×1.00)" in text
    assert "Pidgey −12 PS" in text
    assert "eficacia ×2" in text


def test_multi_hit_effectiveness_is_said_once_after_the_last_hit():
    state = battle_state()
    foe = state.opponent.current
    double_kick = (
        TurnStarted(1),
        MoveUsed(
            "player",
            "p0",
            "Chamusca",
            "double-kick",
            "Doble Patada",
            "fighting",
            DamageClass.PHYSICAL,
        ),
        Damaged("opponent", "o0", "Pidgey", 3, foe.hp, foe.hp - 3, foe.max_hp, effectiveness=0.5),
        Damaged(
            "opponent", "o0", "Pidgey", 3, foe.hp - 3, foe.hp - 6, foe.max_hp, effectiveness=0.5
        ),
        HitCount("opponent", "o0", "Pidgey", 2),
    )
    harness = Harness(
        state, [TurnOutcome(BattleStatus.APPLIED, double_kick, state), victory], stdin="1\n1\n"
    )
    console = plain_console()

    battle_cli.run_combat(
        combat_args(), console=console, services=harness.services(), interactive=False
    )

    assert plain(console).count("No es muy eficaz") == 1


# --- Arranque del combate ----------------------------------------------------------


def test_a_saved_battle_is_resumed_without_preparing_another():
    state = battle_state(turn=3)
    view = BattleView(state.kind, state, None)
    harness = Harness(state, [victory], view=view, stdin="1\n")
    console = plain_console()

    assert (
        battle_cli.run_combat(
            combat_args(entrenador=True),
            console=console,
            services=harness.services(),
            interactive=False,
        )
        == 0
    )

    assert not harness.called("prepare_battle")
    assert not harness.called("start")
    assert harness.called("persisted") == [("persisted", view, ())]
    assert "Tienes un combate a medias" in plain(console)


@pytest.mark.parametrize(
    ("status", "fragment"),
    [
        (BattleStatus.NO_TEAM, "Tu equipo está vacío"),
        (BattleStatus.NO_ENCOUNTER, "No hay ningún Pokémon a la vista"),
        (BattleStatus.ALREADY_CAPTURED, "Ya capturaste a este Pokémon"),
        (BattleStatus.NO_SPECIES_DATA, "No hay datos de Pidgey"),
        (BattleStatus.NO_TRAINER_POOL, "formar un entrenador"),
    ],
)
def test_a_battle_that_cannot_start_says_why(status, fragment):
    harness = Harness(setup=status)
    console = plain_console()

    assert (
        battle_cli.run_combat(
            combat_args(), console=console, services=harness.services(), interactive=False
        )
        == 1
    )

    assert fragment in plain(console)
    assert not harness.called("start")


def test_trainer_flag_prepares_a_trainer_without_looking_at_the_encounter():
    state = battle_state(BattleKind.TRAINER, trainer="Montañero Fermín")
    harness = Harness(state, [victory], stdin="1\n")
    console = plain_console()

    battle_cli.run_combat(
        combat_args(entrenador=True),
        console=console,
        services=harness.services(),
        interactive=False,
    )

    assert harness.preparer.calls == [("trainer",)]
    assert not harness.called("read_encounter")
    text = plain(console)
    assert "¡Montañero Fermín quiere luchar!" in text
    assert "¡Has derrotado a Montañero Fermín!" in text
    assert "Entrenadores:" in text


def test_a_battle_started_elsewhere_meanwhile_is_resumed():
    state = battle_state()
    harness = Harness(
        state,
        [victory],
        started=TurnOutcome(BattleStatus.BATTLE_IN_PROGRESS, (), state),
        stdin="1\n",
    )
    describes = iter([None, BattleView(state.kind, state, None)])
    harness.view = lambda: next(describes)

    assert (
        battle_cli.run_combat(
            combat_args(), console=plain_console(), services=harness.services(), interactive=False
        )
        == 0
    )

    assert len(harness.called("describe")) == 2
    assert harness.session.commands == [Fight(0)]


def test_an_encounter_that_changed_before_starting_is_reported():
    harness = Harness(started=TurnOutcome(BattleStatus.STALE, (), None, reason="ya no está"))
    console = plain_console()

    assert (
        battle_cli.run_combat(
            combat_args(), console=console, services=harness.services(), interactive=False
        )
        == 1
    )
    assert "se fue antes de empezar" in plain(console)


@pytest.mark.parametrize(
    ("status", "message"),
    [(BattleStatus.STALE, battle_cli.STALE_TEXT), (BattleStatus.NO_BATTLE, battle_cli.GONE_TEXT)],
)
def test_a_battle_that_vanishes_mid_turn_is_reported(status, message):
    harness = Harness(outcomes=[TurnOutcome(status, (), None)], stdin="1\n")
    console = plain_console()

    assert (
        battle_cli.run_combat(
            combat_args(), console=console, services=harness.services(), interactive=False
        )
        == 1
    )
    assert message in plain(console)
    assert not harness.called("record")


# --- Resumen y evoluciones -------------------------------------------------------------


def test_the_summary_lists_experience_levels_learned_moves_and_the_capture():
    state = battle_state()
    events = (
        TurnStarted(1),
        BallThrown("ultraball", "Ultraball", 3, caught=True),
        ExperienceGained("p0", "Chamusca", 120),
        LeveledUp("p0", "Chamusca", 16, (("hp", 3),)),
        MoveLearned("p0", "Chamusca", "Furia Dragón", forgot="Arañazo"),
        BattleEnded(Outcome.CAPTURED),
    )
    caught = TurnOutcome(
        BattleStatus.FINISHED, events, finished(state, Outcome.CAPTURED), capture_id=42
    )
    harness = Harness(state, [caught], stdin="b ultra\n")
    console = plain_console()

    battle_cli.run_combat(
        combat_args(), console=console, services=harness.services(), interactive=False
    )

    text = plain(console)
    assert "¡Ya está! ¡Has capturado a Pidgey!" in text
    assert "¡Has capturado a Pidgey!" in text
    assert "+120 EXP" in text
    assert "Nv. 15 → 16" in text
    assert "aprendió Furia Dragón (olvidó Arañazo)" in text
    assert "Captura #42 · míralo con `pokedex vision 42`" in text


def test_evolutions_queued_by_the_battle_are_animated_after_the_summary(monkeypatch):
    charmander = PendingEvolution(1, "charmander", "regular", "charmeleon", "regular", False, 16)
    unrelated = PendingEvolution(9, "bulbasaur", "regular", "ivysaur", "regular", False, 16)
    harness = Harness(
        outcomes=[lambda state: victory(state, leveled=(1,))],
        pending=[charmander, unrelated],
        stdin="1\n",
    )
    animate = MagicMock()
    monkeypatch.setattr(battle_cli.animation, "play_evolution_animation", animate)

    battle_cli.run_combat(
        combat_args(velocidad=1.4),
        console=plain_console(),
        services=harness.services(),
        interactive=False,
    )

    assert harness.called("prepare") == [("prepare", charmander)]
    assert harness.called("evolve") == [("evolve", (1,))]
    animate.assert_called_once()
    assert animate.call_args.args[1:6] == ("charmander", "regular", "charmeleon", "regular", False)
    assert animate.call_args.kwargs["speed"] == 1.4


def test_a_battle_without_level_ups_evolves_nothing(monkeypatch):
    harness = Harness(outcomes=[victory], pending=[], stdin="1\n")
    animate = MagicMock()
    monkeypatch.setattr(battle_cli.animation, "play_evolution_animation", animate)

    battle_cli.run_combat(
        combat_args(), console=plain_console(), services=harness.services(), interactive=False
    )

    assert not harness.called("evolve")
    animate.assert_not_called()


# --- Exhibición ------------------------------------------------------------------------


def test_an_exhibition_is_played_in_memory_and_saves_nothing():
    session = ExhibitionSession(battle_state(BattleKind.EXHIBITION), rng=random.Random(4))
    harness = Harness(exhibition=session)
    console = plain_console()

    code = battle_cli.run_exhibition(
        exhibition_args(primero="1", segundo="pidgey", nivel=30),
        console=console,
        services=harness.services(),
        interactive=False,
    )

    assert code == 0
    assert session.state.finished
    assert harness.called("exhibition") == [("exhibition", "1", "pidgey", 30)]
    assert not harness.called("sync")
    assert not harness.called("record")
    text = plain(console)
    assert "¡Comienza el combate de exhibición!" in text
    assert "Exhibición: Chamusca contra Pidgey" in text
    assert "no se ha guardado nada" in text


@pytest.mark.parametrize(
    ("status", "fragment"),
    [
        (BattleStatus.NO_TEAM, "tu equipo está vacío"),
        (BattleStatus.NO_TRAINER_POOL, "rival al azar"),
        (BattleStatus.NO_CAPTURE, "ninguna captura con ese id"),
        (BattleStatus.NO_SPECIES_DATA, "No hay datos de esa especie"),
    ],
)
def test_an_exhibition_that_cannot_start_says_why(status, fragment):
    console = plain_console()
    harness = Harness(exhibition=status)

    assert (
        battle_cli.run_exhibition(
            exhibition_args(), console=console, services=harness.services(), interactive=False
        )
        == 1
    )
    assert fragment in plain(console)


def test_quitting_an_exhibition_is_not_a_pause():
    session = FakeSession(battle_state(BattleKind.EXHIBITION))
    console = plain_console()
    harness = Harness(exhibition=session, stdin="q\n")

    assert (
        battle_cli.run_exhibition(
            exhibition_args(auto=False),
            console=console,
            services=harness.services(),
            interactive=False,
        )
        == 0
    )
    assert battle_cli.EXHIBITION_LEFT_TEXT in plain(console)


# --- Pantalla completa -----------------------------------------------------------------


def run_screen(harness: Harness, *, console=None, **options):
    console = console or tty_console()
    code = battle_cli.run_combat(
        combat_args(**options), console=console, services=harness.services(), interactive=True
    )
    return code, console


def test_screen_menus_pick_a_move_with_arrows_and_enter():
    keys = ScriptedKeys(["right", "left", "enter", "right", "enter"])
    harness = Harness(outcomes=[lambda state: applied(state, ember_hits(state))], keys=keys)

    code, console = run_screen(harness)

    # LUCHAR → segundo movimiento (Arañazo); luego el guion se agota: Ctrl+C.
    assert harness.session.commands == [Fight(1)]
    assert code == 0
    raw = output(console)
    assert ALT_SCREEN_ON in raw
    assert raw.rfind(ALT_SCREEN_OFF) > raw.rfind(ALT_SCREEN_ON)
    assert keys.entered == keys.exited == 1
    assert battle_cli.PAUSED_TEXT in plain(console)


def test_screen_digits_choose_menu_entries_and_moves_directly():
    keys = ScriptedKeys(["1", "3"])
    harness = Harness(outcomes=[lambda state: applied(state, ember_hits(state))], keys=keys)

    run_screen(harness)

    assert harness.session.commands == [Fight(2)]


def test_q_in_the_main_menu_pauses_and_x_only_goes_back():
    keys = ScriptedKeys(["1", "cancel", "2", "cancel", "3", "cancel", "quit"])
    harness = Harness(keys=keys)

    code, console = run_screen(harness)

    assert code == 0
    assert harness.session.commands == []
    assert battle_cli.PAUSED_TEXT in plain(console)
    assert keys.keys == []


def test_the_bag_refuses_an_empty_ball_and_throws_another():
    keys = ScriptedKeys(["2", "down", "down", "enter", "up", "up", "enter"])
    harness = Harness(
        outcomes=[lambda state: applied(state, (BallThrown("pokeball", "Pokeball", 1, False),))],
        keys=keys,
        inventory={"balls": {"superball": 1, "ultraball": 0, "masterball": 0}},
    )

    run_screen(harness)

    # La Ultraball (×0) no se lanza; la Pokeball sí.
    assert harness.session.commands == [ThrowBall("pokeball", "Pokeball", 1.0)]


def test_a_thrown_ball_is_taken_out_of_the_bag():
    bag = Bag.build(
        cli.composition.ball_catalog(), {"balls": {"superball": 1}}, cli.composition.stock_count
    )
    superball = cli.composition.ball_catalog()["superball"]
    thrown = ThrowBall("superball", "Superball", 1.5)

    bag.spend(thrown, TurnOutcome(BattleStatus.NO_STOCK, (), None))
    assert bag.count(superball) == 1
    bag.spend(thrown, TurnOutcome(BattleStatus.APPLIED, (), battle_state()))
    assert bag.count(superball) == 0
    assert bag.empty(superball)
    assert bag.count(cli.composition.ball_catalog()["pokeball"]) is None


def test_the_party_menu_switches_and_refuses_the_active_member():
    keys = ScriptedKeys(["3", "enter", "down", "enter"])
    harness = Harness(outcomes=[lambda state: applied(state, ())], keys=keys)

    _, console = run_screen(harness)

    assert harness.session.commands == [Switch(1)]
    assert "¡Chamusca ya está combatiendo!" in plain(console)


def test_after_a_faint_the_party_menu_is_forced_and_cannot_be_dismissed():
    state = battle_state(phase=Phase.CHOOSE_REPLACEMENT)
    state = replace(state, player=state.player.with_current(replace(state.player.current, hp=0)))
    keys = ScriptedKeys(["cancel", "up", "enter", "down", "enter"])
    harness = Harness(state, [victory], view=BattleView(state.kind, state, None), keys=keys)

    run_screen(harness)

    # Esc no cierra el menú; el debilitado no puede salir; el segundo sí.
    assert harness.session.commands == [Replace(1)]


def test_a_charging_pokemon_skips_the_menus():
    state = battle_state()
    charging = replace(state.player.current, volatile=Volatile(charging="solar-beam"))
    state = replace(state, player=state.player.with_current(charging))
    harness = Harness(state, [victory], view=BattleView(state.kind, state, None))

    code, console = run_screen(harness)

    assert code == 0
    assert harness.session.commands == [Fight(0)]
    assert "Combate contra Pidgey salvaje" in plain(console)


def test_running_from_an_exhibition_explains_how_to_leave():
    session = FakeSession(battle_state(BattleKind.EXHIBITION))
    keys = ScriptedKeys(["4", "quit"])
    console = tty_console()

    code = battle_cli.run_exhibition(
        exhibition_args(auto=False),
        console=console,
        services=Harness(exhibition=session, keys=keys).services(),
        interactive=True,
    )

    assert code == 0
    assert session.commands == []
    assert "En una exhibición no se huye" in plain(console)
    assert battle_cli.EXHIBITION_LEFT_TEXT in plain(console)


def test_auto_mode_lets_the_ai_play_until_q_pauses():
    keys = ScriptedKeys(waiting=[None, "quit"])
    harness = Harness(
        outcomes=[lambda state: applied(state, ember_hits(state)) for _ in range(3)], keys=keys
    )

    code, console = run_screen(harness, auto=True)

    assert code == 0
    assert harness.session.commands == [Auto()]
    assert battle_cli.PAUSED_TEXT in plain(console)


def test_a_full_real_battle_plays_on_screen_from_opening_to_summary():
    exhibition = battle_state(BattleKind.EXHIBITION, award_experience=False)
    session = ExhibitionSession(exhibition, rng=random.Random(11))
    console = tty_console()
    harness = Harness(exhibition=session, keys=ScriptedKeys())

    code = battle_cli.run_exhibition(
        exhibition_args(), console=console, services=harness.services(), interactive=True
    )

    assert code == 0
    assert session.state.finished
    text = plain(console)
    assert "¡Comienza el combate de exhibición!" in text
    assert "Exhibición: Chamusca contra Pidgey" in text


def test_the_screen_restores_the_terminal_even_when_a_turn_fails():
    keys = ScriptedKeys(["1", "1"])
    harness = Harness(keys=keys)

    def broken(state):
        raise RuntimeError("fallo inesperado")

    harness.outcomes = [broken]
    console = tty_console()

    with pytest.raises(RuntimeError, match="fallo inesperado"):
        run_screen(harness, console=console)

    raw = output(console)
    assert raw.rfind(ALT_SCREEN_OFF) > raw.rfind(ALT_SCREEN_ON)
    assert SHOW_CURSOR in raw[raw.rfind(ALT_SCREEN_ON) :]
    assert keys.exited == 1


def test_speed_zero_never_waits_between_frames_or_letters():
    keys = ScriptedKeys(["1", "1"])
    harness = Harness(outcomes=[victory], keys=keys)

    run_screen(harness, velocidad=0.0)

    assert all(timeout == 0 for timeout in keys.timeouts)


def test_speed_scales_the_pauses_of_the_script():
    def pauses(speed):
        keys = ScriptedKeys(["1", "1"], clock=Clock())
        harness = Harness(outcomes=[victory], keys=keys)
        run_screen(harness, console=tty_console(60, 22), velocidad=speed)
        return sum(keys.timeouts)

    slow, fast = pauses(2.0), pauses(0.5)
    assert slow > 3 * fast > 0


def test_enter_completes_the_message_being_typed():
    console = tty_console()
    keys = ScriptedKeys(waiting=[None, None, "enter"], clock=Clock())
    screen = ScreenBattle(
        console,
        keys,
        lambda *unused: None,
        options=BattleOptions(speed=1.0),
        bag=Bag((), {}),
        clock=keys.clock,
    )
    screen._state = battle_state()
    seen = []
    screen._refresh = lambda: seen.append(screen._typed(90).plain)
    message = "¡Chamusca usó Ascuas!"

    screen._type(message)

    # Unas pocas letras por fotograma (≤ 30 repintados por segundo) y, con
    # Enter, el resto de golpe.
    assert seen[:3] == ["¡C", "¡Cha", "¡Chamu"]
    assert seen[-1] == message
    assert len(seen) == 4


def test_resizing_the_terminal_builds_a_director_for_the_new_size(monkeypatch):
    console = tty_console(100, 30)
    sizes = []
    real_director = battle_cli.Director

    class SpyDirector(real_director):
        def __init__(self, state, sprites, width, height):
            sizes.append((width, height))
            super().__init__(state, sprites, width, height)

    monkeypatch.setattr(battle_cli, "Director", SpyDirector)

    def shrink(state):
        console.size = (80, 24)
        return applied(state, ember_hits(state))

    harness = Harness(outcomes=[shrink], keys=ScriptedKeys(["1", "1"]))

    run_screen(harness, console=console)

    assert sizes[0] == (100, 24)
    assert sizes[-1][0] == 80
    assert len(sizes) >= 2


def test_debug_shows_the_damage_of_the_turn_on_screen():
    harness = Harness(
        outcomes=[lambda state: applied(state, ember_hits(state, damage=7))],
        keys=ScriptedKeys(["1", "1"]),
    )

    _, console = run_screen(harness, debug=True)

    assert "Pidgey −7 PS" in plain(console)


@pytest.mark.parametrize(
    ("overlay", "dialog"),
    [(None, "text"), (None, "menu"), (None, "moves"), ("party", "prompt"), ("bag", "prompt")],
)
@pytest.mark.parametrize(("width", "height"), [(100, 30), (80, 24), (60, 22), (160, 50)])
def test_every_screen_fits_the_terminal_exactly(overlay, dialog, width, height):
    console = tty_console(width, height)
    bag = Bag.build(
        cli.composition.ball_catalog(), {"balls": {"superball": 3}}, cli.composition.stock_count
    )
    estimator = CatchEstimator(45, False, False, 5, 1.0)
    screen = ScreenBattle(
        console,
        ScriptedKeys(),
        lambda *unused: None,
        options=BattleOptions(speed=0, debug=True),
        bag=bag,
        estimator=estimator,
    )
    screen._state = battle_state()
    screen._ensure_director()
    screen._overlay, screen._dialog = overlay, dialog
    screen._message = screen._prompt = "¡Un Pidgey salvaje apareció! " * 4
    screen._shown = len(screen._message)
    screen._debug = "Pidgey −7 PS (20→13/20) · " * 10

    # Sin recortar ni rellenar: lo que se salga del terminal se notaría aquí.
    segments = console.render(screen.render(), console.options.update(height=height))
    lines = list(Segment.split_lines(segments))

    assert len(lines) == height
    assert all(Segment.get_line_length(line) == width for line in lines)


def test_a_tiny_terminal_asks_for_more_room():
    console = tty_console(40, 12)
    screen = ScreenBattle(
        console, ScriptedKeys(), lambda *unused: None, options=BattleOptions(), bag=Bag((), {})
    )

    lines = console.render_lines(screen.render(), console.options.update(height=12))

    assert "Agranda la terminal" in "".join(segment.text for line in lines for segment in line)


def test_the_field_backdrop_only_shows_through_transparent_cells():
    scene = Text("ab")
    scene.stylize("on #ff0000", 0, 1)
    console = tty_console()

    segments = list(console.render(battle_cli.with_backdrop(scene, BattleKind.WILD)))

    painted = {
        segment.text: segment.style.bgcolor.triplet for segment in segments if segment.text.strip()
    }
    assert tuple(painted["a"]) == (255, 0, 0)
    assert tuple(painted["b"]) != (255, 0, 0)


@pytest.mark.parametrize("kind", list(BattleKind))
def test_the_intro_covers_the_field_and_ends_in_a_flash(kind):
    frames = battle_cli.intro_frames(30, 10, kind)
    console = tty_console()

    def dark_cells(frame):
        return sum(
            len(segment.text)
            for segment in console.render(frame)
            if segment.style
            and segment.style.bgcolor
            and tuple(segment.style.bgcolor.triplet) == (18, 20, 28)
        )

    assert len(frames) == battle_cli.INTRO_STEPS + 1
    assert all(frame.plain.count("\n") == 9 for frame in frames)
    assert all(cell_len(line) == 30 for frame in frames for line in frame.plain.split("\n"))
    covered = [dark_cells(frame) for frame in frames[:-1]]
    assert covered == sorted(covered)
    assert 0 < covered[0] < covered[-1] == 300
    assert dark_cells(frames[-1]) == 0


# --- Integraciones con el resto de la CLI ------------------------------------------------


def hook_result(*, evolutions=(), reminder=None):
    return OpenTerminalResult(MagicMock(), (), tuple(evolutions), False, battle_reminder=reminder)


@pytest.mark.parametrize("evolving", [True, False])
def test_the_hook_reminds_a_paused_battle_and_keeps_its_encounter(monkeypatch, evolving):
    evolution = PendingEvolution(1, "bulbasaur", "regular", "ivysaur", "regular", False, 16)
    use_case = MagicMock()
    use_case.execute.return_value = hook_result(
        evolutions=(evolution,) if evolving else (), reminder="Pidgey"
    )
    monkeypatch.setattr(cli, "_open_terminal_use_case", lambda write: use_case)
    monkeypatch.setattr(cli.animation, "play_evolution_animation", MagicMock())
    monkeypatch.setattr(cli, "_sprite_renderer", MagicMock())
    cleared = MagicMock()
    monkeypatch.setattr(cli.composition, "clear_encounter", cleared)

    with cli.console.capture() as captured:
        assert cli.cmd_hook(argparse.Namespace(generations="1-9")) == 0

    cleared.assert_not_called()
    assert "Combate en pausa contra Pidgey salvaje" in " ".join(captured.get().split())


def test_without_a_battle_the_hook_still_clears_the_encounter_after_evolving(monkeypatch):
    evolution = PendingEvolution(1, "bulbasaur", "regular", "ivysaur", "regular", False, 16)
    use_case = MagicMock()
    use_case.execute.return_value = hook_result(evolutions=(evolution,))
    monkeypatch.setattr(cli, "_open_terminal_use_case", lambda write: use_case)
    monkeypatch.setattr(cli.animation, "play_evolution_animation", MagicMock())
    monkeypatch.setattr(cli, "_sprite_renderer", MagicMock())
    cleared = MagicMock()
    monkeypatch.setattr(cli.composition, "clear_encounter", cleared)

    with cli.console.capture() as captured:
        cli.cmd_hook(argparse.Namespace(generations="1-9"))

    cleared.assert_called_once()
    assert "Combate en pausa" not in captured.get()


def test_the_terminal_hook_does_not_replace_the_rival_of_a_paused_battle(monkeypatch):
    processor = MagicMock()
    processor.pending.return_value = ()
    monkeypatch.setattr(cli, "_process_evolutions_use_case", lambda: processor)
    monkeypatch.setattr(cli, "_sync_training", lambda: (MagicMock(), ()))
    monkeypatch.setattr(cli.composition, "active_wild_battle_name", lambda: "Pidgey")
    wild = MagicMock()
    monkeypatch.setattr(cli.composition, "run_wild_encounter", wild)

    result = cli._open_terminal_use_case(lambda *unused: None).execute("1-9")

    assert result.battle_reminder == "Pidgey"
    assert result.wild_encounter_started is False
    wild.assert_not_called()


def test_ver_marks_the_encounter_that_is_being_fought(monkeypatch, capsys):
    from pokedex_cli.application.encounter import EncounterStatus

    monkeypatch.setattr(
        cli.composition,
        "read_encounter",
        lambda: {"species": "pidgey", "form": "regular", "shiny": False, "captured": False},
    )
    describe = MagicMock()
    describe.execute.return_value = EncounterStatus(captured=True, special=False)
    monkeypatch.setattr(cli.composition, "describe_encounter", lambda: describe)
    renderer = MagicMock()
    renderer.capture_sprite.return_value = None
    monkeypatch.setattr(cli, "_sprite_renderer", lambda: renderer)
    monkeypatch.setattr(cli.composition, "active_wild_battle_name", lambda: "Pidgey")

    assert cli.cmd_ver(argparse.Namespace(debug=False)) == 0

    assert "Pidgey — ya capturado · en combate" in capsys.readouterr().out


@pytest.mark.parametrize("command", [cli.cmd_capturar, cli.cmd_roca, cli.cmd_cebo])
def test_loose_capture_and_safari_orders_point_to_the_battle_bag(monkeypatch, capsys, command):
    monkeypatch.setattr(
        cli.composition,
        "read_encounter",
        lambda: {"species": "pidgey", "form": "regular", "shiny": False, "captured": False},
    )
    monkeypatch.setattr(cli.composition, "active_wild_battle_name", lambda: "Pidgey")
    sync = MagicMock()
    monkeypatch.setattr(cli, "_sync_training", sync)
    safari = MagicMock()
    monkeypatch.setattr(cli.composition, "throw_safari_item", safari)

    assert command(argparse.Namespace(bola="poke", debug=False)) == 1

    assert (
        "Estás combatiendo contra Pidgey: usa la BOLSA en `pokedex combate`."
        in capsys.readouterr().out
    )
    sync.assert_not_called()
    safari.assert_not_called()


def moveset_view(**overrides):
    values = {
        "capture_id": 7,
        "name": "Chamusca",
        "species": "charmander",
        "form": "regular",
        "level": 15,
        "types": ("fire",),
        "custom": False,
        "moves": (EMBER, SCRATCH),
        "learnable": (
            LearnableMove("scratch", 1, SCRATCH),
            LearnableMove("ember", 7, EMBER),
            LearnableMove(
                "transform",
                9,
                Move(
                    "transform",
                    "Transformación",
                    "normal",
                    DamageClass.STATUS,
                    None,
                    None,
                    10,
                    category="unique",
                ),
            ),
            LearnableMove("mystery-move", 12, None),
        ),
        "upcoming": (LearnableMove("dragon-rage", 16, None),),
    }
    values.update(overrides)
    return MovesetView(**values)


def test_vision_heals_an_old_cache_once_and_shows_the_current_moveset(monkeypatch):
    profiles = iter([{"level_moves": None}, {"level_moves": "[]"}])
    species = MagicMock()
    species.execute.side_effect = lambda *args, **kwargs: next(profiles)
    monkeypatch.setattr(cli, "_species_data_use_case", lambda: species)
    manager = MagicMock()
    manager.view.return_value = moveset_view(custom=True)
    monkeypatch.setattr(cli, "_manage_moveset_use_case", lambda: manager)

    view = cli._vision_moveset({"id": 7, "species": "charmander", "form": "regular"})

    assert view.moves == (EMBER, SCRATCH)
    assert species.execute.call_args_list[1].kwargs == {"refresh": True}
    manager.view.assert_called_once_with("7")


@pytest.mark.parametrize("profile", [None, {"level_moves": None}])
def test_vision_skips_the_moveset_without_a_learnset(monkeypatch, profile):
    species = MagicMock()
    species.execute.return_value = profile
    monkeypatch.setattr(cli, "_species_data_use_case", lambda: species)
    manager = MagicMock()
    monkeypatch.setattr(cli, "_manage_moveset_use_case", lambda: manager)

    assert cli._vision_moveset({"id": 7, "species": "charmander", "form": "regular"}) is None
    manager.view.assert_not_called()


def test_a_failing_moveset_never_breaks_the_vision_card(monkeypatch):
    species = MagicMock()
    species.execute.side_effect = RuntimeError("caché rota")
    monkeypatch.setattr(cli, "_species_data_use_case", lambda: species)
    recorded = MagicMock()
    monkeypatch.setattr(cli.composition, "record_failure", recorded)

    assert cli._vision_moveset({"id": 7, "species": "charmander", "form": "regular"}) is None
    assert recorded.call_args.args[0] == "vision moveset"


def test_vision_passes_the_moveset_to_the_card(tmp_path, monkeypatch):
    database = tmp_path / "pokedex.db"
    monkeypatch.setattr(cli.composition.paths, "DB_PATH", database)
    connection = cli.composition.database.connect(database)
    try:
        capture_id = storage.insert_capture(connection, "charmander", "regular", False, "now")
    finally:
        connection.close()
    species = MagicMock()
    species.execute.return_value = None
    monkeypatch.setattr(cli, "_species_data_use_case", lambda: species)
    renderer = MagicMock()
    renderer.capture_sprite.return_value = None
    monkeypatch.setattr(cli, "_sprite_renderer", lambda: renderer)
    monkeypatch.setattr(cli, "_vision_moveset", lambda row: moveset_view(custom=True))
    render = MagicMock()
    monkeypatch.setattr(cli.display, "render_vision_card", render)

    assert cli.cmd_vision(argparse.Namespace(pokemon=str(capture_id), form=None)) == 0

    assert render.call_args.kwargs == {"moves": (EMBER, SCRATCH), "custom_moves": True}


# --- Movimientos -------------------------------------------------------------------------


class Moves:
    def __init__(self, view, result=None) -> None:
        self._view = view
        self.result = result or MovesetResult(MovesetStatus.SAVED, moves=(EMBER, GROWL))
        self.calls: list = []

    def view(self, identifier):
        self.calls.append(("view", identifier))
        return self._view

    def set(self, capture_id, slugs):
        self.calls.append(("set", capture_id, list(slugs)))
        return self.result

    def reset(self, capture_id):
        self.calls.append(("reset", capture_id))
        return self.result


def moves_args(pokemon="7", **overrides):
    values = {"pokemon": pokemon, "set": None, "auto": False}
    values.update(overrides)
    return argparse.Namespace(**values)


def test_the_moves_card_shows_the_moveset_the_learnset_and_what_comes_next():
    console = plain_console(160)

    assert (
        battle_cli.run_moves(moves_args("#7"), console=console, manager=Moves(moveset_view())) == 0
    )

    text = plain(console)
    assert "Chamusca (Charmander) · #7 · Nv. 15" in text
    assert "Moveset automático" in text
    assert "Ascuas" in text and "Fuego" in text and "Especial" in text
    assert "Aprendibles hasta el Nv. 15" in text
    assert "en uso" in text
    assert "aún no funciona en combate" in text
    assert "Mystery Move" in text and "sin datos" in text
    assert "Próximos: Nv. 16 Dragon Rage" in text
    assert "--set mov1,mov2" in text


def test_set_saves_moves_given_by_spanish_name_separated_by_commas():
    console = plain_console()
    manager = Moves(moveset_view())

    code = battle_cli.run_moves(
        moves_args(set="pistola agua, placaje"), console=console, manager=manager
    )

    assert code == 0
    assert ("set", 7, ["pistola agua", "placaje"]) in manager.calls
    assert "Moveset de Chamusca guardado: Ascuas, Gruñido." in plain(console)


def test_set_explains_why_a_moveset_is_rejected():
    console = plain_console()
    failure = MovesetResult(
        MovesetStatus.NOT_LEARNABLE, "Chamusca no puede aprender «surf» a su nivel."
    )

    assert (
        battle_cli.run_moves(
            moves_args(set="surf"), console=console, manager=Moves(moveset_view(), failure)
        )
        == 1
    )
    assert "no puede aprender «surf»" in plain(console)


def test_auto_goes_back_to_the_automatic_moveset():
    console = plain_console()
    manager = Moves(moveset_view(), MovesetResult(MovesetStatus.RESET))

    assert battle_cli.run_moves(moves_args(auto=True), console=console, manager=manager) == 0
    assert ("reset", 7) in manager.calls
    assert "Chamusca vuelve al moveset automático." in plain(console)


def test_the_moves_command_reports_an_unknown_capture():
    console = plain_console()

    assert battle_cli.run_moves(moves_args("mew"), console=console, manager=Moves(None)) == 1
    assert "No tienes ninguna captura que encaje con mew" in plain(console)


def test_cli_battle_commands_delegate_to_the_battle_module(monkeypatch):
    services = object()
    monkeypatch.setattr(cli, "_battle_services", lambda: services)
    monkeypatch.setattr(cli, "_manage_moveset_use_case", lambda: "manager")
    seen = []
    monkeypatch.setattr(
        battle_cli, "run_combat", lambda args, **kwargs: seen.append(("combate", kwargs)) or 3
    )
    monkeypatch.setattr(
        battle_cli, "run_exhibition", lambda args, **kwargs: seen.append(("demo", kwargs)) or 4
    )
    monkeypatch.setattr(
        battle_cli, "run_moves", lambda args, **kwargs: seen.append(("moves", kwargs)) or 5
    )

    assert cli.cmd_combate(argparse.Namespace()) == 3
    assert cli.cmd_demo_combate(argparse.Namespace()) == 4
    assert cli.cmd_movimientos(argparse.Namespace()) == 5
    assert seen[0][1] == {"console": cli.console, "services": services}
    assert seen[2][1] == {"console": cli.console, "manager": "manager"}


def test_default_services_read_keys_through_the_cli_key_reader(monkeypatch):
    monkeypatch.setattr(cli.composition, "sprite_renderer", MagicMock)
    reader = MagicMock(return_value="enter")

    services = battle_cli.default_services(read_key=reader, sync_inventory=dict)

    keyboard = services.keyboard()
    assert isinstance(keyboard, TerminalKeyboard)
    assert keyboard.read(0.1) == "enter"
    reader.assert_called_once_with(0.1, raw=False)
    assert services.sync_inventory() == {}


def test_the_catch_estimate_follows_the_real_capture_rules():
    wild = battle_state().opponent.current
    estimator = CatchEstimator.build({"capture_rate": 45, "encounter_level": 5}, {"catch_stage": 1})
    weakened = replace(wild, hp=1)

    full = estimator.chance(1.0, wild)
    assert estimator.chance(1.0, weakened) > full
    assert estimator.chance(1.5, wild) == pytest.approx(full * 1.5)
    assert estimator.chance(255.0, wild) == 1.0
    assert CatchEstimator.build(None, None) is None


def test_the_report_keeps_who_progressed_in_team_order():
    state = battle_state()
    report = BattleReport.start(FakeSession(state))
    report.record(
        TurnOutcome(
            BattleStatus.APPLIED,
            (ExperienceGained("p1", "Chispa", 10), ExperienceGained("p0", "Chamusca", 20)),
            state,
            leveled=(2,),
        )
    )

    assert report.progressed() == ["p0", "p1"]
    assert report.leveled == {2}


def test_a_menu_repaints_at_the_new_size_while_it_waits_for_a_key(monkeypatch):
    console = tty_console(100, 30)
    sizes = []
    real_director = battle_cli.Director

    class SpyDirector(real_director):
        def __init__(self, state, sprites, width, height):
            sizes.append((width, height))
            super().__init__(state, sprites, width, height)

    monkeypatch.setattr(battle_cli, "Director", SpyDirector)

    class ResizingKeys(ScriptedKeys):
        def read(self, timeout):
            assert timeout == battle_cli.MENU_POLL
            if console.size == (100, 30):
                console.size = (80, 24)  # el jugador agranda/encoge la ventana
                return None
            return "quit"

    screen = ScreenBattle(
        console,
        ResizingKeys(),
        lambda *unused: None,
        options=BattleOptions(speed=0),
        bag=Bag((), {}),
    )
    session = FakeSession(battle_state())

    assert screen.run(session, BattleReport.start(session)) is battle_cli.Ending.PAUSED
    assert sizes == [(100, 24), (80, 18)]


def test_ctrl_c_while_preparing_cancels_the_battle_without_saving_anything():
    harness = Harness()

    def interrupted(encounter):
        raise KeyboardInterrupt

    harness.preparer.wild = interrupted
    console = plain_console()

    assert (
        battle_cli.run_combat(
            combat_args(), console=console, services=harness.services(), interactive=False
        )
        == 1
    )
    assert battle_cli.CANCELLED_TEXT in plain(console)
    assert not harness.called("start")


def test_ctrl_c_while_preparing_an_exhibition_cancels_it():
    harness = Harness()

    def interrupted(left, right, level, progress):
        raise KeyboardInterrupt

    services = replace(harness.services(), exhibition=interrupted)
    console = plain_console()

    assert (
        battle_cli.run_exhibition(
            exhibition_args(), console=console, services=services, interactive=False
        )
        == 1
    )
    assert battle_cli.CANCELLED_TEXT in plain(console)


def test_the_real_keyboard_works_on_a_pseudo_terminal_and_leaves_it_as_it_was(monkeypatch):
    import pty
    import termios
    import threading

    master, slave = pty.openpty()
    stream = os.fdopen(slave, "r")
    try:
        monkeypatch.setattr(cli.sys, "stdin", stream)
        before = termios.tcgetattr(slave)
        keyboard = TerminalKeyboard(cli._read_menu_key, stream)
        with keyboard:
            inside = termios.tcgetattr(slave)
            assert not inside[3] & (termios.ICANON | termios.ECHO)  # sin Enter ni eco
            assert inside[3] & termios.ISIG  # Ctrl+C sigue siendo KeyboardInterrupt
            assert keyboard.read(0.01) is None
            os.write(master, b"\x1b[A")
            assert keyboard.read(5.0) == "up"
            os.write(master, b"z")
            assert keyboard.read(5.0) == "enter"
        assert termios.tcgetattr(slave) == before
        # El selector del equipo lee en crudo solo durante la tecla. Poner la
        # terminal en crudo descarta lo tecleado antes, así que la tecla llega
        # con el lector ya esperando.
        typed = threading.Timer(0.2, os.write, args=(master, b"j"))
        typed.start()
        try:
            assert cli._read_menu_key(5.0) == "down"
        finally:
            typed.join()
        assert termios.tcgetattr(slave) == before
    finally:
        stream.close()
        os.close(master)


# Sprite ANSI diminuto (como el de krabby): rojo arriba, azul abajo.
TINY_SPRITE = "\x1b[38;2;220;40;40m▀▀▀▀\x1b[0m\n\x1b[38;2;40;80;220m▄▄▄▄\x1b[0m"


def test_the_party_shows_a_colour_icon_of_each_member():
    console = tty_console()
    screen = ScreenBattle(
        console,
        ScriptedKeys(),
        lambda species, form, shiny: TINY_SPRITE,
        options=BattleOptions(speed=0),
        bag=Bag((), {}),
    )
    state = battle_state()
    state = replace(
        state, player=state.player.replace_member(1, replace(state.player.team[1], hp=0))
    )

    icons = [screen._icon(member) for member in state.player.team]

    assert all(icon.plain.count("\n") == battle_cli.ICON_ROWS - 1 for icon in icons)
    assert all(cell_len(line) == battle_cli.ICON_COLS for line in icons[0].plain.split("\n"))
    colours = {span.style.color.triplet for span in icons[0].spans if span.style.color}
    assert {(220, 40, 40), (40, 80, 220)} <= {tuple(colour) for colour in colours}
    # El debilitado sale apagado.
    assert any(span.style.dim for span in icons[1].spans)


@pytest.mark.parametrize(
    ("kind", "outcome", "trainer", "expected"),
    [
        (BattleKind.WILD, Outcome.LOST, None, "Tu equipo ya no puede luchar."),
        (BattleKind.TRAINER, Outcome.LOST, "Domadora Celia", "Has perdido contra Domadora Celia."),
        (BattleKind.WILD, Outcome.ESCAPED, None, "Escapaste sin problemas."),
        (BattleKind.WILD, Outcome.OPPONENT_FLED, None, "Pidgey salvaje huyó."),
        (BattleKind.WILD, Outcome.DRAW, None, "Tablas: las dos partes están agotadas."),
        (BattleKind.EXHIBITION, Outcome.WON, None, "¡Gana Chamusca!"),
        (BattleKind.EXHIBITION, Outcome.LOST, None, "Gana Pidgey."),
        (BattleKind.EXHIBITION, Outcome.DRAW, None, "Tablas: los dos están agotados."),
    ],
)
def test_the_summary_headline_tells_how_the_battle_ended(kind, outcome, trainer, expected):
    state = battle_state(kind, trainer=trainer)
    report = BattleReport.start(FakeSession(state))

    assert battle_cli.summary_headline(report, finished(state, outcome)) == expected


def test_the_screen_explains_an_order_that_does_not_count_and_asks_again():
    invalid = TurnOutcome(BattleStatus.NO_STOCK, (), None, reason="No te queda ninguna Superball.")
    keys = ScriptedKeys(["2", "down", "enter", "1", "1"])
    harness = Harness(outcomes=[invalid, victory], keys=keys)

    code, console = run_screen(harness)

    assert code == 0
    assert harness.session.commands == [ThrowBall("superball", "Superball", 1.5), Fight(0)]
    assert "No te queda ninguna Superball." in plain(console)
    assert "Combate contra Pidgey salvaje" in plain(console)


def test_the_screen_leaves_a_battle_whose_rival_is_gone():
    stale = TurnOutcome(BattleStatus.STALE, (), None, reason=battle_cli.STALE_TEXT)
    harness = Harness(outcomes=[stale], keys=ScriptedKeys(["1", "1"]))

    code, console = run_screen(harness)

    assert code == 1
    raw = output(console)
    assert raw.rfind(ALT_SCREEN_OFF) > raw.rfind(ALT_SCREEN_ON)
    assert battle_cli.STALE_TEXT in plain(console)


# --- Revisión: Ctrl+C con el combate ya terminado ------------------------------------------


class CtrlCOnceFinished(ScriptedKeys):
    """Teclado que pulsa Ctrl+C en cuanto el combate ha terminado: durante el
    guion del último turno o la espera final de la pantalla."""

    def __init__(self, harness, keys) -> None:
        super().__init__(keys)
        self.harness = harness

    def read(self, timeout):
        session = self.harness.session
        if timeout is not None and session is not None and session.state.finished:
            raise KeyboardInterrupt
        return super().read(timeout)


def test_ctrl_c_after_the_last_turn_shows_the_summary_instead_of_a_pause(monkeypatch):
    charmander = PendingEvolution(1, "charmander", "regular", "charmeleon", "regular", False, 16)
    harness = Harness(outcomes=[lambda state: victory(state, leveled=(1,))], pending=[charmander])
    harness.keys = CtrlCOnceFinished(harness, ["1", "1"])
    animate = MagicMock()
    monkeypatch.setattr(battle_cli.animation, "play_evolution_animation", animate)

    code, console = run_screen(harness, velocidad=1.0)

    # El turno ya se guardó y el combate ya no existe: no hay nada que reanudar.
    assert code == 0
    text = plain(console)
    assert battle_cli.PAUSED_TEXT not in text
    assert "¡Victoria! Pidgey salvaje se debilitó." in text
    assert harness.called("record")
    assert harness.called("evolve") == [("evolve", (1,))]
    raw = output(console)
    assert raw.rfind(ALT_SCREEN_OFF) > raw.rfind(ALT_SCREEN_ON)


class CtrlCWhenWriting(io.StringIO):
    """Salida en la que el jugador pulsa Ctrl+C al escribirse ``fragment``."""

    def __init__(self, fragment: str) -> None:
        super().__init__()
        self.fragment = fragment

    def write(self, text: str) -> int:
        if self.fragment and self.fragment in text:
            self.fragment = ""
            raise KeyboardInterrupt
        return super().write(text)


def test_ctrl_c_while_narrating_the_last_turn_in_plain_text_shows_the_summary():
    harness = Harness(outcomes=[victory], stdin="1\n")
    console = Console(file=CtrlCWhenWriting("se debilitó"), width=140, color_system=None)

    code = battle_cli.run_combat(
        combat_args(), console=console, services=harness.services(), interactive=False
    )

    assert code == 0
    text = plain(console)
    assert battle_cli.PAUSED_TEXT not in text
    assert "Combate contra Pidgey salvaje" in text


def test_an_empty_set_is_explained_instead_of_ignored():
    console = plain_console()
    failure = MovesetResult(MovesetStatus.WRONG_COUNT, "Elige entre 1 y 4 movimientos.")
    manager = Moves(moveset_view(), failure)

    assert battle_cli.run_moves(moves_args(set=""), console=console, manager=manager) == 1
    assert ("set", 7, []) in manager.calls
    assert "Elige entre 1 y 4 movimientos." in plain(console)


class StrictMoves(Moves):
    """Como ``ManageMoveset``: buscar por un nombre que no se puede normalizar
    («?», o «#» sin número) o por un «número» que ``int`` no lee («²») lanza
    ``ValueError``."""

    def view(self, identifier):
        if identifier.isdigit():
            int(identifier)  # como CollectionQueries.resolve: «²» no es un id
        else:
            normalize_species(identifier)
        return super().view(identifier)


@pytest.mark.parametrize("pokemon", ["?", "#", "¿¡!?", "²"])
def test_a_name_that_cannot_be_looked_up_is_reported_without_a_traceback(pokemon):
    console = plain_console()
    manager = StrictMoves(moveset_view())

    assert battle_cli.run_moves(moves_args(pokemon), console=console, manager=manager) == 1
    assert "No tienes ninguna captura que encaje con" in plain(console)


@pytest.mark.parametrize(
    "failure", [sqlite3.OperationalError("database disk image is malformed"), RuntimeError("roto")]
)
def test_a_failing_battle_lookup_never_stops_the_hook_from_drawing_an_encounter(
    monkeypatch, failure
):
    monkeypatch.setattr(cli.composition, "active_wild_battle_name", REAL_ACTIVE_WILD_BATTLE_NAME)

    def broken():
        raise failure

    monkeypatch.setattr(cli.composition, "describe_battle", broken)
    processor = MagicMock()
    processor.pending.return_value = ()
    monkeypatch.setattr(cli, "_process_evolutions_use_case", lambda: processor)
    monkeypatch.setattr(cli, "_sync_training", lambda: (MagicMock(), ()))
    wild = MagicMock()
    monkeypatch.setattr(cli.composition, "run_wild_encounter", wild)

    with cli.console.capture() as captured:
        assert cli.cmd_hook(argparse.Namespace(generations="1-9")) == 0

    wild.assert_called_once()
    assert "Combate en pausa" not in captured.get()


def test_ctrl_c_while_syncing_before_the_battle_cancels_it_without_a_traceback():
    harness = Harness()

    def interrupted():
        raise KeyboardInterrupt

    services = replace(harness.services(), sync_inventory=interrupted)
    console = plain_console()

    assert (
        battle_cli.run_combat(combat_args(), console=console, services=services, interactive=False)
        == 1
    )
    assert battle_cli.CANCELLED_TEXT in plain(console)
    assert not harness.called("start")


def test_running_from_an_exhibition_in_plain_text_explains_how_to_leave():
    session = ExhibitionSession(battle_state(BattleKind.EXHIBITION), rng=random.Random(3))
    console = plain_console()
    harness = Harness(exhibition=session, stdin="h\nq\n")

    code = battle_cli.run_exhibition(
        exhibition_args(auto=False), console=console, services=harness.services(), interactive=False
    )

    assert code == 0
    text = plain(console)
    # No es un combate contra un entrenador: se explica sin gastar la orden.
    assert "contra un entrenador" not in text
    assert "En una exhibición no se huye: escribe q para dejarla." in text
    assert session.state.turn == 0
