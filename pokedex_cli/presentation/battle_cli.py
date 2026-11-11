"""Combate por turnos en la CLI: pantalla completa, texto plano y comandos.

Con terminal interactiva el combate ocupa la pantalla entera (``rich.live.Live``
en la pantalla alternativa): arriba la escena del director sobre un fondo de
campo, abajo la caja de texto que se escribe a máquina y los menús de Gen 3
(LUCHAR, BOLSA, POKÉMON, HUIR), que se manejan con teclas sueltas, sin Enter.
Sin terminal cada evento sale como texto plano y las órdenes llegan por líneas
de stdin; si stdin se agota, o con ``--auto``, decide la IA. Así un combate se
juega igual por una tubería y los tests lo recorren de punta a punta.

El combate guardado se escribe en la BD turno a turno, así que salir (Q en el
menú principal o Ctrl+C en cualquier momento) es dejarlo en pausa. La terminal
se restaura siempre, también ante una excepción.

Todo lo de fuera (BD, krabby, teclado, reloj) llega en ``BattleServices``: los
tests pasan dobles y ``default_services`` lo enlaza con ``composition``.
"""

from __future__ import annotations

import math
import sys
import termios
import textwrap
import time
import tty
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import lru_cache
from typing import Any, Protocol, TextIO

from rich import box
from rich.align import Align
from rich.color import Color
from rich.console import Console, Group, JustifyMethod, RenderableType
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from pokedex_cli.application.battle import (
    LEGENDARY_LEVEL,
    Auto,
    BattleCommand,
    BattleSession,
    BattleSetup,
    BattleStatus,
    BattleView,
    Fight,
    Flee,
    Replace,
    Switch,
    ThrowBall,
    TurnOutcome,
)
from pokedex_cli.application.evolutions import PendingEvolution
from pokedex_cli.application.movesets import LearnableMove, MovesetResult, MovesetView
from pokedex_cli.domain.battle import engine
from pokedex_cli.domain.battle.mechanics import battle_catch_multiplier
from pokedex_cli.domain.battle.model import (
    BallThrown,
    BattleEvent,
    BattleKind,
    BattleState,
    Combatant,
    DamageCause,
    Damaged,
    ExperienceGained,
    LeveledUp,
    Move,
    MoveLearned,
    Outcome,
    Phase,
    SwitchTo,
    TurnStarted,
    UseMove,
)
from pokedex_cli.domain.capture import catch_chance
from pokedex_cli.domain.identity import normalize_species
from pokedex_cli.domain.models import Ball
from pokedex_cli.domain.progression import STARTING_LEVEL
from pokedex_cli.domain.safari import catch_stage_multiplier
from pokedex_cli.presentation import animation
from pokedex_cli.presentation.battle_director import Director
from pokedex_cli.presentation.battle_scene import (
    RGB,
    TYPE_RGB,
    Grid,
    SceneModel,
    SpriteLookup,
    hp_bar,
    menu_text,
    moves_panel,
    prepare_sprite,
    render_scene,
)
from pokedex_cli.presentation.battle_text import (
    DAMAGE_CLASS_NAMES_ES,
    STATUS_BADGES,
    display_label,
    messages_for,
    type_name,
)
from pokedex_cli.presentation.display import BALL_LABELS, display_name

# Menú principal en el orden de Gen 3: LUCHAR y BOLSA arriba, POKÉMON y HUIR abajo.
MAIN_MENU: tuple[str, ...] = ("LUCHAR", "BOLSA", "POKÉMON", "HUIR")
FIGHT, BAG, PARTY, RUN = range(4)

# Ventana de juego centrada. Tiene tope porque los sprites nunca se amplían: en
# una terminal enorme quedarían diminutos en un campo inmenso.
MAX_WINDOW_WIDTH = 112
MAX_SCENE_HEIGHT = 32
MIN_WINDOW_WIDTH = 60
MIN_SCENE_HEIGHT = 14
SCENE_ASPECT = 0.36  # alto máximo de la escena respecto a su ancho (proporción GBA)
DIALOG_HEIGHT = 5  # caja de texto: marco y tres líneas
MENU_WIDTH = 30
ICON_ROWS, ICON_COLS = 3, 8

# Ritmos a velocidad ×1; ``--velocidad`` los multiplica (0 = instantáneo).
TYPE_DELAY = 0.02  # por letra escrita a máquina
FRAME_BUDGET = 1 / 30  # el texto a máquina no repinta más deprisa que esto
NOTICE_DELAY = 1.2  # aviso de una orden que no vale
MIN_NOTICE_DELAY = 0.6  # un aviso se tiene que poder leer, aunque la velocidad sea 0
FINALE_DELAY = 3.0  # última frase antes de volver a la terminal
INTRO_STEPS = 10
INTRO_DELAY = 0.035
# Un menú espera teclas en tandas de este tiempo: entre una y otra mira si la
# terminal cambió de tamaño (SIGWINCH no despierta a ``select``) y repinta.
MENU_POLL = 0.25

_MESSAGE_STYLE = Style(color="#f6f2e6", bgcolor="#26303d")
_MESSAGE_BORDER = Style(color="#d9a640", bgcolor="#26303d")
_PAPER_STYLE = Style(color="#2b2e2a", bgcolor="#f7f3e6")
_FRAME_STYLE = Style(color="#55675c", bgcolor="#f7f3e6")
_SELECTED_STYLE = Style(bgcolor="#f1dc98")
_CURSOR_STYLE = Style(color="#d84030", bold=True)
_FOOTER_STYLE = Style(color="grey58")
_DEBUG_STYLE = Style(color="grey58", italic=True)
_WAIT_STYLE = Style(color="#d9a640", bold=True, blink=True)
_INTRO_DARK: RGB = (18, 20, 28)
_FLASH: RGB = (255, 255, 248)

# Fondo del campo: cielo, horizonte y suelo, según el tipo de combate.
_BACKDROPS: dict[BattleKind, tuple[RGB, RGB, RGB]] = {
    BattleKind.WILD: ((198, 226, 238), (236, 242, 222), (178, 210, 136)),
    BattleKind.TRAINER: ((236, 222, 200), (242, 234, 214), (212, 188, 142)),
    BattleKind.EXHIBITION: ((208, 216, 236), (230, 232, 240), (174, 182, 206)),
}

_DAMAGE_CAUSES: dict[DamageCause, str] = {
    DamageCause.RECOIL: "retroceso",
    DamageCause.CONFUSION: "confusión",
    DamageCause.BURN: "quemadura",
    DamageCause.POISON: "veneno",
    DamageCause.LEECH_SEED: "drenadoras",
    DamageCause.TRAP: "atrapado",
    DamageCause.SACRIFICE: "sacrificio",
}

PROMPT_HELP = "1-4 movimiento · c N cambiar · b [bola] · h huir · a IA · q pausa"
_SWITCH_WORDS = frozenset({"c", "cambiar", "cambia", "cambio"})
_BALL_WORDS = frozenset({"b", "bola", "pokeball", "pokéball"})
_RUN_WORDS = frozenset({"h", "huir", "huye"})
_AUTO_WORDS = frozenset({"a", "auto", "ia"})
_PAUSE_WORDS = frozenset({"q", "salir", "pausa"})

PAUSED_TEXT = "Combate en pausa: vuelve con `pokedex combate`."
EXHIBITION_LEFT_TEXT = "Exhibición interrumpida: no se guardaba nada."
GONE_TEXT = "Ese combate ya no existe (¿lo terminaste en otra terminal?)."
STALE_TEXT = "El Pokémon salvaje de ese combate ya no está."
CANCELLED_TEXT = "Combate cancelado antes de empezar: no se ha guardado nada."
TOO_SMALL_TEXT = "Agranda la terminal para ver el combate (mínimo 60×22).\nQ: pausa"

Progress = Callable[[str], None]
# ``cli._read_menu_key``: lee una tecla con espera máxima (None la agota).
KeyReader = Callable[..., "str | None"]


class Ending(Enum):
    FINISHED = "finished"
    PAUSED = "paused"
    STALE = "stale"  # el encuentro del combate salvaje ya no es el actual
    GONE = "gone"  # el combate desapareció (se terminó en otra terminal)


class _Signal(Enum):
    PAUSE = "pause"
    EOF = "eof"
    BACK = "back"


@dataclass(frozen=True)
class BattleOptions:
    auto: bool = False
    speed: float = 1.0
    debug: bool = False

    @classmethod
    def from_args(cls, args: Any) -> BattleOptions:
        speed = getattr(args, "velocidad", 1.0)
        return cls(
            auto=bool(getattr(args, "auto", False)),
            speed=max(0.0, float(1.0 if speed is None else speed)),
            debug=bool(getattr(args, "debug", False)),
        )


# --- Puertos -------------------------------------------------------------------


class Keyboard(Protocol):
    """Teclado de la pantalla de combate (gestor de contexto + lectura)."""

    def __enter__(self) -> Keyboard: ...

    def __exit__(self, *exc: object) -> None: ...

    def read(self, timeout: float | None) -> str | None:
        """Siguiente tecla normalizada; ``None`` si vence ``timeout``."""
        ...


class BattlePreparer(Protocol):
    def wild(self, encounter: Mapping[str, Any] | None) -> BattleSetup | BattleStatus: ...

    def trainer(self) -> BattleSetup | BattleStatus: ...


class MovesetManager(Protocol):
    def view(self, identifier: str) -> MovesetView | None: ...

    def set(self, capture_id: int, slugs: Sequence[str]) -> MovesetResult: ...

    def reset(self, capture_id: int) -> MovesetResult: ...


