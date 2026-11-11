"""Combate en la TUI: lanzador, guion, menús y paneles, montados con dobles.

Sin BD, sin HOME, sin red y sin krabby: la sesión es un doble que devuelve
``TurnOutcome`` guionizados, los sprites son ANSI sintéticos y todo lo externo
entra por ``BattleServices``.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

textual = pytest.importorskip("textual")

from rich.text import Text  # noqa: E402
from textual import events  # noqa: E402
from textual.app import App  # noqa: E402
from textual.widgets import OptionList, Static  # noqa: E402

from pokedex_cli.application.battle import (  # noqa: E402
    STALE_REASON,
    Auto,
    BattleStatus,
    BattleView,
    Fight,
    Flee,
    Replace,
    Switch,
    ThrowBall,
    TurnOutcome,
)
from pokedex_cli.application.movesets import LearnableMove, MovesetView  # noqa: E402
from pokedex_cli.domain.battle import model as m  # noqa: E402
from pokedex_cli.domain.battle.combatants import build_combatant  # noqa: E402
from pokedex_cli.domain.battle.moves import FALLBACK_BY_TYPE, TACKLE  # noqa: E402
from pokedex_cli.domain.models import Ball  # noqa: E402
from pokedex_cli.domain.progression import experience_for_level  # noqa: E402
from pokedex_cli.presentation.battle_director import Beat  # noqa: E402
from pokedex_cli.presentation.battle_scene import SceneModel, render_scene  # noqa: E402
from pokedex_cli.presentation.tui.battle import (  # noqa: E402
    CHANGED_ELSEWHERE,
    EXHIBITION_LEFT,
    EXHIBITION_NO_BAG,
    NO_TEAM_NOTICE,
    OPEN_FAILED,
    PAUSED_NOTICE,
    TURN_FAILED,
    BagItem,
    BagPanel,
    BattleChoice,
    BattleLauncher,
    BattleScreen,
    BattleServices,
    BeatPlayer,
    LauncherOptions,
    OpenedBattle,
    PartyPanel,
    ResultPanel,
    WildPreview,
    bag_items,
    battle_identity,
    grid_step,
    launcher_options,
    moveset_markup,
    open_battle_session,
    party_balls,
    status_message,
    summarize,
    wild_level,
)

WILD = m.BattleKind.WILD
TRAINER = m.BattleKind.TRAINER
EMBER = FALLBACK_BY_TYPE["fire"]
WATER_GUN = FALLBACK_BY_TYPE["water"]
GUST = FALLBACK_BY_TYPE["flying"]
MAX_HP = 52  # base 50, IV 10, nivel 20 y naturaleza neutra


def member(
    key: str,
    species: str,
    name: str,
    *,
    types: tuple[str, ...] = ("fire",),
    moves: tuple[m.Move, ...] = (EMBER, TACKLE),
    capture_id: int | None = None,
    level: int = 20,
) -> m.Combatant:
    return build_combatant(
        key=key,
        species=species,
        form="regular",
        name=name,
        level=level,
        types=types,
        base_stats=m.Stats(50, 50, 50, 50, 50, 50),
        ivs=m.Stats(10, 10, 10, 10, 10, 10),
        nature="hardy",
        moves=moves,
        capture_id=capture_id,
        experience=experience_for_level("medium", level),
    )


def wild_state(**changes: Any) -> m.BattleState:
    player = m.Side(
        (
            member("p0", "charmander", "Chispa", capture_id=1),
            member(
                "p1",
                "squirtle",
                "Burbuja",
                types=("water",),
                moves=(WATER_GUN, TACKLE),
                capture_id=2,
            ),
        )
    )
    opponent = m.Side(
        (member("o0", "pidgey", "Pidgey", types=("normal", "flying"), moves=(GUST, TACKLE)),)
    )
    return m.BattleState(kind=WILD, player=player, opponent=opponent, **changes)


def trainer_state() -> m.BattleState:
    state = wild_state()
    return replace(state, kind=TRAINER, opponent=replace(state.opponent, trainer="Montañero Bruno"))


def with_hp(state: m.BattleState, side: m.SideId, hp: int) -> m.BattleState:
    team = state.side(side)
    return state.with_side(side, team.with_current(replace(team.current, hp=hp)))


def hit(side: m.SideId, key: str, name: str, before: int, after: int, **extra: Any) -> m.Damaged:
    return m.Damaged(side, key, name, before - after, before, after, MAX_HP, **extra)


def used(side: m.SideId, key: str, name: str, move: m.Move) -> m.MoveUsed:
    return m.MoveUsed(side, key, name, move.slug, move.name, move.type, move.damage_class)


OPENING = (
    m.BattleStarted(WILD, None),
    m.SentOut("opponent", "o0", "Pidgey", "pidgey", "regular", False, 20),
    m.SentOut("player", "p0", "Chispa", "charmander", "regular", False, 20),
)


def first_turn(state: m.BattleState) -> TurnOutcome:
    after = replace(with_hp(with_hp(state, "opponent", 30), "player", 40), turn=state.turn + 1)
    events = (
        m.TurnStarted(after.turn),
        used("player", "p0", "Chispa", EMBER),
        hit("opponent", "o0", "Pidgey", MAX_HP, 30),
        used("opponent", "o0", "Pidgey", GUST),
        hit("player", "p0", "Chispa", MAX_HP, 40),
    )
    return TurnOutcome(BattleStatus.APPLIED, events, after)


def winning_turn(state: m.BattleState) -> TurnOutcome:
    leveled = state.player.with_current(replace(state.player.current, level=21))
    after = replace(
        with_hp(state, "opponent", 0),
        player=leveled,
        turn=state.turn + 1,
        phase=m.Phase.FINISHED,
        outcome=m.Outcome.WON,
    )
    events = (
        m.TurnStarted(after.turn),
        used("player", "p0", "Chispa", EMBER),
        hit("opponent", "o0", "Pidgey", state.opponent.current.hp, 0),
        m.Fainted("opponent", "o0", "Pidgey"),
        m.ExperienceGained("p0", "Chispa", 120),
        m.LeveledUp("p0", "Chispa", 21, (("hp", 2), ("atk", 1))),
        m.MoveLearned("p0", "Chispa", "Furia Dragón"),
        m.BattleEnded(m.Outcome.WON),
    )
    return TurnOutcome(BattleStatus.FINISHED, events, after, leveled=(1,))


def fainting_turn(state: m.BattleState) -> TurnOutcome:
    after = replace(
        with_hp(state, "player", 0), turn=state.turn + 1, phase=m.Phase.CHOOSE_REPLACEMENT
    )
    events = (
        m.TurnStarted(after.turn),
        used("opponent", "o0", "Pidgey", GUST),
        hit("player", "p0", "Chispa", state.player.current.hp, 0),
        m.Fainted("player", "p0", "Chispa"),
        m.ReplacementNeeded("player"),
    )
    return TurnOutcome(BattleStatus.APPLIED, events, after)


def replacement(state: m.BattleState) -> TurnOutcome:
    after = replace(state, player=replace(state.player, active=1), phase=m.Phase.CHOOSE_ACTION)
    events = (m.SentOut("player", "p1", "Burbuja", "squirtle", "regular", False, 20),)
    return TurnOutcome(BattleStatus.APPLIED, events, after)


class FakeSession:
    """Sesión guionizada: cada ``take`` devuelve el siguiente resultado.

    Un resultado puede ser un ``TurnOutcome``, una función del estado actual
    o una excepción que lanzar. ``gate`` retiene el turno hasta que el test
    lo suelte (para ver que la interfaz no se bloquea).
    """

    def __init__(
        self,
        state: m.BattleState,
        script: list[Any] | None = None,
        *,
        opening: tuple[m.BattleEvent, ...] = (),
        gate: threading.Event | None = None,
    ) -> None:
        self._state = state
        self._script = list(script or [])
        self._opening = opening
        self.gate = gate
        self.commands: list[Any] = []
        self.threads: set[str] = set()

    @property
    def state(self) -> m.BattleState:
        return self._state

    @property
    def kind(self) -> m.BattleKind:
        return self._state.kind

    @property
    def trainer(self) -> str | None:
        return self._state.opponent.trainer

    @property
    def opening(self) -> tuple[m.BattleEvent, ...]:
        return self._opening

    def take(self, command: Any) -> TurnOutcome:
        self.commands.append(command)
        self.threads.add(threading.current_thread().name)
        if self.gate is not None:
            assert self.gate.wait(10), "el test no soltó el turno"
        step = self._script.pop(0)
        if isinstance(step, BaseException):
            raise step
        outcome = step(self._state) if callable(step) else step
        if outcome.state is not None:
            self._state = outcome.state
        return outcome


def ansi_sprite(rgb: tuple[int, int, int], cols: int = 8, rows: int = 5) -> str:
    cell = "\x1b[38;2;{0};{1};{2}m\x1b[48;2;{0};{1};{2}m▀\x1b[0m".format(*rgb)
    return "\n".join(cell * cols for _ in range(rows))


SPRITES = {
    "charmander": ansi_sprite((240, 128, 48)),
    "squirtle": ansi_sprite((104, 144, 240)),
    "pidgey": ansi_sprite((170, 120, 70)),
}


class FakeSprites:
    """Hace de krabby: anota desde qué hilo lo llaman."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.threads: set[str] = set()

    def __call__(self, species: str, form: str, shiny: bool) -> str | None:
        self.calls.append(species)
        self.threads.add(threading.current_thread().name)
        return SPRITES.get(species)


BAG = (
    BagItem("pokeball", "Pokeball", 1.0, None),
    BagItem("superball", "Superball", 1.5, 1),
    BagItem("ultraball", "Ultraball", 2.0, 0),
)


def services(
    session: FakeSession | None = None,
    *,
    open_result: OpenedBattle | str | None = None,
    bag: Any = BAG,
    speed: float = 1000.0,
    reports: list[tuple[str, BaseException]] | None = None,
    record: dict[str, int] | None = None,
) -> BattleServices:
    def open_session(choice: BattleChoice, progress: Any) -> OpenedBattle | str:
        progress("Preparando a Chispa…")
        if open_result is not None:
            return open_result
        assert session is not None
        return OpenedBattle(session)

    def load_bag() -> Any:
        if isinstance(bag, BaseException):
            raise bag
        return list(bag)

    def report(context: str, error: BaseException) -> None:
        if reports is not None:
            reports.append((context, error))

    return BattleServices(
        options=lambda: LauncherOptions(trainer=True),
        open_session=open_session,
        bag=load_bag,
        record=lambda: record or {},
        moveset=lambda capture_id: None,
        report=report,
        speed=speed,
    )


class Host(App[None]):
    """App mínima que abre la pantalla de combate y guarda lo que devuelve."""

    def __init__(self, screen: BattleScreen) -> None:
        super().__init__()
        self.battle = screen
        self.results: list[Any] = []

    def on_mount(self) -> None:
        self.push_screen(self.battle, self.results.append)


def battle_host(
    session: FakeSession | None = None,
    *,
    choice: BattleChoice = BattleChoice.WILD,
    sprites: FakeSprites | None = None,
    **options: Any,
) -> Host:
    screen = BattleScreen(
        choice, services=services(session, **options), sprites=sprites or FakeSprites()
    )
    return Host(screen)