@dataclass(frozen=True)
class BattleServices:
    """Todo lo que el combate de la CLI toma de fuera; los tests pasan dobles."""

    describe: Callable[[], BattleView | None]
    prepare: Callable[[Progress | None], BattlePreparer]
    start: Callable[[BattleSetup], TurnOutcome]
    persisted: Callable[[BattleView | None, Sequence[BattleEvent]], BattleSession | None]
    exhibition: Callable[
        [str | None, str | None, int | None, Progress | None], BattleSession | BattleStatus
    ]
    read_encounter: Callable[[], Mapping[str, Any] | None]
    # Sincroniza recompensas y entrenamiento (y los cuenta); devuelve el inventario.
    sync_inventory: Callable[[], Mapping[str, Any] | None]
    balls: Callable[[], Mapping[str, Ball]]
    stock: Callable[[Any, str], int | None]
    resolve_ball: Callable[[str], Ball | None]
    sprites: SpriteLookup
    sprite_renderer: Callable[[], animation.SpriteRenderer]
    species_data: Callable[[str, str], Mapping[str, Any] | None]
    pending_evolutions: Callable[[], Sequence[PendingEvolution]]
    evolve: Callable[[Sequence[int]], Sequence[PendingEvolution]]
    prepare_evolution: Callable[[PendingEvolution], object]
    record: Callable[[], Mapping[str, Any]]
    keyboard: Callable[[], Keyboard]
    stdin: TextIO
    clock: Callable[[], float] = time.monotonic
    # Espera máxima de cada lectura de un menú; None la deja bloquear (teclados
    # guionizados, que no cambian de tamaño).
    menu_poll: float | None = MENU_POLL


class TerminalKeyboard:
    """Teclado real: modo cbreak (sin eco ni Enter) mientras dura el combate.

    Ctrl+C sigue llegando como ``KeyboardInterrupt``. Al salir se restaura el
    modo anterior de la terminal pase lo que pase.
    """

    def __init__(self, read_key: KeyReader, stream: TextIO | None = None) -> None:
        self._read_key = read_key
        self._stream = stream if stream is not None else sys.stdin
        self._saved: list[Any] | None = None

    def __enter__(self) -> TerminalKeyboard:
        fd = self._stream.fileno()
        self._saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._saved is not None:
            termios.tcsetattr(self._stream.fileno(), termios.TCSADRAIN, self._saved)
            self._saved = None

    def read(self, timeout: float | None) -> str | None:
        return self._read_key(timeout, raw=False)


def cached_sprites(capture: Callable[[str, str, bool], str | None]) -> SpriteLookup:
    """Sprites de krabby recordados: cada uno se pide una sola vez por combate."""
    cache: dict[tuple[str, str, bool], str | None] = {}

    def lookup(species: str, form: str, shiny: bool) -> str | None:
        key = (species, form, shiny)
        if key not in cache:
            try:
                cache[key] = capture(species, form, shiny)
            except Exception:  # el sprite es decorativo: sin él sale el sustituto
                cache[key] = None
        return cache[key]

    return lookup


def default_services(
    *, read_key: KeyReader, sync_inventory: Callable[[], Mapping[str, Any] | None]
) -> BattleServices:
    """Servicios reales, enlazados con ``composition`` (BD, krabby y PokeAPI)."""
    from pokedex_cli import composition

    renderer = composition.sprite_renderer()
    return BattleServices(
        describe=lambda: composition.describe_battle().execute(),
        prepare=composition.prepare_battle,
        start=lambda setup: composition.start_battle().execute(
            setup, now=datetime.now(timezone.utc)
        ),
        persisted=lambda view, opening: composition.persisted_session(view, opening=opening),
        exhibition=lambda left, right, level, progress: composition.exhibition_session(
            left, right, level, on_progress=progress
        ),
        read_encounter=composition.read_encounter,
        sync_inventory=sync_inventory,
        balls=composition.ball_catalog,
        stock=composition.stock_count,
        resolve_ball=composition.resolve_ball,
        sprites=cached_sprites(renderer.capture_sprite),
        sprite_renderer=lambda: renderer,
        species_data=lambda species, form: composition.species_data().execute(species, form),
        pending_evolutions=lambda: composition.process_evolutions().pending(),
        evolve=lambda ids: composition.process_evolutions().execute(ids),
        prepare_evolution=lambda evolution: composition.species_data().execute(
            evolution.target_species, evolution.target_form
        ),
        record=composition.battle_record,
        keyboard=lambda: TerminalKeyboard(read_key),
        stdin=sys.stdin,
    )


# --- Comandos ------------------------------------------------------------------


def run_combat(
    args: Any,
    *,
    console: Console,
    services: BattleServices,
    interactive: bool | None = None,
) -> int:
    """``pokedex combate``: reanuda el combate guardado o empieza uno nuevo."""
    options = BattleOptions.from_args(args)
    interactive = _is_interactive(console, services) if interactive is None else interactive
    trainer = bool(getattr(args, "entrenador", False))
    try:
        inventory = services.sync_inventory()
        session = _open_saved_session(console, services, trainer=trainer, interactive=interactive)
    except KeyboardInterrupt:
        # Sincronizar y preparar pueden tardar (repos, descargas): nada del
        # combate se guarda hasta empezarlo.
        console.print(CANCELLED_TEXT, highlight=False)
        return 1
    if isinstance(session, int):
        return session
    bag = Bag.build(services.balls(), inventory, services.stock)
    return _play(
        session,
        console=console,
        services=services,
        options=options,
        bag=bag,
        interactive=interactive,
        saved=True,
    )


def run_exhibition(
    args: Any,
    *,
    console: Console,
    services: BattleServices,
    interactive: bool | None = None,
) -> int:
    """``pokedex demo-combate``: exhibición en memoria, sin guardar nada."""
    options = BattleOptions.from_args(args)
    interactive = _is_interactive(console, services) if interactive is None else interactive
    left = getattr(args, "primero", None)
    right = getattr(args, "segundo", None)
    level = getattr(args, "nivel", None)
    try:
        session = _with_progress(
            console,
            interactive,
            lambda progress: services.exhibition(left, right, level, progress),
        )
    except KeyboardInterrupt:
        console.print(CANCELLED_TEXT, highlight=False)
        return 1
    if isinstance(session, BattleStatus):
        console.print(_exhibition_message(session), highlight=False)
        return 1
    return _play(
        session,
        console=console,
        services=services,
        options=options,
        bag=Bag.build(services.balls(), None, services.stock),
        interactive=interactive,
        saved=False,
    )


def run_moves(args: Any, *, console: Console, manager: MovesetManager) -> int:
    """``pokedex movimientos``: ver, fijar (``--set``) o quitar (``--auto``) el moveset."""
    identifier = str(args.pokemon).strip().lstrip("#")
    view = manager.view(identifier) if _searchable(identifier) else None
    if view is None:
        console.print(
            f"No tienes ninguna captura que encaje con [bold]{escape(str(args.pokemon))}[/]. "
            "Mira `pokedex list` para ver tus capturas.",
            highlight=False,
        )
        return 1
    if getattr(args, "auto", False):
        result = manager.reset(view.capture_id)
        if not result.ok:
            console.print(f"[red]{escape(result.reason or 'No se pudo cambiar.')}[/]")
            return 1
        console.print(f"{escape(view.name)} vuelve al moveset automático.", highlight=False)
        view = manager.view(str(view.capture_id)) or view
    elif getattr(args, "set", None) is not None:
        tokens = [token.strip() for token in str(args.set).split(",") if token.strip()]
        result = manager.set(view.capture_id, tokens)
        if not result.ok:
            console.print(f"[red]{escape(result.reason or 'No se pudo cambiar.')}[/]")
            return 1
        chosen = ", ".join(move.name for move in result.moves)
        console.print(
            f"Moveset de {escape(view.name)} guardado: [bold]{escape(chosen)}[/].",
            highlight=False,
        )
        view = manager.view(str(view.capture_id)) or view
    render_moveset(console, view)
    return 0


def _searchable(identifier: str) -> bool:
    """¿Se puede buscar la captura? Un id o un nombre que no quede vacío al
    normalizarlo («?» o «#» solos no encajan con ninguna)."""
    if identifier.isdecimal():
        return True
    if identifier.isdigit():
        return False  # «²» pasa por número, pero ``int`` no lo lee: no es un id
    try:
        normalize_species(identifier)
    except ValueError:
        return False
    return True


def _open_saved_session(
    console: Console, services: BattleServices, *, trainer: bool, interactive: bool
) -> BattleSession | int:
    """El combate guardado o uno recién empezado; si no se puede, el código de
    salida tras explicar por qué."""
    view = services.describe()
    opening: Sequence[BattleEvent] = ()
    if view is not None:
        if trainer:
            console.print(
                "[dim]Tienes un combate a medias: primero se termina ese.[/]", highlight=False
            )
    else:
        encounter = None if trainer else services.read_encounter()

        def prepare(progress: Progress | None) -> BattleSetup | BattleStatus:
            preparer = services.prepare(progress)
            return preparer.trainer() if trainer else preparer.wild(encounter)

        setup = _with_progress(console, interactive, prepare)
        if isinstance(setup, BattleStatus):
            console.print(_setup_message(setup, encounter), highlight=False)
            return 1
        started = services.start(setup)
        if started.status is BattleStatus.STARTED and started.state is not None:
            state = started.state
            view = BattleView(state.kind, state, state.opponent.trainer)
            opening = started.events
        elif started.status is BattleStatus.BATTLE_IN_PROGRESS:
            view = services.describe()
        else:
            console.print(_setup_message(started.status, encounter), highlight=False)
            return 1
    session = services.persisted(view, opening) if view is not None else None
    if session is None:
        console.print(GONE_TEXT, highlight=False)
        return 1
    return session


def _is_interactive(console: Console, services: BattleServices) -> bool:
    return console.is_terminal and _isatty(services.stdin)


def _isatty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def _with_progress(
    console: Console, interactive: bool, work: Callable[[Progress | None], Any]
) -> Any:
    """Prepara el combate contando qué hace («Preparando a Noctowl…») si hay TTY."""
    if not interactive:
        return work(None)
    with console.status("Preparando el combate…", spinner="dots") as status:
        return work(lambda message: status.update(message))