async def wait_for(pilot: Any, condition: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("la condición no se cumplió a tiempo")
        await pilot.pause(0.02)


async def press_at_once(pilot: Any, *keys: str) -> None:
    """Encola varias teclas sin esperar entre ellas, como un terminal cuando se
    pulsa rápido o se deja una tecla apretada (``pilot.press`` espera a que la
    app quede ociosa entre tecla y tecla y nunca lo reproduce)."""
    for key in keys:
        pilot.app.post_message(events.Key(key, key if len(key) == 1 else None))
    await pilot.pause(0.3)


def plain(widget: Static) -> str:
    content = widget.content
    return content.plain if isinstance(content, Text) else str(content)


def message(screen: BattleScreen) -> str:
    return plain(screen.query_one("#combate-mensaje", Static))


def menu(screen: BattleScreen) -> str:
    return plain(screen.query_one("#combate-menu", Static))


# --- Reproducción del guion ---------------------------------------------------

SCENE = SceneModel(10, 4)
OTHER = SceneModel(10, 4, shake=1)


def test_beat_player_types_each_message_and_then_waits_its_delay() -> None:
    player = BeatPlayer(typing_delay=0.1)
    player.play([Beat(SCENE, "Hola", 0.5)])

    player.advance(0.25)
    assert player.visible == "Ho"
    assert player.typing and not player.waiting

    player.advance(0.2)
    assert player.visible == "Hola"
    assert player.waiting and player.playing

    player.advance(0.44)
    assert player.playing
    player.advance(0.02)
    assert not player.playing
    assert player.visible == "Hola"


def test_beat_player_speed_divides_every_wait() -> None:
    slow, fast = BeatPlayer(typing_delay=0.1), BeatPlayer(speed=4, typing_delay=0.1)
    beats = [Beat(SCENE, None, 1.0), Beat(OTHER, "Ya", 1.0)]
    slow.play(beats)
    fast.play(beats)

    slow.advance(0.5)
    fast.advance(0.5)
    assert slow.scene is SCENE
    assert fast.scene is OTHER and fast.visible == "Ya"
    fast.advance(0.25)
    assert not fast.playing


def test_beat_player_skip_finishes_the_line_then_its_pause_then_jumps_animations() -> None:
    player = BeatPlayer(typing_delay=0.1)
    frames = [Beat(SceneModel(10, 4, shake=index), None, 1.0) for index in range(5)]
    player.play([Beat(SCENE, "¡Adelante!", 5.0), *frames, Beat(OTHER, "¡Pum!", 5.0)])

    player.skip()  # termina de escribir
    assert player.visible == "¡Adelante!" and player.playing
    player.skip()  # corta la pausa: empieza la animación
    assert player.scene is frames[0].scene
    player.skip()  # salta los fotogramas hasta la frase siguiente
    assert player.scene is OTHER
    assert player.previous == "¡Adelante!"
    assert player.visible == ""
    player.skip()
    player.skip()
    assert not player.playing
    assert player.visible == "¡Pum!"


def test_beat_player_show_keeps_the_previous_line_above() -> None:
    player = BeatPlayer()
    player.play([Beat(SCENE, "¡Pidgey se debilitó!", 0.0)])
    player.finish()
    player.show("¿Qué debería hacer Chispa?")
    assert player.previous == "¡Pidgey se debilitó!"
    assert player.visible == "¿Qué debería hacer Chispa?"
    player.show("¿Qué debería hacer Chispa?")
    assert player.previous == "¡Pidgey se debilitó!"


# --- Lógica pura ----------------------------------------------------------------


def test_status_message_explains_every_refusal_in_spanish() -> None:
    refusals = [
        BattleStatus.NO_TEAM,
        BattleStatus.NO_ENCOUNTER,
        BattleStatus.ALREADY_CAPTURED,
        BattleStatus.NO_SPECIES_DATA,
        BattleStatus.NO_TRAINER_POOL,
        BattleStatus.NO_CAPTURE,
        BattleStatus.BATTLE_IN_PROGRESS,
        BattleStatus.STALE,
        BattleStatus.NO_BATTLE,
    ]
    messages = [status_message(status) for status in refusals]
    assert all(text and text != OPEN_FAILED for text in messages)
    assert len(set(messages)) == len(messages)
    assert status_message(BattleStatus.STALE) == STALE_REASON
    assert status_message(BattleStatus.APPLIED) == OPEN_FAILED


@pytest.mark.parametrize(
    ("data", "level"),
    [
        (None, None),
        ({"encounter_level": 3}, 5),
        ({"encounter_level": 30}, 30),
        ({"encounter_level": 12, "is_legendary": 1}, 50),
        ({"encounter_level": 70, "is_legendary": True}, 70),
        ({"encounter_level": "x"}, 5),
    ],
)
def test_wild_level_matches_the_level_of_the_capture(data: Any, level: int | None) -> None:
    assert wild_level(data) == level


def test_grid_step_moves_inside_the_two_by_two_grid() -> None:
    assert grid_step(0, "right", 4) == 1
    assert grid_step(1, "right", 4) == 1
    assert grid_step(0, "down", 4) == 2
    assert grid_step(3, "up", 4) == 1
    assert grid_step(2, "left", 4) == 2
    assert grid_step(1, "down", 3) == 1  # el hueco 3 no existe
    assert grid_step(0, "down", 0) == 0


def test_battle_identity_tells_apart_another_battle() -> None:
    state = wild_state()
    assert battle_identity(first_turn(state).state) == battle_identity(state)
    other = replace(
        state,
        opponent=m.Side((member("o0", "rattata", "Rattata", types=("normal",)),)),
    )
    assert battle_identity(other) != battle_identity(state)
    assert battle_identity(trainer_state()) != battle_identity(state)


def test_party_balls_mark_fainted_members() -> None:
    state = with_hp(wild_state(), "player", 0)
    assert party_balls(state.player) == "○●"


def test_summarize_tells_the_outcome_exp_levels_moves_and_record() -> None:
    finished = winning_turn(first_turn(wild_state()).state)
    summary = summarize(
        finished.events,
        state=finished.state,
        kind=WILD,
        trainer=None,
        record={"wild_battles": 3, "wild_wins": 2, "wild_captures": 1},
    )
    assert summary.finished and summary.outcome is m.Outcome.WON
    assert summary.title == "¡VICTORIA!"
    assert summary.lines[0] == "¡Has ganado el combate!"
    assert "Chispa: +120 EXP · ¡Nv. 21!" in summary.lines
    assert "Chispa aprendió Furia Dragón." in summary.lines
    assert any("próxima terminal" in line for line in summary.lines)
    assert summary.lines[-1] == "Salvajes: 3 combates · 2 victorias · 1 captura"


def test_summarize_a_capture_names_the_new_capture() -> None:
    state = replace(wild_state(), phase=m.Phase.FINISHED, outcome=m.Outcome.CAPTURED)
    summary = summarize((), state=state, kind=WILD, trainer=None, capture_id=42)
    assert summary.title == "¡CAPTURADO!"
    assert summary.lines == ("¡Pidgey ya está en tu colección (captura #42)!",)


def test_summarize_a_paused_or_stale_battle() -> None:
    paused = summarize((), state=wild_state(), kind=WILD, trainer=None)
    assert not paused.finished and paused.title == "COMBATE EN PAUSA"
    stale = summarize((), state=None, kind=WILD, trainer=None, reason=STALE_REASON)
    assert stale.title == "COMBATE TERMINADO" and stale.lines == (STALE_REASON,)


def test_moveset_markup_lists_type_power_accuracy_pp_and_next_move() -> None:
    view = MovesetView(
        capture_id=1,
        name="Chispa",
        species="charmander",
        form="regular",
        level=20,
        types=("fire",),
        custom=False,
        moves=(EMBER, TACKLE),
        learnable=(),
        upcoming=(LearnableMove("flamethrower", 31, replace(EMBER, name="Lanzallamas")),),
    )
    lines = Text.from_markup("\n".join(moveset_markup(view))).plain.splitlines()
    assert lines[0] == "MOVIMIENTOS automáticos"
    assert lines[1].endswith("Pot Prec  PP")
    assert lines[2].split() == ["Ascuas", "Fuego", "Especial", "40", "100", "25"]
    assert lines[3].split() == ["Placaje", "Normal", "Físico", "40", "100", "35"]
    # Las cifras caen bajo su cabecera y nada parte línea en la ficha.
    assert {len(line) for line in lines[1:4]} == {48}
    assert lines[4] == "Próximo: Lanzallamas al Nv. 31"
    loading = Text.from_markup(moveset_markup(None, loading=True)[0]).plain
    assert loading == "MOVIMIENTOS cargando…"
    assert "Sin datos" in "".join(moveset_markup(None))


# --- Pegamento con composition (dobles del gateway) -----------------------------


class FakeGateway:
    """Hace de ``composition`` para el pegamento del lanzador y la apertura."""

    def __init__(self, **values: Any) -> None:
        self.values = values
        self.calls: list[tuple[Any, ...]] = []

    def describe_battle(self) -> Any:
        return SimpleNamespace(execute=lambda: self.values.get("view"))

    def collection_queries(self) -> Any:
        return SimpleNamespace(team=lambda: list(self.values.get("team", [{"id": 1}])))

    def read_encounter(self) -> Any:
        return self.values.get("encounter")

    def species_data(self) -> Any:
        return SimpleNamespace(execute=lambda species, form: self.values.get("species"))

    def battle_record(self) -> dict[str, int]:
        return self.values.get("record", {})

    def prepare_battle(self, on_progress: Any = None) -> Any:
        self.calls.append(("prepare",))
        setup = self.values.get("setup", "SETUP")
        return SimpleNamespace(wild=lambda encounter: setup, trainer=lambda: setup)

    def start_battle(self) -> Any:
        def execute(setup: Any, *, now: Any) -> TurnOutcome:
            self.calls.append(("start", setup))
            return self.values["started"]

        return SimpleNamespace(execute=execute)

    def persisted_session(self, view: Any = None, *, opening: Any = ()) -> Any:
        self.calls.append(("persisted", tuple(opening)))
        return self.values.get("session")

    def exhibition_session(self, left=None, right=None, level=None, *, on_progress=None) -> Any:
        self.calls.append(("exhibition", left, right, level))
        return self.values.get("exhibition")

    def sync_training(self, *, force_repo_scan: bool = False) -> Any:
        return SimpleNamespace(inventory=self.values["inventory"]), ()

    def ball_catalog(self) -> dict[str, Ball]:
        return {
            "pokeball": Ball("pokeball", "Pokeball", 1.0, None),
            "superball": Ball("superball", "Superball", 1.5, 10),
        }

    def stock_count(self, inventory: dict[str, Any], slug: str) -> int | None:
        return None if slug == "pokeball" else inventory["balls"][slug]


def test_launcher_options_offer_only_what_is_possible_now() -> None:
    view = BattleView(WILD, wild_state(), None)
    resume = launcher_options(FakeGateway(view=view))
    assert [choice for choice, _ in resume.choices()] == [
        BattleChoice.RESUME,
        BattleChoice.EXHIBITION,
    ]
    assert "Pidgey salvaje" in resume.choices()[0][1]

    empty = launcher_options(FakeGateway(team=[]))
    assert empty.choices() == [] and empty.notice == NO_TEAM_NOTICE

    captured = launcher_options(
        FakeGateway(encounter={"species": "pidgey", "form": "regular", "captured": True})
    )
    assert [choice for choice, _ in captured.choices()] == [
        BattleChoice.TRAINER,
        BattleChoice.EXHIBITION,
    ]

    wild = launcher_options(
        FakeGateway(
            encounter={"species": "pidgey", "form": "regular", "shiny": True},
            species={"encounter_level": 12},
            record={"wild_battles": 2, "wild_wins": 1},
        )
    )
    assert wild.wild == WildPreview("Pidgey", 12, shiny=True)
    assert wild.choices()[0] == (BattleChoice.WILD, "Pokémon salvaje: Pidgey Nv. 12 ✨")
    assert wild.record is not None and wild.record.startswith("Salvajes: 2 combates")


def test_open_battle_session_starts_a_wild_battle_with_its_opening() -> None:
    session = FakeSession(wild_state())
    started = TurnOutcome(BattleStatus.STARTED, OPENING, wild_state())
    gateway = FakeGateway(started=started, session=session, encounter={"species": "pidgey"})
    progress: list[str] = []

    opened = open_battle_session(BattleChoice.WILD, progress.append, gateway)

    assert opened == OpenedBattle(session)
    assert gateway.calls == [("prepare",), ("start", "SETUP"), ("persisted", OPENING)]
    assert progress == ["¡Todo listo!"]


def test_open_battle_session_resumes_a_battle_already_in_progress() -> None:
    session = FakeSession(wild_state())
    started = TurnOutcome(BattleStatus.BATTLE_IN_PROGRESS, (), wild_state())
    gateway = FakeGateway(started=started, session=session)

    opened = open_battle_session(BattleChoice.TRAINER, lambda text: None, gateway)

    assert isinstance(opened, OpenedBattle) and opened.session is session
    assert opened.notice == "Ya tenías un combate a medias: lo retomamos."
    assert gateway.calls[-1] == ("persisted", ())


def test_open_battle_session_explains_why_it_cannot_start() -> None:
    def opened(choice: BattleChoice, **values: Any) -> Any:
        return open_battle_session(choice, lambda text: None, FakeGateway(**values))

    assert opened(BattleChoice.WILD, setup=BattleStatus.NO_ENCOUNTER) == status_message(
        BattleStatus.NO_ENCOUNTER
    )
    stale = TurnOutcome(BattleStatus.STALE, (), None, reason=STALE_REASON)
    assert opened(BattleChoice.WILD, started=stale) == STALE_REASON
    assert opened(BattleChoice.RESUME, session=None) == status_message(BattleStatus.NO_BATTLE)
    assert opened(BattleChoice.EXHIBITION, exhibition=BattleStatus.NO_TEAM) == NO_TEAM_NOTICE


def test_open_battle_session_plays_exhibitions_at_level_fifty() -> None:
    exhibition = FakeSession(wild_state())
    gateway = FakeGateway(exhibition=exhibition)
    assert open_battle_session(BattleChoice.EXHIBITION, lambda text: None, gateway) == (
        OpenedBattle(exhibition)
    )
    assert gateway.calls == [("exhibition", None, None, 50)]


def test_bag_items_read_the_stock_of_the_synced_inventory() -> None:
    gateway = FakeGateway(inventory={"balls": {"superball": 3}})
    assert bag_items(gateway) == [
        BagItem("pokeball", "Pokeball", 1.0, None),
        BagItem("superball", "Superball", 1.5, 3),
    ]
    assert BagItem("superball", "Superball", 1.5, 1).spent().stock == 0
    assert BagItem("pokeball", "Pokeball", 1.0, None).spent().available


# --- Pantalla de combate ----------------------------------------------------------


def test_opening_plays_and_then_the_command_menu_waits_for_orders() -> None:
    sprites = FakeSprites()
    session = FakeSession(wild_state(), opening=OPENING)

    async def scenario() -> None:
        host = battle_host(session, sprites=sprites)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            assert "¿Qué debería hacer Chispa?" in message(screen)
            assert "¡Adelante, Chispa!" in message(screen)
            assert "LUCHAR" in menu(screen) and "HUIR" in menu(screen)
            field = screen.query_one("#combate-campo")
            assert "Pokémon salvaje" in str(field.border_title)
            # Krabby (aquí, su doble) va en hilos y una sola vez por sprite.
            assert sorted(sprites.calls) == ["charmander", "pidgey", "squirtle"]
            assert threading.main_thread().name not in sprites.threads

    asyncio.run(scenario())


def test_fight_then_a_move_plays_the_turn_and_returns_to_the_menu() -> None:
    session = FakeSession(wild_state(), [first_turn], opening=OPENING)

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            await pilot.press("enter")  # LUCHAR
            assert screen.mode == "moves"
            assert "Ascuas" in message(screen) and "Placaje" in message(screen)
            assert "PP 25/25" in menu(screen)
            assert "Muy eficaz" not in menu(screen)
            await pilot.press("right")
            assert "PP 35/35" in menu(screen)  # Placaje
            await pilot.press("left", "enter")

            await wait_for(pilot, lambda: screen.mode == "main")
            assert session.commands == [Fight(0)]
            assert threading.main_thread().name not in session.threads
            assert screen.state is not None and screen.state.turn == 1
            assert screen.state.opponent.current.hp == 30
            assert "Turno 1" in str(screen.query_one("#combate-campo").border_subtitle)

    asyncio.run(scenario())


def test_number_keys_choose_commands_and_moves_directly_and_esc_goes_back() -> None:
    session = FakeSession(wild_state(), [first_turn])

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            await pilot.press("1")
            assert screen.mode == "moves"
            await pilot.press("escape")
            assert screen.mode == "main" and host.results == []
            await pilot.press("1", "2")
            await wait_for(pilot, lambda: screen.mode == "main")
            assert session.commands == [Fight(1)]

    asyncio.run(scenario())


def test_a_move_without_pp_is_refused_without_bothering_the_session() -> None:
    state = wild_state()
    tired = state.player.current
    empty = (replace(tired.moves[0], pp=0), tired.moves[1])
    state = state.with_side("player", state.player.with_current(replace(tired, moves=empty)))
    session = FakeSession(state)

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            await pilot.press("1", "1")
            await wait_for(
                pilot, lambda: "¡No quedan PP para este movimiento!" in screen.transcript
            )
            await wait_for(pilot, lambda: screen.mode == "moves")
            assert session.commands == []
            await pilot.press("2")  # Placaje sí tiene PP
            await wait_for(pilot, lambda: screen.mode == "main" and bool(session.commands))
            assert session.commands == [Fight(1)]

    asyncio.run(scenario())


def test_bag_throws_the_chosen_ball_and_keeps_its_stock_up_to_date() -> None:
    thrown = m.BallThrown("superball", "Superball", 1, caught=False)

    def failed_ball(state: m.BattleState) -> TurnOutcome:
        after = replace(with_hp(state, "player", 44), turn=state.turn + 1)
        events = (
            m.TurnStarted(after.turn),
            thrown,
            used("opponent", "o0", "Pidgey", GUST),
            hit("player", "p0", "Chispa", MAX_HP, 44),
        )
        return TurnOutcome(BattleStatus.APPLIED, events, after)

    session = FakeSession(wild_state(), [failed_ball])

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            await pilot.press("2")  # BOLSA
            await wait_for(pilot, lambda: isinstance(host.screen, BagPanel))
            options = host.screen.query_one(OptionList).options
            assert [option.disabled for option in options] == [False, False, True]
            assert "×∞" in options[0].prompt.plain
            assert "sin existencias" in options[2].prompt.plain
            await pilot.press("down", "enter")

            await wait_for(pilot, lambda: screen.mode == "main")
            assert session.commands == [ThrowBall("superball", "Superball", 1.5)]
            narrated = screen.transcript
            assert "¡Lanzaste una Superball!" in narrated
            assert narrated.index("¡Lanzaste una Superball!") < narrated.index(
                "¡Pidgey salvaje usó Tornado!"
            )

            await pilot.press("2")
            await wait_for(pilot, lambda: isinstance(host.screen, BagPanel))
            options = host.screen.query_one(OptionList).options
            assert [option.disabled for option in options] == [False, True, True]
            await pilot.press("escape")
            await wait_for(pilot, lambda: screen.mode == "main")

    asyncio.run(scenario())


def test_bag_opens_while_loading_and_a_refused_ball_explains_why() -> None:
    gate = threading.Event()
    refused = TurnOutcome(
        BattleStatus.INVALID_ACTION,
        (),
        trainer_state(),
        reason="¡No puedes capturar el Pokémon de otro entrenador!",
    )
    session = FakeSession(trainer_state(), [refused])
    loads: list[str] = []

    def slow_bag() -> list[BagItem]:
        loads.append(threading.current_thread().name)
        assert gate.wait(10)
        return list(BAG)

    async def scenario() -> None:
        screen = BattleScreen(
            BattleChoice.TRAINER,
            services=replace(services(session), bag=slow_bag),
            sprites=FakeSprites(),
        )
        host = Host(screen)
        async with host.run_test(size=(110, 36)) as pilot:
            await wait_for(pilot, lambda: screen.mode == "main")
            assert loads == []  # contra un entrenador no se precarga
            await pilot.press("2")
            await wait_for(pilot, lambda: isinstance(host.screen, BagPanel))
            panel = host.screen
            assert "Abriendo la bolsa…" in plain(panel.query_one("#bolsa-nota", Static))
            gate.set()
            await wait_for(pilot, lambda: panel.query_one(OptionList).option_count == 3)
            await pilot.press("enter")
            await wait_for(pilot, lambda: screen.mode == "main")
            assert session.commands == [ThrowBall("pokeball", "Pokeball", 1.0)]
            assert "¡No puedes capturar el Pokémon de otro entrenador!" in message(screen)
            assert threading.main_thread().name not in loads

    try:
        asyncio.run(scenario())
    finally:
        gate.set()


def test_no_stock_shows_the_reason_and_empties_that_ball() -> None:
    no_stock = TurnOutcome(
        BattleStatus.NO_STOCK, (), wild_state(), reason="No te queda ninguna Superball."
    )
    session = FakeSession(wild_state(), [no_stock])

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            await pilot.press("2")
            await wait_for(pilot, lambda: isinstance(host.screen, BagPanel))
            await pilot.press("down", "enter")
            await wait_for(pilot, lambda: screen.mode == "main" and bool(session.commands))
            assert "No te queda ninguna Superball." in message(screen)
            await pilot.press("2")
            await wait_for(pilot, lambda: isinstance(host.screen, BagPanel))
            assert host.screen.query_one(OptionList).options[1].disabled

    asyncio.run(scenario())


def test_party_panel_switches_to_a_healthy_member() -> None:
    def switched(state: m.BattleState) -> TurnOutcome:
        after = replace(state, player=replace(state.player, active=1), turn=1)
        events = (
            m.TurnStarted(1),
            m.Recalled("player", "p0", "Chispa"),
            m.SentOut("player", "p1", "Burbuja", "squirtle", "regular", False, 20),
        )
        return TurnOutcome(BattleStatus.APPLIED, events, after)

    session = FakeSession(wild_state(), [switched])

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            await pilot.press("3")  # POKÉMON
            await wait_for(pilot, lambda: isinstance(host.screen, PartyPanel))
            options = host.screen.query_one(OptionList).options
            assert [option.disabled for option in options] == [True, False]
            assert "en combate" in options[0].prompt.plain
            assert f"{MAX_HP}/{MAX_HP}" in options[1].prompt.plain
            await pilot.press("enter")
            await wait_for(pilot, lambda: screen.mode == "main")
            assert session.commands == [Switch(1)]
            assert "¿Qué debería hacer Burbuja?" in message(screen)

    asyncio.run(scenario())


def test_a_fainted_pokemon_forces_a_replacement_from_the_party() -> None:
    session = FakeSession(wild_state(), [fainting_turn, replacement])

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            await pilot.press("4")  # HUIR (el guion lo responde con un desmayo)
            await wait_for(pilot, lambda: isinstance(host.screen, PartyPanel))
            panel = host.screen
            assert "¿A QUIÉN SACAS?" in str(panel.query_one("#equipo").border_title)
            assert "debilitado" in panel.query_one(OptionList).options[0].prompt.plain
            await pilot.press("escape")  # se puede posponer…
            await wait_for(pilot, lambda: screen.mode == "replace")
            assert "¿A qué Pokémon vas a sacar?" in message(screen)
            await pilot.press("1", "4")  # …pero solo vale POKÉMON
            assert screen.mode == "replace" and session.commands == [Flee()]
            await pilot.press("3")
            await wait_for(pilot, lambda: isinstance(host.screen, PartyPanel))
            await pilot.press("escape")
            await wait_for(pilot, lambda: screen.mode == "replace")
            await pilot.press("enter")
            await wait_for(pilot, lambda: isinstance(host.screen, PartyPanel))
            await pilot.press("enter")
            await wait_for(pilot, lambda: screen.mode == "main")
            assert session.commands == [Flee(), Replace(1)]

    asyncio.run(scenario())


def test_a_charging_move_continues_on_its_own() -> None:
    def charging(state: m.BattleState) -> TurnOutcome:
        current = state.player.current
        busy = replace(current, volatile=replace(current.volatile, charging="solar-beam"))
        after = replace(state, player=state.player.with_current(busy), turn=1)
        events = (
            m.TurnStarted(1),
            m.Charging("player", "p0", "Chispa", "solar-beam", "Rayo Solar"),
        )
        return TurnOutcome(BattleStatus.APPLIED, events, after)

    def released(state: m.BattleState) -> TurnOutcome:
        current = state.player.current
        calm = replace(current, volatile=replace(current.volatile, charging=None))
        return first_turn(replace(state, player=state.player.with_current(calm)))

    session = FakeSession(wild_state(), [charging, released])

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            await pilot.press("1", "1")
            await wait_for(pilot, lambda: len(session.commands) == 2 and screen.mode == "main")
            assert session.commands == [Fight(0), Fight(0)]

    asyncio.run(scenario())


def test_auto_lets_the_ai_choose() -> None:
    session = FakeSession(wild_state(), [first_turn])

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            await pilot.press("a")
            await wait_for(pilot, lambda: screen.mode == "main" and bool(session.commands))
            assert session.commands == [Auto()]

    asyncio.run(scenario())


def test_the_end_shows_the_result_and_escape_returns_the_summary() -> None:
    session = FakeSession(wild_state(), [first_turn, winning_turn], opening=OPENING)
    record = {"wild_battles": 4, "wild_wins": 3, "wild_captures": 1}

    async def scenario() -> None:
        host = battle_host(session, record=record)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            await pilot.press("1", "1")
            await wait_for(pilot, lambda: screen.mode == "main" and len(session.commands) == 1)
            await pilot.press("1", "1")
            await wait_for(pilot, lambda: isinstance(host.screen, ResultPanel))
            panel = host.screen
            assert "¡VICTORIA!" in str(panel.query_one("#resultado").border_title)
            body = plain(panel.query_one("#resultado-texto", Static))
            assert "Chispa: +120 EXP · ¡Nv. 21!" in body
            assert "Salvajes: 4 combates · 3 victorias · 1 captura" in body
            await pilot.press("escape")
            await wait_for(pilot, lambda: bool(host.results))
            summary = host.results[0]
            assert summary.finished and summary.outcome is m.Outcome.WON

    asyncio.run(scenario())


def test_escape_in_the_menu_leaves_the_battle_paused() -> None:
    session = FakeSession(wild_state())

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(110, 36)) as pilot:
            await wait_for(pilot, lambda: host.battle.mode == "main")
            await pilot.press("escape")
            await wait_for(pilot, lambda: bool(host.results))
            assert host.results[0].title == "COMBATE EN PAUSA"
            assert not host.results[0].finished
            assert session.commands == []

    asyncio.run(scenario())


def test_resizing_rebuilds_the_scene_at_the_new_field_size() -> None:
    session = FakeSession(wild_state(), opening=OPENING)

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(120, 40)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            before = (screen.scene.width, screen.scene.height)
            assert before == screen.field_size()

            await pilot.resize_terminal(84, 28)
            await wait_for(
                pilot,
                lambda: (
                    screen.scene is not None
                    and ((screen.scene.width, screen.scene.height) == screen.field_size())
                    and screen.field_size() != before
                ),
            )
            width, height = screen.field_size()
            assert width < before[0] and height < before[1]
            lines = render_scene(screen.scene).plain.split("\n")
            assert len(lines) == height and {len(line) for line in lines} == {width}

    asyncio.run(scenario())


def test_the_screen_stays_responsive_while_the_session_works() -> None:
    gate = threading.Event()
    session = FakeSession(wild_state(), [first_turn], gate=gate)

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            await pilot.press("1", "1")
            await wait_for(pilot, lambda: screen.mode == "busy")
            started = time.monotonic()
            await pilot.press("2", "enter", "escape", "a")
            await pilot.pause(0.4)
            assert time.monotonic() - started < 3
            assert screen.mode == "busy" and host.results == []
            assert screen.busy_ticks > 0  # sus temporizadores siguen vivos
            assert session.commands == [Fight(0)]
            gate.set()
            await wait_for(pilot, lambda: screen.mode == "main")
            assert screen.state is not None and screen.state.turn == 1

    try:
        asyncio.run(scenario())
    finally:
        gate.set()