def _setup_message(status: BattleStatus, encounter: Mapping[str, Any] | None) -> str:
    if status is BattleStatus.NO_TEAM:
        return "Tu equipo está vacío: añade Pokémon con `pokedex equipo add`."
    if status is BattleStatus.NO_ENCOUNTER:
        return (
            "No hay ningún Pokémon a la vista. Abre una terminal nueva o lucha contra "
            "un entrenador con `pokedex combate -e`."
        )
    if status is BattleStatus.ALREADY_CAPTURED:
        return (
            "Ya capturaste a este Pokémon. Espera a que aparezca otro o lucha contra un "
            "entrenador con `pokedex combate -e`."
        )
    if status is BattleStatus.NO_SPECIES_DATA:
        name = "este Pokémon"
        if encounter is not None and encounter.get("species"):
            name = display_name(str(encounter["species"]), str(encounter.get("form") or "regular"))
        return f"No hay datos de {name} (¿sin conexión?): prueba de nuevo con conexión."
    if status is BattleStatus.NO_TRAINER_POOL:
        return (
            "Aún no hay especies en tu caché para formar un entrenador: captura o busca "
            "(`pokedex search`) algunos Pokémon primero."
        )
    if status is BattleStatus.STALE:
        return "El Pokémon salvaje se fue antes de empezar el combate."
    return f"No se puede empezar el combate ({status.value})."


def _exhibition_message(status: BattleStatus) -> str:
    if status is BattleStatus.NO_TEAM:
        return (
            "Sin A lucha tu líder de equipo, pero tu equipo está vacío: di quién lucha "
            "(`pokedex demo-combate pikachu charmander`) o añade Pokémon con "
            "`pokedex equipo add`."
        )
    if status is BattleStatus.NO_TRAINER_POOL:
        return (
            "No hay especies en tu caché para elegir un rival al azar: di contra quién "
            "(`pokedex demo-combate A B`)."
        )
    if status is BattleStatus.NO_CAPTURE:
        return "No tienes ninguna captura con ese id (mira `pokedex list`)."
    if status is BattleStatus.NO_SPECIES_DATA:
        return (
            "No hay datos de esa especie (¿nombre incorrecto? ¿sin conexión?): prueba con "
            "un nombre de `krabby list`."
        )
    return f"No se puede montar la exhibición ({status.value})."


def _play(
    session: BattleSession,
    *,
    console: Console,
    services: BattleServices,
    options: BattleOptions,
    bag: Bag,
    interactive: bool,
    saved: bool,
) -> int:
    report = BattleReport.start(session)
    estimator = None
    if options.debug and session.kind is BattleKind.WILD:
        estimator = _estimator(session.state, services)
    try:
        if interactive:
            screen = ScreenBattle(
                console,
                services.keyboard(),
                services.sprites,
                options=options,
                bag=bag,
                estimator=estimator,
                clock=services.clock,
                poll=services.menu_poll,
            )
            ending = screen.run(session, report)
        else:
            plain = PlainBattle(
                console,
                services.stdin,
                options=options,
                resolve_ball=services.resolve_ball,
                estimator=estimator,
            )
            ending = plain.run(session, report)
    except KeyboardInterrupt:
        ending = _interrupted(session)
    if ending is Ending.PAUSED:
        console.print(PAUSED_TEXT if saved else EXHIBITION_LEFT_TEXT, highlight=False)
        return 0
    if ending is Ending.STALE:
        console.print(STALE_TEXT, highlight=False)
        return 1
    if ending is Ending.GONE:
        console.print(GONE_TEXT, highlight=False)
        return 1
    render_summary(console, report, session.state, services.record() if saved else None)
    if saved:
        try:
            evolve_after_battle(console, services, report, options.speed)
        except KeyboardInterrupt:
            # La evolución es su propia transacción: si no llegó a completarse
            # sigue encolada y la terminará el hook de la próxima terminal.
            console.print()
    return 0


def _interrupted(session: BattleSession) -> Ending:
    """Ctrl+C: pausa, salvo que el último turno ya terminara el combate.

    Cada turno se guarda al resolverse, así que un Ctrl+C durante el guion del
    último turno o la espera final no deja nada que reanudar: toca el resumen.
    """
    return Ending.FINISHED if session.state.finished else Ending.PAUSED


def _estimator(state: BattleState, services: BattleServices) -> CatchEstimator | None:
    wild = state.opponent.current
    try:
        species = services.species_data(wild.species, wild.form)
        encounter = services.read_encounter()
    except Exception:  # solo es para --debug: sin datos no hay estimación
        return None
    return CatchEstimator.build(species, encounter)


# --- Piezas del combate ----------------------------------------------------------


@dataclass
class Bag:
    """Pokéballs de la BOLSA con sus existencias (``None``: ilimitada)."""

    balls: tuple[Ball, ...]
    stock: dict[str, int | None]

    @classmethod
    def build(
        cls,
        catalog: Mapping[str, Ball],
        inventory: Mapping[str, Any] | None,
        count: Callable[[Any, str], int | None],
    ) -> Bag:
        stock: dict[str, int | None] = {}
        for slug, ball in catalog.items():
            if ball.unlimited:
                stock[slug] = None
                continue
            try:
                stock[slug] = 0 if inventory is None else max(0, int(count(inventory, slug) or 0))
            except (KeyError, TypeError, ValueError):
                stock[slug] = 0
        return cls(tuple(catalog.values()), stock)

    def count(self, ball: Ball) -> int | None:
        return None if ball.unlimited else self.stock.get(ball.slug) or 0

    def empty(self, ball: Ball) -> bool:
        count = self.count(ball)
        return count is not None and count <= 0

    def spend(self, command: BattleCommand, outcome: TurnOutcome) -> None:
        """Descuenta la bola lanzada (la captura ya la gastó en la BD)."""
        if not isinstance(command, ThrowBall):
            return
        if outcome.status not in (BattleStatus.APPLIED, BattleStatus.FINISHED):
            return
        count = self.stock.get(command.ball_slug)
        if count is not None:
            self.stock[command.ball_slug] = max(0, count - 1)


@dataclass(frozen=True)
class CatchEstimator:
    """Probabilidad de captura para ``--debug``, con las reglas de la captura real.

    Especie, nivel del encuentro (50 como mínimo si es legendario), ánimo de la
    Zona Safari, bola y el factor del combate (PS y estado del salvaje).
    """

    capture_rate: int | None
    is_legendary: bool
    is_mythical: bool
    encounter_level: int
    mood: float

    @classmethod
    def build(
        cls, species: Mapping[str, Any] | None, encounter: Mapping[str, Any] | None
    ) -> CatchEstimator | None:
        if species is None:
            return None
        rate = species.get("capture_rate")
        level = species.get("encounter_level")
        try:
            mood = catch_stage_multiplier(int((encounter or {}).get("catch_stage") or 0))
        except (TypeError, ValueError):
            mood = 1.0
        return cls(
            capture_rate=rate if isinstance(rate, int) and not isinstance(rate, bool) else None,
            is_legendary=bool(species.get("is_legendary")),
            is_mythical=bool(species.get("is_mythical")),
            encounter_level=level if isinstance(level, int) else STARTING_LEVEL,
            mood=mood,
        )

    def chance(self, ball_multiplier: float, wild: Combatant) -> float | None:
        minimum = LEGENDARY_LEVEL if self.is_legendary else STARTING_LEVEL
        try:
            return catch_chance(
                self.capture_rate,
                is_legendary=self.is_legendary,
                is_mythical=self.is_mythical,
                shiny=wild.shiny,
                ball_multiplier=ball_multiplier,
                level=max(STARTING_LEVEL, self.encounter_level, minimum),
                mood_multiplier=self.mood,
                battle_multiplier=battle_catch_multiplier(wild),
            )
        except (TypeError, ValueError):
            return None


@dataclass
class BattleReport:
    """Lo que deja el combate para el resumen: EXP, niveles, movimientos y captura."""

    kind: BattleKind
    foe: str
    trainer: str | None
    names: dict[str, str]
    start_levels: dict[str, int]
    experience: dict[str, int] = field(default_factory=dict)
    levels: dict[str, int] = field(default_factory=dict)
    learned: dict[str, list[str]] = field(default_factory=dict)
    capture_id: int | None = None
    leveled: set[int] = field(default_factory=set)

    @classmethod
    def start(cls, session: BattleSession) -> BattleReport:
        state = session.state
        team = state.player.team
        return cls(
            kind=session.kind,
            foe=foe_label(state),
            trainer=session.trainer,
            names={member.key: member.name for member in team},
            start_levels={member.key: member.level for member in team},
        )

    def record(self, outcome: TurnOutcome) -> None:
        for event in outcome.events:
            if isinstance(event, ExperienceGained):
                self.experience[event.key] = self.experience.get(event.key, 0) + event.amount
                self.names.setdefault(event.key, event.name)
            elif isinstance(event, LeveledUp):
                self.levels[event.key] = event.level
                self.names.setdefault(event.key, event.name)
            elif isinstance(event, MoveLearned):
                learned = event.move_name
                if event.forgot:
                    learned += f" (olvidó {event.forgot})"
                self.learned.setdefault(event.key, []).append(learned)
        if outcome.capture_id is not None:
            self.capture_id = outcome.capture_id
        self.leveled.update(outcome.leveled)

    def progressed(self) -> list[str]:
        """Claves de quienes ganaron algo, en el orden del equipo."""
        keys = [*self.names]
        return [
            key
            for key in keys
            if self.experience.get(key) or key in self.levels or self.learned.get(key)
        ]


def foe_label(state: BattleState) -> str:
    """«Pidgey salvaje», «Montañero Fermín» o «Pidgey enemigo»."""
    if state.kind is BattleKind.TRAINER and state.opponent.trainer:
        return state.opponent.trainer
    return display_label(state.kind, "opponent", state.opponent.current.name)


def parse_order(
    text: str, state: BattleState, resolve_ball: Callable[[str], Ball | None]
) -> BattleCommand | _Signal | str:
    """Orden escrita en modo texto → orden del combate, señal o motivo del error.

    ``1``-``4`` usa ese movimiento, ``c N`` cambia al N.º del equipo, ``b
    [bola]`` lanza una Pokéball, ``h`` huye, ``a`` (o una línea vacía) deja
    elegir a la IA y ``q`` pausa. Tras un desmayo basta el número del relevo.
    """
    words = text.strip().lower().split()
    replacing = state.phase is Phase.CHOOSE_REPLACEMENT
    if not words or words[0] in _AUTO_WORDS:
        return Auto()
    head, rest = words[0], words[1:]
    if head in _PAUSE_WORDS:
        return _Signal.PAUSE
    if head in _SWITCH_WORDS:
        # ``isdecimal`` y no ``isdigit``: «²» es un dígito que ``int`` no lee.
        if len(rest) != 1 or not rest[0].isdecimal():
            return "Di a quién sacar: «c 2» saca al segundo de tu equipo."
        index = int(rest[0]) - 1
        return Replace(index) if replacing else Switch(index)
    if head.isdecimal() and not rest:
        index = int(head) - 1
        return Replace(index) if replacing else Fight(index)
    if replacing:
        return "Antes tienes que sacar a otro Pokémon: escribe su número."
    if head in _BALL_WORDS:
        name = " ".join(rest) or "poke"
        ball = resolve_ball(name)
        if ball is None:
            return f"No conozco esa Pokéball: «{name}» (poke, super, ultra o master)."
        return ThrowBall(ball.slug, ball.name, ball.multiplier)
    if head in _RUN_WORDS:
        return Flee()
    return f"No entiendo «{text.strip()}»: {PROMPT_HELP}."


def debug_notes(
    events: Sequence[BattleEvent],
    before: BattleState,
    command: BattleCommand | None,
    estimator: CatchEstimator | None,
) -> list[str]:
    """Cifras de un turno para ``--debug``: probabilidad de la bola y cada daño."""
    notes: list[str] = []
    for event in events:
        note = debug_note(event, before, command, estimator)
        if note:
            notes.append(note)
    return notes


def debug_note(
    event: BattleEvent,
    before: BattleState,
    command: BattleCommand | None,
    estimator: CatchEstimator | None,
) -> str | None:
    if isinstance(event, Damaged):
        text = (
            f"{event.name} −{event.amount} PS ({event.hp_before}→{event.hp_after}/{event.max_hp})"
        )
        if event.cause is DamageCause.MOVE:
            if event.effectiveness != 1:
                text += f" · eficacia ×{event.effectiveness:g}"
            if event.critical:
                text += " · crítico"
        else:
            text += f" · {_DAMAGE_CAUSES.get(event.cause, event.cause.value)}"
        return text
    if isinstance(event, BallThrown) and isinstance(command, ThrowBall) and estimator:
        wild = before.opponent.current
        chance = estimator.chance(command.multiplier, wild)
        if chance is None:
            return None
        factor = battle_catch_multiplier(wild)
        return f"{command.ball_name}: {chance:.1%} de captura (combate ×{factor:.2f})"
    return None


# --- Modo texto ------------------------------------------------------------------


class PlainBattle:
    """Combate sin TTY: cada evento es una línea y cada orden, una línea de stdin."""

    def __init__(
        self,
        console: Console,
        stdin: TextIO,
        *,
        options: BattleOptions,
        resolve_ball: Callable[[str], Ball | None],
        estimator: CatchEstimator | None = None,
    ) -> None:
        self._console = console
        self._stdin = stdin
        self._options = options
        self._resolve_ball = resolve_ball
        self._estimator = estimator
        self._helped = False

    def run(self, session: BattleSession, report: BattleReport) -> Ending:
        if session.opening:
            self._narrate(session.opening, session, session.state, None)
        else:
            self._print(f"¡Sigue el combate contra {foe_label(session.state)}!")
        auto = self._options.auto
        while not session.state.finished:
            before = session.state
            command: BattleCommand
            if auto:
                command = Auto()
            else:
                asked = self._ask(session)
                if asked is _Signal.PAUSE:
                    return Ending.PAUSED
                if asked is _Signal.EOF:
                    auto = True
                    self._print("(Sin más órdenes: decide la IA.)", style="dim")
                    command = Auto()
                elif isinstance(asked, _Signal):
                    continue
                else:
                    command = asked
            outcome = session.take(command)
            if outcome.status in (BattleStatus.INVALID_ACTION, BattleStatus.NO_STOCK):
                self._print(outcome.reason or "Esa orden no vale ahora.")
                if auto:  # la IA no se equivoca; si pasara, no repetirlo sin fin
                    return Ending.GONE
                continue
            if outcome.status is BattleStatus.STALE:
                return Ending.STALE
            if outcome.status is BattleStatus.NO_BATTLE or outcome.state is None:
                return Ending.GONE
            report.record(outcome)
            self._narrate(outcome.events, session, before, command)
        return Ending.FINISHED

    def _ask(self, session: BattleSession) -> BattleCommand | _Signal:
        state = session.state
        replacing = state.phase is Phase.CHOOSE_REPLACEMENT
        if not replacing and engine.locked_in(state, "player"):
            return Fight(0)  # carga o recarga: el motor sigue con lo que tenga en curso
        while True:
            for line in _prompt_lines(state, session.kind):
                self._print(line)
            if not self._helped:
                # La ayuda de las órdenes, una sola vez: luego estorba en el registro.
                self._print(f"Órdenes: {PROMPT_HELP}", style="dim")
                self._helped = True
            if replacing:
                question = "¿A qué Pokémon vas a sacar? (número) › "
            else:
                question = f"¿Qué debe hacer {state.player.current.name}? › "
            self._console.print(question, end="", markup=False, highlight=False, soft_wrap=True)
            line = self._stdin.readline()
            if not line:
                self._console.print()
                return _Signal.EOF
            if not _isatty(self._stdin):
                # Eco de la orden leída: el registro se entiende sin ver stdin.
                self._print(line.rstrip("\n"))
            parsed = parse_order(line, state, self._resolve_ball)
            if isinstance(parsed, Flee) and session.kind is BattleKind.EXHIBITION:
                # El motor lo rechazaría como si fuera contra un entrenador.
                parsed = "En una exhibición no se huye: escribe q para dejarla."
            if isinstance(parsed, str):
                self._print(parsed)
                continue
            return parsed

    def _narrate(
        self,
        events: Sequence[BattleEvent],
        session: BattleSession,
        before: BattleState,
        command: BattleCommand | None,
    ) -> None:
        opponent = before.opponent.current.name
        for index, event in enumerate(events):
            upcoming = events[index + 1] if index + 1 < len(events) else None
            lines = messages_for(
                event,
                kind=session.kind,
                trainer=session.trainer,
                opponent=opponent,
                upcoming=upcoming,
            )
            if isinstance(event, TurnStarted):
                self._console.print()
                for line in lines:
                    self._print(line, style="dim")
                continue
            for line in lines:
                self._print(line)
            if self._options.debug:
                note = debug_note(event, before, command, self._estimator)
                if note:
                    self._print(f"  · {note}", style="dim")

    def _print(self, text: str, style: str | None = None) -> None:
        # Sin cortes de línea de rich: cada frase entera en su línea, también en tuberías.
        self._console.print(text, style=style, markup=False, highlight=False, soft_wrap=True)


def _prompt_lines(state: BattleState, kind: BattleKind) -> list[str]:
    player, foe = state.player.current, state.opponent.current
    if state.phase is Phase.CHOOSE_REPLACEMENT:
        team = " · ".join(
            f"[{index + 1}] {_member_brief(member)}"
            for index, member in enumerate(state.player.team)
        )
        return [f"Tu equipo: {team}"]
    moves = " · ".join(
        f"[{index + 1}] {slot.move.name} {slot.pp}/{slot.max_pp}"
        for index, slot in enumerate(player.moves)
    )
    return [
        f"{_brief(player, 'player', kind)}   contra   {_brief(foe, 'opponent', kind)}",
        f"Movimientos: {moves}",
    ]


def _brief(combatant: Combatant, side: str, kind: BattleKind) -> str:
    label = display_label(kind, "player" if side == "player" else "opponent", combatant.name)
    text = f"{label} Nv.{combatant.level} · PS {max(0, combatant.hp)}/{combatant.max_hp}"
    badge = STATUS_BADGES.get(combatant.status)
    if badge is not None:
        text += f" · {badge[0]}"
    return text


def _member_brief(member: Combatant) -> str:
    text = f"{member.name} Nv.{member.level} PS {max(0, member.hp)}/{member.max_hp}"
    return text + " (debilitado)" if member.fainted else text


# --- Pantalla completa -----------------------------------------------------------


@dataclass(frozen=True)
class _Geometry:
    width: int  # terminal
    height: int
    window: int  # ancho de la ventana de juego
    scene: int  # alto de la escena
    footer: int

    @property
    def fits(self) -> bool:
        return self.window >= MIN_WINDOW_WIDTH and self.scene >= MIN_SCENE_HEIGHT