def test_input_is_blocked_while_playing_and_enter_speeds_it_up() -> None:
    session = FakeSession(wild_state())  # sin apertura: «¡Sigue el combate…!»

    async def scenario() -> None:
        host = battle_host(session, speed=0.05)  # a este ritmo tardaría ~30 s
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "playing")
            await pilot.press("escape", "2", "1", "a")
            await pilot.pause(0.1)
            assert screen.mode == "playing" and host.results == []
            assert session.commands == []
            await pilot.press("enter")  # termina de escribir
            assert "¡Sigue el combate contra Pidgey salvaje!" in message(screen)
            await pilot.press("enter")  # corta la pausa
            await wait_for(pilot, lambda: screen.mode == "main", timeout=2)

    asyncio.run(scenario())


def test_a_failed_opening_explains_why_and_escape_goes_back() -> None:
    reason = status_message(BattleStatus.NO_ENCOUNTER)

    async def scenario() -> None:
        host = battle_host(open_result=reason)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "failed")
            assert reason in message(screen)
            assert reason in plain(screen.query_one("#combate-escena", Static))
            await pilot.press("escape")
            await wait_for(pilot, lambda: bool(host.results))
            assert host.results[0].title == "SIN COMBATE"

    asyncio.run(scenario())


def test_a_broken_turn_is_reported_and_the_menu_comes_back() -> None:
    reports: list[tuple[str, BaseException]] = []
    session = FakeSession(wild_state(), [RuntimeError("database is locked"), first_turn])

    async def scenario() -> None:
        host = battle_host(session, reports=reports)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            await pilot.press("1", "1")
            await wait_for(pilot, lambda: screen.mode == "main" and bool(reports))
            assert TURN_FAILED in message(screen)
            assert "¿Qué debería hacer Chispa?" in message(screen)
            assert reports[0][0] == "TUI battle turn"
            await pilot.press("1", "1")  # se puede reintentar
            await wait_for(pilot, lambda: screen.mode == "main" and len(session.commands) == 2)
            assert session.commands == [Fight(0), Fight(0)]
            assert screen.state is not None and screen.state.turn == 1

    asyncio.run(scenario())


def test_a_stale_battle_ends_with_its_reason() -> None:
    stale = TurnOutcome(BattleStatus.STALE, (), None, reason=STALE_REASON)
    session = FakeSession(wild_state(), [stale])

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(110, 36)) as pilot:
            await wait_for(pilot, lambda: host.battle.mode == "main")
            await pilot.press("4")
            await wait_for(pilot, lambda: isinstance(host.screen, ResultPanel))
            assert STALE_REASON in plain(host.screen.query_one("#resultado-texto", Static))
            await pilot.press("escape")
            await wait_for(pilot, lambda: bool(host.results))
            assert host.results[0].title == "COMBATE TERMINADO"

    asyncio.run(scenario())


def test_a_battle_changed_from_another_terminal_is_not_played_as_this_one() -> None:
    other = replace(
        wild_state(turn=7),
        opponent=m.Side((member("o0", "rattata", "Rattata", types=("normal",)),)),
    )
    session = FakeSession(wild_state(), [TurnOutcome(BattleStatus.APPLIED, (), other)])

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(110, 36)) as pilot:
            await wait_for(pilot, lambda: host.battle.mode == "main")
            await pilot.press("1", "1")
            await wait_for(pilot, lambda: isinstance(host.screen, ResultPanel))
            assert CHANGED_ELSEWHERE in plain(host.screen.query_one("#resultado-texto", Static))
            assert host.battle.state is not None and host.battle.state.turn == 0

    asyncio.run(scenario())