class ScreenBattle:
    """Combate a pantalla completa: escena animada, caja de texto y menús."""

    def __init__(
        self,
        console: Console,
        keyboard: Keyboard,
        sprites: SpriteLookup,
        *,
        options: BattleOptions,
        bag: Bag,
        estimator: CatchEstimator | None = None,
        clock: Callable[[], float] = time.monotonic,
        poll: float | None = MENU_POLL,
    ) -> None:
        self._console = console
        self._keys = keyboard
        self._sprites = sprites
        self._options = options
        self._speed = max(0.0, options.speed)
        self._bag = bag
        self._estimator = estimator
        self._clock = clock
        self._poll = poll
        self._live: Live | None = None
        self._director: Director | None = None
        self._director_size = (0, 0)
        self._state: BattleState | None = None  # estado al que se resincroniza la escena
        self._kind = BattleKind.WILD
        self._trainer: str | None = None
        self._scene: SceneModel | None = None
        self._override: Text | None = None  # fotograma de transición, en vez de la escena
        self._overlay: str | None = None  # "bag" | "party"
        self._dialog = "text"  # "text" | "menu" | "moves" | "prompt"
        self._message = ""
        self._shown = 0
        self._waiting = False
        self._prompt = ""
        self._menu_index = FIGHT
        self._move_index = 0
        self._bag_index = 0
        self._party_index = 0
        self._replacing = False
        self._debug = ""
        self._quit_requested = False
        self._playing = False
        self._icons: dict[tuple[str, str, bool], Text] = {}

    # --- Bucle ------------------------------------------------------------

    def run(self, session: BattleSession, report: BattleReport) -> Ending:
        """Juega ``session`` hasta el final o la pausa. Pase lo que pase (también
        una excepción o Ctrl+C), la terminal vuelve a su pantalla y a su modo."""
        self._kind, self._trainer = session.kind, session.trainer
        self._state = session.state
        live = Live(
            console=self._console,
            screen=True,
            auto_refresh=False,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        self._keys.__enter__()
        try:
            live.start()
            self._live = live
            try:
                return self._loop(session, report)
            except KeyboardInterrupt:
                return _interrupted(session)
        finally:
            self._live = None
            try:
                live.stop()
                if self._console.is_terminal:
                    # Un Ctrl+C a mitad de fotograma puede dejar un color puesto.
                    self._console.file.write("\x1b[0m")
                    self._console.file.flush()
            finally:
                self._keys.__exit__(None, None, None)

    def _loop(self, session: BattleSession, report: BattleReport) -> Ending:
        if session.opening:
            self._intro()
            self._play(session.opening, session.state)
        else:
            self._ensure_director()
            self._say(f"¡Sigue el combate contra {foe_label(session.state)}!")
        while not session.state.finished:
            before = session.state
            command = self._choose(before)
            if command is None:
                return Ending.PAUSED
            outcome = session.take(command)
            if outcome.status in (BattleStatus.INVALID_ACTION, BattleStatus.NO_STOCK):
                self._notice(outcome.reason or "Esa orden no vale ahora.")
                if self._options.auto:
                    return Ending.GONE
                continue
            if outcome.status is BattleStatus.STALE:
                self._notice(outcome.reason or STALE_TEXT)
                return Ending.STALE
            if outcome.status is BattleStatus.NO_BATTLE or outcome.state is None:
                self._notice(GONE_TEXT)
                return Ending.GONE
            report.record(outcome)
            self._bag.spend(command, outcome)
            if self._options.debug:
                self._debug = " · ".join(
                    debug_notes(outcome.events, before, command, self._estimator)
                )
            self._play(outcome.events, outcome.state)
        self._finale()
        return Ending.FINISHED

    # --- Órdenes ----------------------------------------------------------

    def _choose(self, state: BattleState) -> BattleCommand | None:
        """Orden del jugador para este turno; ``None`` si pide la pausa."""
        if self._options.auto:
            self._overlay, self._dialog = None, "text"
            key = self._keys.read(0)
            if key is not None:
                self._handle_async_key(key)
            return None if self._quit_requested else Auto()
        if state.phase is Phase.CHOOSE_REPLACEMENT:
            replacement = self._party_menu(state, replacing=True)
            return None if isinstance(replacement, _Signal) else replacement
        if engine.locked_in(state, "player"):
            return Fight(0)  # carga o recarga: el motor sigue con lo que tenga en curso
        while True:
            choice = self._main_menu(state)
            if choice is None:
                return None
            command: BattleCommand | _Signal
            if choice == FIGHT:
                command = self._moves_menu(state)
            elif choice == BAG:
                command = self._bag_menu(state)
            elif choice == PARTY:
                command = self._party_menu(state, replacing=False)
            elif state.kind is BattleKind.EXHIBITION:
                self._notice("En una exhibición no se huye: pulsa Q para dejarla.")
                command = _Signal.BACK
            else:
                command = Flee()
            if command is _Signal.PAUSE:
                return None
            if not isinstance(command, _Signal):
                return command

    def _main_menu(self, state: BattleState) -> int | None:
        self._overlay, self._dialog = None, "menu"
        self._prompt = f"¿Qué debe hacer {state.player.current.name}?"
        while True:
            self._refresh()
            key = self._read()
            if key == "quit":
                return None
            if key in ("1", "2", "3", "4"):
                self._menu_index = int(key) - 1
                return self._menu_index
            if key == "enter":
                return self._menu_index
            self._menu_index = _grid_move(self._menu_index, key, len(MAIN_MENU))

    def _moves_menu(self, state: BattleState) -> BattleCommand | _Signal:
        active = state.player.current
        if not engine.available_moves(active):
            return Fight(0)  # sin PP en nada: Forcejeo
        self._move_index = max(0, min(self._move_index, len(active.moves) - 1))
        while True:
            self._overlay, self._dialog = None, "moves"
            self._refresh()
            key = self._read()
            if key in ("cancel", "quit"):
                return _Signal.BACK
            if key.isdecimal() and 1 <= int(key) <= len(active.moves):
                self._move_index = int(key) - 1
                key = "enter"
            if key == "enter":
                reason = engine.action_error(state, UseMove(self._move_index))
                if reason is None:
                    return Fight(self._move_index)
                self._notice(reason)
                continue
            self._move_index = _grid_move(self._move_index, key, len(active.moves))

    def _bag_menu(self, state: BattleState) -> BattleCommand | _Signal:
        balls = self._bag.balls
        if not balls:
            self._notice("La bolsa está vacía.")
            return _Signal.BACK
        self._bag_index = max(0, min(self._bag_index, len(balls) - 1))
        while True:
            self._overlay, self._dialog = "bag", "prompt"
            self._prompt = "¿Qué Pokéball vas a lanzar?"
            self._refresh()
            key = self._read()
            if key in ("cancel", "quit"):
                return _Signal.BACK
            if key.isdecimal() and 1 <= int(key) <= len(balls):
                self._bag_index = int(key) - 1
                key = "enter"
            if key == "enter":
                ball = balls[self._bag_index]
                if state.kind is BattleKind.WILD and self._bag.empty(ball):
                    self._notice(f"No te queda ninguna {ball.name}.")
                    continue
                return ThrowBall(ball.slug, ball.name, ball.multiplier)
            self._bag_index = _list_move(self._bag_index, key, len(balls))

    def _party_menu(self, state: BattleState, *, replacing: bool) -> BattleCommand | _Signal:
        team = state.player.team
        able = state.player.able
        self._replacing = replacing
        self._party_index = (able[0] if able else 0) if replacing else state.player.active
        try:
            while True:
                self._overlay, self._dialog = "party", "prompt"
                self._prompt = (
                    "¿A qué Pokémon vas a sacar?"
                    if replacing
                    else f"¿Por quién cambias a {state.player.current.name}?"
                )
                self._refresh()
                key = self._read()
                if key == "cancel" and not replacing:
                    return _Signal.BACK
                if key == "quit":
                    return _Signal.PAUSE if replacing else _Signal.BACK
                if key.isdecimal() and 1 <= int(key) <= len(team):
                    self._party_index = int(key) - 1
                    key = "enter"
                if key == "enter":
                    index = self._party_index
                    if replacing:
                        reason = _replacement_error(state, index)
                    else:
                        reason = engine.action_error(state, SwitchTo(index))
                    if reason is None:
                        return Replace(index) if replacing else Switch(index)
                    self._notice(reason)
                    continue
                self._party_index = _list_move(self._party_index, key, len(team))
        finally:
            self._replacing = False

    def _read(self) -> str:
        """Siguiente tecla de un menú: espera lo que haga falta y, mientras,
        repinta si la terminal cambia de tamaño."""
        while True:
            key = self._keys.read(self._poll)
            if key == "interrupt":
                raise KeyboardInterrupt
            if key is not None:
                return key
            if self._poll is None:
                return "other"
            if self._resized():
                self._refresh()

    def _handle_async_key(self, key: str) -> bool:
        """Tecla pulsada mientras se escribe o se espera; True si pide pasar."""
        if key == "interrupt":
            raise KeyboardInterrupt
        if key == "quit" and self._options.auto:
            self._quit_requested = True
        return key == "enter"

    # --- Guion ------------------------------------------------------------

    def _intro(self) -> None:
        """Transición de entrada: franjas que tapan el campo y un destello."""
        geometry = self._geometry()
        if not geometry.fits or self._speed <= 0:
            return
        for frame in intro_frames(geometry.window, geometry.scene, self._kind):
            self._override = frame
            self._refresh()
            self._pause(INTRO_DELAY * self._speed)
        self._override = None

    def _play(self, events: Sequence[BattleEvent], final: BattleState) -> None:
        """Reproduce los beats del director con su ritmo y deja la escena en reposo."""
        director = self._ensure_director()
        beats = director.direct(events, kind=self._kind, trainer=self._trainer)
        self._state = final
        self._overlay, self._dialog = None, "text"
        self._playing = True
        try:
            stale = False
            for beat in beats:
                if not stale and self._resized():
                    # La terminal cambió de tamaño: un director nuevo, ya con el
                    # estado final; lo que queda del guion solo aporta su texto.
                    stale = True
                    director = self._ensure_director()
                if not stale:
                    self._scene = beat.scene
                if beat.message is not None:
                    self._say(beat.message, delay=beat.delay)
                elif not stale and self._speed > 0:
                    self._refresh()
                    self._pause(beat.delay * self._speed)
            self._scene = director.scene_for(final)
            self._refresh()
        finally:
            self._playing = False

    def _say(self, message: str, *, delay: float = 0.7) -> None:
        self._dialog = "text"
        self._type(message)
        self._pause(delay * self._speed, skippable=True)

    def _notice(self, message: str) -> None:
        """Aviso breve en la caja de texto; vuelve al menú en el que estaba."""
        dialog = self._dialog
        self._dialog = "text"
        self._type(message)
        self._pause(max(NOTICE_DELAY * self._speed, MIN_NOTICE_DELAY), skippable=True)
        self._dialog = dialog

    def _finale(self) -> None:
        self._overlay, self._dialog = None, "text"
        self._waiting = True
        self._refresh()
        self._pause(FINALE_DELAY * self._speed, skippable=True)
        self._waiting = False

    def _type(self, message: str) -> None:
        """Escribe ``message`` a máquina; Enter (o Z, o espacio) lo completa."""
        self._message, self._shown = message, 0
        total = len(message)
        step = TYPE_DELAY * self._speed
        if step <= 0 or total == 0:
            self._shown = total
            self._refresh()
            return
        # Cada fotograma repinta la pantalla entera: varias letras por fotograma
        # mantienen el ritmo de lectura sin pasar de ~30 repintados por segundo.
        chunk = max(1, math.ceil(FRAME_BUDGET / step))
        start = self._clock()
        while self._shown < total:
            # La letra i (desde 1) toca en ``start + (i - 1) * step``.
            due = int((self._clock() - start) / step + 1e-9) + 1
            self._shown = min(total, max(self._shown + chunk, due))
            self._refresh()
            if self._shown >= total:
                return
            next_frame = start + (self._shown + chunk - 1) * step
            key = self._keys.read(max(0.0, next_frame - self._clock()))
            if key is not None and self._handle_async_key(key):
                self._shown = total
                self._refresh()
                return

    def _pause(self, seconds: float, *, skippable: bool = False) -> None:
        """Espera atendiendo al teclado: Enter acorta las esperas de texto."""
        if seconds <= 0:
            return
        deadline = self._clock() + seconds
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                return
            key = self._keys.read(remaining)
            if key is None:
                return
            if self._handle_async_key(key) and skippable:
                return

    # --- Escena -----------------------------------------------------------

    def _geometry(self) -> _Geometry:
        width, height = self._console.size
        window = min(width, MAX_WINDOW_WIDTH)
        footer = 2 if self._options.debug else 1
        scene = min(
            MAX_SCENE_HEIGHT,
            height - DIALOG_HEIGHT - footer,
            max(MIN_SCENE_HEIGHT, round(window * SCENE_ASPECT)),
        )
        return _Geometry(width, height, window, scene, footer)

    def _resized(self) -> bool:
        geometry = self._geometry()
        return (geometry.window, geometry.scene) != self._director_size

    def _ensure_director(self) -> Director:
        """El director del tamaño actual; uno nuevo si la terminal cambió."""
        geometry = self._geometry()
        size = (max(1, geometry.window), max(1, geometry.scene))
        if self._director is None or size != self._director_size:
            if self._state is None:
                raise RuntimeError("no hay estado de combate que pintar")
            self._director = Director(self._state, self._sprites, *size)
            self._director_size = size
            self._scene = self._director.scene
        return self._director

    def _refresh(self) -> None:
        if not self._playing and self._state is not None and self._resized():
            self._ensure_director()
        if self._live is not None:
            self._live.update(self.render(), refresh=True)

    # --- Render -----------------------------------------------------------

    def render(self) -> RenderableType:
        """Fotograma completo: escena (con menú encima si toca), caja y pie."""
        geometry = self._geometry()
        if not geometry.fits:
            return Align.center(
                Text(TOO_SMALL_TEXT, justify="center", style="bold"), vertical="middle"
            )
        scene = self._scene_text(geometry)
        if self._overlay is not None and self._state is not None:
            if self._overlay == "party":
                panel, width = self._party_panel(self._state, geometry)
            else:
                panel, width = self._bag_panel(self._state, geometry)
            scene = self._overlaid(scene, panel, width, geometry)
        parts: list[RenderableType] = [
            scene,
            self._dialog_box(geometry),
            _line(self._hint(), geometry.window, _FOOTER_STYLE, justify="center"),
        ]
        if self._options.debug:
            parts.append(_line(self._debug, geometry.window, _DEBUG_STYLE))
        return Align.center(Group(*parts), vertical="middle")

    def _scene_text(self, geometry: _Geometry) -> Text:
        if self._override is not None:
            return self._override
        if self._scene is None:
            blank = Text("\n".join(" " * geometry.window for _ in range(geometry.scene)))
            return with_backdrop(blank, self._kind)
        return with_backdrop(render_scene(self._scene), self._kind)

    def _overlaid(
        self, scene: Text, panel: RenderableType, width: int, geometry: _Geometry
    ) -> Text:
        """La escena atenuada con un panel flotante en el centro (equipo, bolsa)."""
        scene = scene.copy()
        scene.stylize(Style(dim=True))
        lines = scene.split("\n", allow_blank=True)
        width = min(width, geometry.window)
        options = self._console.options.update(width=width, height=None)
        rendered = self._console.render_lines(panel, options, pad=True)[: len(lines)]
        top = max(0, (len(lines) - len(rendered)) // 2)
        left = max(0, (geometry.window - width) // 2)
        for offset, segments in enumerate(rendered):
            row = top + offset
            line = lines[row]
            piece = Text()
            for segment in segments:
                if not segment.control:
                    piece.append(segment.text, segment.style)
            merged = line[:left]
            merged.append_text(piece)
            merged.append_text(line[left + width :])
            lines[row] = merged
        joined = Text("\n").join(lines)
        joined.no_wrap = True
        joined.overflow = "crop"
        return joined

    def _dialog_box(self, geometry: _Geometry) -> RenderableType:
        width = geometry.window
        if self._dialog == "menu":
            menu_width = min(MENU_WIDTH, width // 2)
            grid = Table.grid(padding=0)
            grid.add_column(width=width - menu_width)
            grid.add_column(width=menu_width)
            grid.add_row(
                _message_panel(
                    self._wrapped(self._prompt, width - menu_width - 4), width - menu_width
                ),
                Panel(
                    menu_text(MAIN_MENU, self._menu_index),
                    width=menu_width,
                    height=DIALOG_HEIGHT,
                    box=box.ROUNDED,
                    style=_PAPER_STYLE,
                    border_style=_FRAME_STYLE,
                    padding=(0, 1),
                ),
            )
            return grid
        if self._dialog == "moves" and self._state is not None:
            active = self._state.player.current
            body = moves_panel(active.moves, self._move_index, self._state.opponent.current.types)
            return Panel(
                body,
                width=width,
                height=DIALOG_HEIGHT,
                box=box.ROUNDED,
                style=_PAPER_STYLE,
                border_style=_FRAME_STYLE,
                padding=(0, 1),
            )
        if self._dialog == "prompt":
            return _message_panel(self._wrapped(self._prompt, width - 4), width)
        return _message_panel(self._typed(width - 4), width)

    def _wrapped(self, message: str, inner: int) -> Text:
        lines = textwrap.wrap(message, width=max(8, inner)) or [""]
        return Text("\n".join(lines[: DIALOG_HEIGHT - 2]), no_wrap=True, overflow="crop")

    def _typed(self, inner: int) -> Text:
        """El mensaje actual hasta la letra escrita, sin saltos de línea que bailen."""
        lines = (textwrap.wrap(self._message, width=max(8, inner)) or [""])[: DIALOG_HEIGHT - 2]
        remaining = self._shown
        text = Text(no_wrap=True, overflow="crop")
        for index, line in enumerate(lines):
            if index:
                text.append("\n")
            text.append(line[: max(0, remaining)])
            remaining -= len(line) + 1  # el espacio que se come el salto de línea
        if self._waiting and self._shown >= len(self._message):
            text.append(" ▼", style=_WAIT_STYLE)
        return text

    def _hint(self) -> str:
        leave = "Q sale" if self._kind is BattleKind.EXHIBITION else "Q pausa"
        if self._options.auto:
            return f"Combate automático · {leave} · Ctrl+C también"
        if self._overlay == "party":
            if self._replacing:
                return f"↑↓ elegir · Enter/Z sacar · 1-6 directo · {leave}"
            return "↑↓ elegir · Enter/Z cambiar · 1-6 directo · X/Esc atrás"
        if self._overlay == "bag":
            return "↑↓ elegir · Enter/Z lanzar · X/Esc atrás"
        if self._dialog == "menu":
            return f"←↑↓→ mover · Enter/Z elegir · 1-4 directo · {leave}"
        if self._dialog == "moves":
            return "←↑↓→ mover · 1-4 o Enter/Z usar · X/Esc atrás"
        verb = "sale" if self._kind is BattleKind.EXHIBITION else "pausa"
        return f"Enter/Z pasa el texto · Ctrl+C {verb}"

    def _party_panel(self, state: BattleState, geometry: _Geometry) -> tuple[Panel, int]:
        team = state.player.team
        width = max(40, min(geometry.window - 4, 76))
        roomy = geometry.scene >= len(team) * ICON_ROWS + 2
        table = Table.grid(padding=(0, 1))
        table.add_column(width=1)
        if roomy:
            table.add_column(width=ICON_COLS)
        table.add_column()
        for index, member in enumerate(team):
            chosen = index == self._party_index
            cursor = Text("▶" if chosen else " ", style=_CURSOR_STYLE)
            info = _member_info(member, active=index == state.player.active, roomy=roomy)
            cells: list[RenderableType] = (
                [cursor, self._icon(member), info] if roomy else [cursor, info]
            )
            table.add_row(*cells, style=_SELECTED_STYLE if chosen else None)
        title = "¿A QUIÉN SACAS?" if self._replacing else "TU EQUIPO"
        panel = Panel(
            table,
            title=f"[bold]{title}[/]",
            box=box.DOUBLE,
            style=_PAPER_STYLE,
            border_style=_FRAME_STYLE,
            width=width,
            padding=(0, 1),
        )
        return panel, width

    def _bag_panel(self, state: BattleState, geometry: _Geometry) -> tuple[Panel, int]:
        width = max(40, min(geometry.window - 4, 58))
        table = Table.grid(padding=(0, 1))
        table.add_column(width=1)
        table.add_column(width=1)
        table.add_column(min_width=11)
        table.add_column(justify="right", min_width=3)
        table.add_column(min_width=6)
        estimate = self._estimator is not None and state.kind is BattleKind.WILD
        if estimate:
            table.add_column(justify="right")
        for index, ball in enumerate(self._bag.balls):
            chosen = index == self._bag_index
            count = self._bag.count(ball)
            empty = count is not None and count <= 0
            color = BALL_LABELS.get(ball.slug, ("", "red3"))[1]
            row: list[RenderableType] = [
                Text("▶" if chosen else " ", style=_CURSOR_STYLE),
                Text("●", style=color),
                Text(ball.name, style="bold" if chosen else ("dim" if empty else "")),
                Text("∞" if count is None else f"×{count}", style="dim" if empty else ""),
                Text("segura" if ball.guaranteed else f"×{ball.multiplier:g}", style="grey46"),
            ]
            if estimate and self._estimator is not None:
                chance = self._estimator.chance(ball.multiplier, state.opponent.current)
                row.append(Text("" if chance is None else f"{chance:.0%}", style="grey46"))
            table.add_row(*row, style=_SELECTED_STYLE if chosen else None)
        note = Text(_bag_note(state), style="grey46")
        panel = Panel(
            Group(table, Text(""), note),
            title="[bold]BOLSA[/]",
            box=box.DOUBLE,
            style=_PAPER_STYLE,
            border_style=_FRAME_STYLE,
            width=width,
            padding=(0, 1),
        )
        return panel, width

    def _icon(self, member: Combatant) -> Text:
        key = (member.species, member.form, member.shiny)
        if key not in self._icons:
            try:
                ansi = self._sprites(*key)
            except Exception:  # decorativo
                ansi = None
            self._icons[key] = grid_text(
                prepare_sprite(ansi, ICON_ROWS, ICON_COLS, mirror=False), ICON_ROWS, ICON_COLS
            )
        icon = self._icons[key]
        if member.fainted:
            icon = icon.copy()
            icon.stylize(Style(dim=True))
        return icon


def _line(content: str, width: int, style: Style, *, justify: JustifyMethod = "left") -> Text:
    """Una línea que nunca pasa de ``width`` celdas: si no, ensancharía la ventana."""
    text = Text(content or " ", style=style, justify=justify, no_wrap=True, overflow="ellipsis")
    text.truncate(width, overflow="ellipsis")
    return text


def _message_panel(body: Text, width: int) -> Panel:
    return Panel(
        body,
        width=width,
        height=DIALOG_HEIGHT,
        box=box.HEAVY,
        style=_MESSAGE_STYLE,
        border_style=_MESSAGE_BORDER,
        padding=(0, 1),
    )


def _bag_note(state: BattleState) -> str:
    if state.kind is BattleKind.TRAINER:
        return "No puedes capturar el Pokémon de otro entrenador."
    if state.kind is BattleKind.EXHIBITION:
        return "En una exhibición no se captura."
    if engine.locked_in(state, "player"):
        return f"{state.player.current.name} no puede hacer otra cosa este turno."
    return "Debilítalo o déjalo dormido, paralizado… y la captura será más fácil."


def _replacement_error(state: BattleState, index: int) -> str | None:
    team = state.player.team
    if not 0 <= index < len(team):
        return "Ese Pokémon no está en el equipo."
    if team[index].fainted:
        return f"¡{team[index].name} no tiene fuerzas para luchar!"
    return None


def _member_info(member: Combatant, *, active: bool, roomy: bool) -> Text:
    """Ficha de un miembro del equipo: nombre, nivel, estado, PS y tipos."""
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(member.name, style="bold dim" if member.fainted else "bold")
    gender = {"male": ("♂", "#2870e8"), "female": ("♀", "#e24268")}.get(member.gender or "")
    if gender:
        text.append(gender[0], style=gender[1])
    if member.shiny:
        text.append("★", style="#e2a014")
    text.append(f"  Nv.{member.level}")
    badge = status_badge(member)
    if badge is not None:
        text.append("  ")
        text.append_text(badge)
    if active and not member.fainted:
        text.append("  ")
        text.append(" EN COMBATE ", style=Style(color="white", bgcolor="#3a6fc4", bold=True))
    bar_width = 18 if roomy else 12
    line = Text(no_wrap=True, overflow="ellipsis")
    line.append("PS ", style="bold #c08a20")
    line.append_text(hp_bar(member.hp, member.max_hp, bar_width))
    line.append(f" {max(0, member.hp)}/{member.max_hp}")
    if not roomy:
        text.append("  ")
        text.append_text(line)
        return text
    types = Text(no_wrap=True, overflow="ellipsis")
    for index, type_slug in enumerate(member.types):
        if index:
            types.append(" ")
        types.append_text(type_chip(type_slug))
    card = Text("\n").join([text, line, types])
    card.no_wrap = True
    card.overflow = "ellipsis"
    return card


def status_badge(member: Combatant) -> Text | None:
    """Insignia de estado («PAR», «DOR»…) o «DEB» si está debilitado."""
    if member.fainted:
        return Text(" DEB ", style=Style(color="white", bgcolor="#7a7a7a", bold=True))
    badge = STATUS_BADGES.get(member.status)
    if badge is None:
        return None
    abbreviation, color = badge
    triplet = Color.parse(color).get_truecolor()
    rgb = (triplet.red, triplet.green, triplet.blue)
    return Text(
        f" {abbreviation} ",
        style=Style(color=Color.from_rgb(*_ink_for(rgb)), bgcolor=Color.from_rgb(*rgb), bold=True),
    )


def type_chip(type_slug: str) -> Text:
    """Chapa del tipo con su color clásico: « Fuego »."""
    rgb = TYPE_RGB.get(type_slug, (168, 168, 120))
    return Text(
        f" {type_name(type_slug)} ",
        style=Style(color=Color.from_rgb(*_ink_for(rgb)), bgcolor=Color.from_rgb(*rgb), bold=True),
    )


def move_chip(move: Move) -> Text:
    rgb = TYPE_RGB.get(move.type, (168, 168, 120))
    return Text(
        f" {move.name} ",
        style=Style(color=Color.from_rgb(*_ink_for(rgb)), bgcolor=Color.from_rgb(*rgb), bold=True),
    )


def _ink_for(rgb: RGB) -> RGB:
    luminance = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    return (36, 32, 24) if luminance > 150 else (255, 255, 255)


def grid_text(grid: Grid, rows: int, cols: int) -> Text:
    """Rejilla de sprite → texto de ``rows`` × ``cols`` celdas, apoyado abajo y centrado."""
    height = len(grid)
    width = len(grid[0]) if grid else 0
    top = max(0, rows - height)
    left = max(0, (cols - width) // 2)
    text = Text(no_wrap=True, overflow="crop")
    for row in range(rows):
        if row:
            text.append("\n")
        source = row - top
        if not 0 <= source < height:
            text.append(" " * cols)
            continue
        text.append(" " * left)
        cells = grid[source][: max(0, cols - left)]
        for cell in cells:
            if cell.fg is None and cell.bg is None:
                text.append(cell.ch)
                continue
            text.append(
                cell.ch,
                style=Style(
                    color=Color.from_rgb(*cell.fg) if cell.fg is not None else None,
                    bgcolor=Color.from_rgb(*cell.bg) if cell.bg is not None else None,
                ),
            )
        text.append(" " * max(0, cols - left - len(cells)))
    return text


def _grid_move(index: int, key: str, count: int, columns: int = 2) -> int:
    """Cursor en una rejilla de ``columns`` columnas (menú 2×2, movimientos)."""
    if count <= 0:
        return 0
    column = index % columns
    if key == "up" and index - columns >= 0:
        return index - columns
    if key == "down" and index + columns < count:
        return index + columns
    if key == "left" and column > 0:
        return index - 1
    if key == "right" and column < columns - 1 and index + 1 < count:
        return index + 1
    return index


def _list_move(index: int, key: str, count: int) -> int:
    if count <= 0:
        return 0
    if key == "up":
        return (index - 1) % count
    if key == "down":
        return (index + 1) % count
    return index


@lru_cache(maxsize=32)
def _backdrop_styles(kind: BattleKind, height: int) -> tuple[Style, ...]:
    top, middle, bottom = _BACKDROPS.get(kind, _BACKDROPS[BattleKind.WILD])
    horizon = max(1, round(height * 0.5))
    styles: list[Style] = []
    for row in range(height):
        if row < horizon:
            rgb = _mix(top, middle, row / max(1, horizon - 1))
        else:
            rgb = _mix(middle, bottom, (row - horizon) / max(1, height - horizon - 1))
        styles.append(Style(bgcolor=Color.from_rgb(*rgb)))
    return tuple(styles)


def _mix(start: RGB, end: RGB, amount: float) -> RGB:
    amount = max(0.0, min(1.0, amount))
    return (
        round(start[0] + (end[0] - start[0]) * amount),
        round(start[1] + (end[1] - start[1]) * amount),
        round(start[2] + (end[2] - start[2]) * amount),
    )


def with_backdrop(text: Text, kind: BattleKind) -> Text:
    """La escena sobre un fondo de campo (cielo y suelo en degradado).

    El fondo va por debajo de todo: lo que la escena pinta con su color lo
    tapa, y lo transparente (celdas vacías, mitades libres de los medios
    bloques) deja ver el campo en vez del fondo de la terminal.
    """
    lines = text.split("\n", allow_blank=True)
    for line, style in zip(lines, _backdrop_styles(kind, len(lines))):
        line.style = style
    joined = Text("\n").join(lines)
    joined.no_wrap = True
    joined.overflow = "crop"
    return joined


def intro_frames(width: int, height: int, kind: BattleKind, steps: int = INTRO_STEPS) -> list[Text]:
    """Transición de entrada: franjas oscuras que cubren el campo y un destello.

    Salvaje: franjas alternas desde los dos lados, como al saltar un Pokémon de
    la hierba; entrenador: una barrida en diagonal; exhibición: un telón que se
    cierra desde el centro.
    """
    styles = _backdrop_styles(kind, height)
    dark = Style(bgcolor=Color.from_rgb(*_INTRO_DARK))
    frames: list[Text] = []
    for step in range(1, steps + 1):
        progress = step / steps
        text = Text(no_wrap=True, overflow="crop")
        for row in range(height):
            if row:
                text.append("\n")
            start, end = _intro_span(kind, row, height, width, progress)
            text.append(" " * start, style=styles[row])
            text.append(" " * (end - start), style=dark)
            text.append(" " * (width - end), style=styles[row])
        frames.append(text)
    frames.append(
        Text(
            "\n".join(" " * width for _ in range(height)),
            style=Style(bgcolor=Color.from_rgb(*_FLASH)),
            no_wrap=True,
            overflow="crop",
        )
    )
    return frames


def _intro_span(
    kind: BattleKind, row: int, height: int, width: int, progress: float
) -> tuple[int, int]:
    """Columnas tapadas ``[inicio, fin)`` de una fila de la transición."""
    if kind is BattleKind.TRAINER:
        covered = round(width * (2 * progress - row / max(1, height)))
        return 0, max(0, min(width, covered))
    if kind is BattleKind.EXHIBITION:
        half = min(width, math.ceil(width * progress)) // 2
        center = width // 2
        return max(0, center - half), min(
            width, center + half + (width % 2 if progress >= 1 else 0)
        )
    covered = min(width, math.ceil(width * progress))
    return (0, covered) if row % 2 == 0 else (width - covered, width)


# --- Resumen y evoluciones ---------------------------------------------------------

_ACCENTS: dict[Outcome | None, str] = {
    Outcome.WON: "green3",
    Outcome.CAPTURED: "green3",
    Outcome.LOST: "red3",
}


def render_summary(
    console: Console,
    report: BattleReport,
    state: BattleState,
    record: Mapping[str, Any] | None,
) -> None:
    """Resumen en la pantalla normal: resultado, EXP, niveles, movimientos y captura."""
    exhibition = report.kind is BattleKind.EXHIBITION
    accent = "cyan" if exhibition else _ACCENTS.get(state.outcome, "yellow3")
    if exhibition:
        title = f"Exhibición: {state.player.current.name} contra {state.opponent.current.name}"
    else:
        title = f"Combate contra {report.foe}"
    rows: list[RenderableType] = []
    headline = Text(summary_headline(report, state), style=f"bold {accent}")
    turns = state.turn
    headline.append(f"   {turns} turno{'' if turns == 1 else 's'}", style="grey54")
    rows.append(headline)
    progressed = report.progressed()
    if progressed:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold")
        table.add_column(justify="right")
        table.add_column()
        table.add_column()
        for key in progressed:
            experience = report.experience.get(key, 0)
            start = report.start_levels.get(key)
            level = report.levels.get(key)
            levels = f"Nv. {start} → {level}" if level is not None and start is not None else ""
            learned = report.learned.get(key)
            table.add_row(
                report.names.get(key, key),
                f"+{experience} EXP" if experience else "",
                levels,
                f"aprendió {', '.join(learned)}" if learned else "",
            )
        rows.append(table)
    if report.capture_id is not None:
        rows.append(
            Text(
                f"Captura #{report.capture_id} · míralo con `pokedex vision {report.capture_id}`",
                style="green3",
            )
        )
    if record is not None:
        rows.append(Text(_record_line(report.kind, record), style="grey54"))
    elif exhibition:
        rows.append(Text("Exhibición: no se ha guardado nada.", style="grey54"))
    console.print()
    console.print(
        Panel(
            Group(*rows),
            title=f"[bold]{escape(title)}[/]",
            title_align="left",
            box=box.ROUNDED,
            border_style=accent,
            padding=(1, 2),
            expand=False,
        )
    )


def summary_headline(report: BattleReport, state: BattleState) -> str:
    outcome = state.outcome
    player, opponent = state.player.current.name, state.opponent.current.name
    if report.kind is BattleKind.EXHIBITION:
        if outcome is Outcome.WON:
            return f"¡Gana {player}!"
        if outcome is Outcome.LOST:
            return f"Gana {opponent}."
        if outcome is Outcome.DRAW:
            return "Tablas: los dos están agotados."
        return "Exhibición terminada."
    if outcome is Outcome.WON:
        if report.kind is BattleKind.TRAINER and report.trainer:
            return f"¡Has derrotado a {report.trainer}!"
        return f"¡Victoria! {report.foe} se debilitó."
    if outcome is Outcome.LOST:
        if report.kind is BattleKind.TRAINER and report.trainer:
            return f"Has perdido contra {report.trainer}."
        return "Tu equipo ya no puede luchar."
    if outcome is Outcome.ESCAPED:
        return "Escapaste sin problemas."
    if outcome is Outcome.CAPTURED:
        return f"¡Has capturado a {opponent}!"
    if outcome is Outcome.OPPONENT_FLED:
        return f"{report.foe} huyó."
    if outcome is Outcome.DRAW:
        return "Tablas: las dos partes están agotadas."
    return "Combate terminado."


def _record_line(kind: BattleKind, record: Mapping[str, Any]) -> str:
    def number(key: str) -> int:
        value = record.get(key)
        return value if isinstance(value, int) else 0

    if kind is BattleKind.TRAINER:
        return (
            f"Entrenadores: {number('trainer_wins')} victorias · "
            f"{number('trainer_losses')} derrotas · racha {number('streak')} "
            f"(mejor {number('best_streak')})"
        )
    return (
        f"Combates salvajes: {number('wild_battles')} · victorias {number('wild_wins')} · "
        f"capturas {number('wild_captures')}"
    )


def evolve_after_battle(
    console: Console, services: BattleServices, report: BattleReport, speed: float
) -> None:
    """Evoluciones que el combate dejó encoladas, animadas como en el hook."""
    if not report.leveled:
        return
    pending = [
        evolution
        for evolution in services.pending_evolutions()
        if evolution.capture_id in report.leveled
    ]
    if not pending:
        return
    # El perfil del destino trae el siguiente eslabón de la cadena: prepararlo
    # antes de completar deja encolado el siguiente si el nivel ya lo alcanza.
    for evolution in pending:
        services.prepare_evolution(evolution)
    done = services.evolve([evolution.capture_id for evolution in pending])
    renderer = services.sprite_renderer()
    for evolution in done:
        animation.play_evolution_animation(
            console,
            evolution.species,
            evolution.form,
            evolution.target_species,
            evolution.target_form,
            evolution.shiny,
            speed=max(0.1, speed),
            sprite_renderer=renderer,
        )


# --- Ficha de movimientos ------------------------------------------------------------


def render_moveset(console: Console, view: MovesetView) -> None:
    """Moveset actual, aprendibles hasta su nivel y próximos de una captura."""
    header = Text()
    header.append(view.name, style="bold")
    species = display_name(view.species, view.form)
    if species != view.name:
        header.append(f" ({species})", style="grey62")
    header.append(f" · #{view.capture_id} · Nv. {view.level}  ", style="grey62")
    for type_slug in view.types:
        header.append_text(type_chip(type_slug))
        header.append(" ")
    console.print(header)
    mode = (
        "Moveset propio, elegido con --set."
        if view.custom
        else "Moveset automático: se recalcula al subir de nivel."
    )
    console.print(Text(mode, style="grey54"))

    current = Table(box=box.SIMPLE_HEAVY, pad_edge=False)
    current.add_column("Movimiento")
    current.add_column("Tipo")
    current.add_column("Clase")
    current.add_column("Pot.", justify="right")
    current.add_column("Prec.", justify="right")
    current.add_column("PP", justify="right")
    for move in view.moves:
        current.add_row(move_chip(move), *_move_cells(move), str(move.pp))
    console.print(current)

    in_use = {move.slug for move in view.moves}
    if view.learnable:
        learnable = Table(
            title=f"Aprendibles hasta el Nv. {view.level}",
            title_justify="left",
            box=box.SIMPLE,
            pad_edge=False,
        )
        learnable.add_column("Nv.", justify="right")
        learnable.add_column("Movimiento")
        learnable.add_column("Tipo")
        learnable.add_column("Clase")
        learnable.add_column("Pot.", justify="right")
        learnable.add_column("Prec.", justify="right")
        learnable.add_column("")
        for entry in view.learnable:
            learnable.add_row(*_learnable_cells(entry, entry.slug in in_use))
        console.print(learnable)
    if view.upcoming:
        upcoming = " · ".join(
            f"Nv. {entry.level} {_learnable_name(entry)}" for entry in view.upcoming[:6]
        )
        console.print(Text(f"Próximos: {upcoming}", style="grey62"))
    console.print(
        Text(
            f"Elige los tuyos con `pokedex movimientos {view.capture_id} --set mov1,mov2,…` "
            "(slugs o nombres en español, entre comillas si llevan espacios) y vuelve al "
            "automático con `--auto`.",
            style="dim",
        )
    )


def _move_cells(move: Move) -> tuple[str, str, str, str]:
    return (
        type_name(move.type),
        DAMAGE_CLASS_NAMES_ES.get(move.damage_class, "Estado"),
        "—" if move.power is None else str(move.power),
        "—" if move.accuracy is None else str(move.accuracy),
    )


def _learnable_name(entry: LearnableMove) -> str:
    if entry.move is not None:
        return entry.move.name
    return entry.slug.replace("-", " ").title()


def _learnable_cells(entry: LearnableMove, in_use: bool) -> list[RenderableType]:
    move = entry.move
    if move is None:
        return [
            str(entry.level),
            _learnable_name(entry),
            "?",
            "?",
            "?",
            "?",
            Text("sin datos", style="dim"),
        ]
    style = "" if entry.supported else "dim"
    if in_use:
        note = Text("en uso", style="green3")
    elif not entry.supported:
        note = Text("aún no funciona en combate", style="dim")
    else:
        note = Text("")
    name = move_chip(move) if entry.supported else Text(move.name, style="dim")
    return [
        Text(str(entry.level), style=style),
        name,
        *(Text(cell, style=style) for cell in _move_cells(move)),
        note,
    ]