def test_the_launcher_lists_options_and_returns_the_choice() -> None:
    options = LauncherOptions(
        wild=WildPreview("Pidgey", 12), trainer=True, exhibition=True, record="Salvajes: 1"
    )
    launcher = BattleLauncher(replace(services(), options=lambda: options))

    class LauncherHost(App[None]):
        def __init__(self) -> None:
            super().__init__()
            self.results: list[Any] = []

        def on_mount(self) -> None:
            self.push_screen(launcher, self.results.append)

    async def scenario() -> None:
        host = LauncherHost()
        async with host.run_test(size=(100, 30)) as pilot:
            await wait_for(pilot, lambda: launcher.options is not None)
            option_list = launcher.query_one(OptionList)
            labels = [option.prompt for option in option_list.options]
            assert labels == [
                "Pokémon salvaje: Pidgey Nv. 12",
                "Entrenador: un rival a la altura de tu equipo",
                "Exhibición Nv. 50: tu líder contra una especie al azar",
            ]
            assert "Salvajes: 1" in plain(launcher.query_one("#lanzador-nota", Static))
            await pilot.press("down", "enter")
            await wait_for(pilot, lambda: bool(host.results))
            assert host.results == [BattleChoice.TRAINER]

    asyncio.run(scenario())


def test_the_launcher_without_options_only_explains_why() -> None:
    launcher = BattleLauncher(
        replace(services(), options=lambda: LauncherOptions(notice=NO_TEAM_NOTICE))
    )

    class LauncherHost(App[None]):
        def __init__(self) -> None:
            super().__init__()
            self.results: list[Any] = []

        def on_mount(self) -> None:
            self.push_screen(launcher, self.results.append)

    async def scenario() -> None:
        host = LauncherHost()
        async with host.run_test(size=(100, 30)) as pilot:
            await wait_for(pilot, lambda: launcher.options is not None)
            assert launcher.query_one(OptionList).option_count == 0
            assert NO_TEAM_NOTICE in plain(launcher.query_one("#lanzador-nota", Static))
            await pilot.press("enter", "escape")
            await wait_for(pilot, lambda: bool(host.results))
            assert host.results == [None]

    asyncio.run(scenario())


def exhibition_state() -> m.BattleState:
    state = wild_state()
    return replace(
        state,
        kind=m.BattleKind.EXHIBITION,
        player=m.Side((state.player.team[0],)),
        award_experience=False,
    )


def test_an_exhibition_has_no_bag_and_run_leaves_it() -> None:
    loads: list[str] = []
    session = FakeSession(exhibition_state())

    def bag() -> list[BagItem]:
        loads.append("bolsa")
        return list(BAG)

    async def scenario() -> None:
        screen = BattleScreen(
            BattleChoice.EXHIBITION,
            services=replace(services(session), bag=bag),
            sprites=FakeSprites(),
        )
        host = Host(screen)
        async with host.run_test(size=(110, 36)) as pilot:
            await wait_for(pilot, lambda: screen.mode == "main")
            assert "Exhibición" in str(screen.query_one("#combate-campo").border_title)
            assert "Esc salir" in plain(screen.query_one("#combate-pie", Static))
            await pilot.press("2")
            await wait_for(pilot, lambda: EXHIBITION_NO_BAG in screen.transcript)
            await wait_for(pilot, lambda: screen.mode == "main")
            assert not isinstance(host.screen, BagPanel) and loads == []
            await pilot.press("4")
            await wait_for(pilot, lambda: bool(host.results))
            assert EXHIBITION_LEFT in screen.transcript
            assert host.results[0].title == "EXHIBICIÓN" and host.results[0].notice is None
            assert session.commands == []

    asyncio.run(scenario())


def test_only_a_trainer_shows_how_many_pokemon_it_has_left() -> None:
    def title_for(state: m.BattleState, choice: BattleChoice) -> str:
        titles: list[str] = []

        async def scenario() -> None:
            host = battle_host(FakeSession(state), choice=choice)
            async with host.run_test(size=(110, 36)) as pilot:
                await wait_for(pilot, lambda: host.battle.mode == "main")
                titles.append(str(host.battle.query_one("#combate-campo").border_title))

        asyncio.run(scenario())
        return titles[0]

    assert title_for(trainer_state(), BattleChoice.TRAINER) == "◓ Montañero Bruno  ●"
    assert title_for(wild_state(), BattleChoice.WILD) == "◓ Pokémon salvaje"


# --- Teclas encoladas, cancelar mientras se prepara y final ----------------------


def switched_turn(state: m.BattleState) -> TurnOutcome:
    after = replace(state, player=replace(state.player, active=1), turn=state.turn + 1)
    return TurnOutcome(BattleStatus.APPLIED, (m.TurnStarted(after.turn),), after)


@pytest.mark.parametrize(
    ("key", "panel", "command"),
    [
        ("3", PartyPanel, Switch(1)),
        ("2", BagPanel, ThrowBall("pokeball", "Pokeball", 1.0)),
    ],
)
def test_two_queued_enters_on_a_panel_send_one_order_and_keep_the_battle(
    key: str, panel: type, command: Any
) -> None:
    # Un segundo Enter que llega con el panel ya cerrado no debe cerrar
    # también la pantalla de debajo (el combate) ni perder la orden.
    session = FakeSession(wild_state(), [switched_turn, first_turn])

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            await pilot.press(key)
            await wait_for(pilot, lambda: isinstance(host.screen, panel))
            await press_at_once(pilot, "enter", "enter")
            await wait_for(pilot, lambda: screen.mode == "main" and bool(session.commands))
            assert host.screen is screen and host.results == []
            assert session.commands == [command]

    asyncio.run(scenario())


def test_the_party_panel_only_offers_auto_when_a_replacement_is_forced() -> None:
    # «a automático» solo hace algo en el relevo obligatorio: abierto desde el
    # menú no debe anunciarlo.
    session = FakeSession(wild_state())

    async def scenario() -> None:
        host = battle_host(session)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "main")
            await pilot.press("3")
            await wait_for(pilot, lambda: isinstance(host.screen, PartyPanel))
            panel = host.screen
            assert "automático" not in str(panel.query_one("#equipo").border_subtitle)
            await pilot.press("a")
            assert host.screen is panel and session.commands == []

    asyncio.run(scenario())


class SlowGateway(FakeGateway):
    """``composition`` con una preparación (o un arranque) que espera al test."""

    def __init__(self, *, prepared: threading.Event, starting: threading.Event, **values: Any):
        super().__init__(**values)
        self.prepared = prepared
        self.starting = starting

    def prepare_battle(self, on_progress: Any = None) -> Any:
        ready = super().prepare_battle(on_progress)

        def wild(encounter: Any) -> Any:
            on_progress("Preparando a Pidgey salvaje…")  # como PrepareBattle
            assert self.prepared.wait(10), "el test no soltó la preparación"
            return ready.wild(encounter)

        return SimpleNamespace(wild=wild, trainer=ready.trainer)

    def start_battle(self) -> Any:
        start = super().start_battle()

        def execute(setup: Any, *, now: Any) -> TurnOutcome:
            self.calls.append(("starting",))
            assert self.starting.wait(10), "el test no soltó el arranque"
            result: TurnOutcome = start.execute(setup, now=now)
            return result

        return SimpleNamespace(execute=execute)


def cancellable_host(gateway: SlowGateway, done: threading.Event) -> Host:
    def open_session(choice: BattleChoice, progress: Any) -> Any:
        try:
            return open_battle_session(choice, progress, gateway)
        finally:
            done.set()

    screen = BattleScreen(
        BattleChoice.WILD,
        services=replace(services(), open_session=open_session),
        sprites=FakeSprites(),
    )
    return Host(screen)


def test_cancelling_while_preparing_does_not_start_the_battle() -> None:
    # «Esc cancelar» mientras se prepara: el combate no debe quedar empezado
    # (guardado en la BD) a espaldas del jugador.
    prepared, starting, done = threading.Event(), threading.Event(), threading.Event()
    starting.set()
    gateway = SlowGateway(
        prepared=prepared,
        starting=starting,
        encounter={"species": "pidgey"},
        started=TurnOutcome(BattleStatus.STARTED, OPENING, wild_state()),
        session=FakeSession(wild_state()),
    )

    async def scenario() -> None:
        host = cancellable_host(gateway, done)
        async with host.run_test(size=(110, 36)) as pilot:
            await wait_for(pilot, lambda: ("prepare",) in gateway.calls)
            assert host.battle.mode == "loading"
            await pilot.press("escape")
            await wait_for(pilot, lambda: bool(host.results))
            assert host.results[0].title == "SIN COMBATE"
            prepared.set()
            await wait_for(pilot, done.is_set)
            await pilot.pause(0.1)
            assert not any(call[0] in ("starting", "start") for call in gateway.calls)

    try:
        asyncio.run(scenario())
    finally:
        prepared.set()


def test_cancelling_while_the_battle_starts_says_that_it_was_saved() -> None:
    # Si Esc llega cuando el combate ya se está empezando, queda guardado: hay
    # que decirlo (si no, aparece «en combate» en el hook sin saber por qué).
    prepared, starting, done = threading.Event(), threading.Event(), threading.Event()
    prepared.set()
    gateway = SlowGateway(
        prepared=prepared,
        starting=starting,
        encounter={"species": "pidgey"},
        started=TurnOutcome(BattleStatus.STARTED, OPENING, wild_state()),
        session=FakeSession(wild_state()),
    )
    notices: list[str] = []

    async def scenario() -> None:
        host = cancellable_host(gateway, done)
        host.notify = lambda message, **options: notices.append(message)  # type: ignore[method-assign]
        async with host.run_test(size=(110, 36)) as pilot:
            await wait_for(pilot, lambda: ("starting",) in gateway.calls)
            await pilot.press("escape")
            await wait_for(pilot, lambda: bool(host.results))
            starting.set()
            await wait_for(pilot, lambda: done.is_set() and bool(notices))
            assert any(call[0] == "start" for call in gateway.calls)
            assert notices == [PAUSED_NOTICE]

    try:
        asyncio.run(scenario())
    finally:
        starting.set()


def test_mashing_enter_through_the_last_turn_keeps_the_result_on_screen() -> None:
    # Enter acelera el guion; las pulsaciones de más no deben cerrar el
    # resultado antes de que se pueda leer (se vuelve con Esc).
    session = FakeSession(wild_state(), [winning_turn])

    async def scenario() -> None:
        host = battle_host(session, speed=0.05)
        async with host.run_test(size=(110, 36)) as pilot:
            screen = host.battle
            await wait_for(pilot, lambda: screen.mode == "playing")
            await pilot.press("enter", "enter")  # «¡Sigue el combate…!»
            await wait_for(pilot, lambda: screen.mode == "main")
            await pilot.press("1", "1")
            for _ in range(40):
                await pilot.press("enter")
            assert isinstance(host.screen, ResultPanel) and host.results == []
            await pilot.press("escape")
            await wait_for(pilot, lambda: bool(host.results))
            assert host.results[0].outcome is m.Outcome.WON

    asyncio.run(scenario())
