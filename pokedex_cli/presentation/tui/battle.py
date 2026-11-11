"""Combate en la TUI: lanzador, pantalla de combate y paneles.

La pantalla de combate imita a los juegos de GBA dentro de la carcasa de la
Pokédex: el campo (``battle_scene.render_scene`` al tamaño exacto del widget)
sobre el papel crema con marco doble y, debajo, la caja de texto que escribe a
máquina junto al menú de órdenes 2×2 (LUCHAR, BOLSA, POKÉMON, HUIR). El equipo,
la bolsa y el resultado son paneles modales encima del combate.

Cada orden la resuelve una ``BattleSession`` (combate guardado o exhibición) en
un hilo; sus eventos pasan por el ``Director`` y los ``Beat`` resultantes los
reproduce ``BeatPlayer`` con un temporizador. Mientras se reproduce o se espera
a la sesión, la entrada queda bloqueada salvo Enter, que acelera. Krabby
también trabaja en hilos: los sprites se traen antes de montar el director, y
el director solo lee de esa caché.

Todo lo externo llega en ``BattleServices``: callables con valores por defecto
que importan ``composition`` de forma perezosa. Así los tests montan las
pantallas con sesiones guionizadas, sprites falsos y sin BD, HOME ni krabby.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from functools import lru_cache
from typing import Any, Protocol

from rich.markup import escape
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from pokedex_cli.application.battle import (
    EXHIBITION_LEVEL,
    LEGENDARY_LEVEL,
    STALE_REASON,
    Auto,
    BattleCommand,
    BattleSession,
    BattleStatus,
    Fight,
    Flee,
    Replace,
    Switch,
    ThrowBall,
    TurnOutcome,
)
from pokedex_cli.application.movesets import MovesetView
from pokedex_cli.domain.battle import engine
from pokedex_cli.domain.battle.model import (
    BallThrown,
    BattleEnded,
    BattleEvent,
    BattleKind,
    BattleState,
    Combatant,
    ExperienceGained,
    LeveledUp,
    MoveLearned,
    MoveSlot,
    Outcome,
    Phase,
    Side,
    UseMove,
)
from pokedex_cli.domain.progression import MAX_LEVEL, STARTING_LEVEL
from pokedex_cli.presentation import display
from pokedex_cli.presentation.battle_director import MESSAGE_DELAY, Beat, Director
from pokedex_cli.presentation.battle_scene import SceneModel, hp_bar, moves_panel, render_scene
from pokedex_cli.presentation.battle_text import (
    DAMAGE_CLASS_NAMES_ES,
    STATUS_BADGES,
    display_label,
    effectiveness_hint,
    messages_for,
    type_name,
)
from pokedex_cli.presentation.tui import presenter
from pokedex_cli.presentation.tui.app import (
    DEFAULT_ACCENT,
    DEX_OLIVE,
    DEX_PAPER,
    DEX_RULE,
    LCD_INK,
    LCD_PAPER,
    TYPE_HEX,
)
from pokedex_cli.presentation.tui.assets import POKEBALL_BRAILLE

# Ritmo de la reproducción, en segundos a velocidad ×1.
TICK = 1 / 30  # cada cuánto avanza el guion
TYPING_DELAY = 0.018  # por carácter escrito a máquina
SPINNER_INTERVAL = 0.12
SPINNER = "◐◓◑◒"
CURSOR = "▶"
CURSOR_HEX = "#d84030"
MUTED = presenter.TEXT_MUTED
DISABLED = "#8c7d5c"  # lo que ahora no se puede elegir
# Orden de GBA: LUCHAR y BOLSA arriba, POKÉMON y HUIR abajo.
COMMANDS: tuple[str, ...] = ("LUCHAR", "BOLSA", "POKÉMON", "HUIR")
FIGHT, BAG, PARTY, RUN = range(4)
CHANGED_ELSEWHERE = "Este combate ha cambiado desde otra terminal: vuelve a abrirlo con c."
TURN_FAILED = "No se pudo completar el turno. Inténtalo otra vez."
OPEN_FAILED = "No se pudo preparar el combate. Inténtalo otra vez."
NO_TEAM_NOTICE = "Tu equipo está vacío: elige tus Pokémon con «pokedex equipo add»."
EXHIBITION_NO_BAG = "En una exhibición no se usa la bolsa."
EXHIBITION_LEFT = "Dejas la exhibición."

ProgressCallback = Callable[[str], None]
SpriteFetcher = Callable[[str, str, bool], "str | None"]


# --- Servicios ----------------------------------------------------------------


class BattleChoice(str, Enum):
    RESUME = "resume"
    WILD = "wild"
    TRAINER = "trainer"
    EXHIBITION = "exhibition"


@dataclass(frozen=True)
class WildPreview:
    """El salvaje del terminal tal y como lo anuncia el lanzador."""

    name: str
    level: int | None
    shiny: bool = False

    @property
    def label(self) -> str:
        level = f" Nv. {self.level}" if self.level is not None else ""
        shiny = " ✨" if self.shiny else ""
        return f"Pokémon salvaje: {self.name}{level}{shiny}"


@dataclass(frozen=True)
class LauncherOptions:
    """Lo que se puede hacer ahora mismo (el lanzador solo ofrece eso)."""

    resume: str | None = None  # contra quién era el combate guardado
    wild: WildPreview | None = None
    trainer: bool = False
    exhibition: bool = False
    notice: str | None = None  # por qué no hay más opciones
    record: str | None = None

    def choices(self) -> list[tuple[BattleChoice, str]]:
        items: list[tuple[BattleChoice, str]] = []
        if self.resume:
            items.append((BattleChoice.RESUME, f"Reanudar el combate contra {self.resume}"))
        if self.wild is not None:
            items.append((BattleChoice.WILD, self.wild.label))
        if self.trainer:
            items.append((BattleChoice.TRAINER, "Entrenador: un rival a la altura de tu equipo"))
        if self.exhibition:
            items.append(
                (
                    BattleChoice.EXHIBITION,
                    f"Exhibición Nv. {EXHIBITION_LEVEL}: tu líder contra una especie al azar",
                )
            )
        return items


@dataclass(frozen=True)
class BagItem:
    """Una Poké Ball de la bolsa; ``stock`` None es ilimitada."""

    slug: str
    name: str
    multiplier: float
    stock: int | None

    @property
    def available(self) -> bool:
        return self.stock is None or self.stock > 0

    def spent(self) -> BagItem:
        return self if self.stock is None else replace(self, stock=max(0, self.stock - 1))

    def command(self) -> ThrowBall:
        return ThrowBall(self.slug, self.name, self.multiplier)


@dataclass(frozen=True)
class OpenedBattle:
    session: BattleSession
    notice: str | None = None  # se dice antes de seguir («lo retomamos»)


class BattleGateway(Protocol):
    """Lo que la TUI usa de ``composition`` (el módulo cumple este protocolo)."""

    def describe_battle(self) -> Any: ...
    def collection_queries(self) -> Any: ...
    def read_encounter(self) -> dict[str, Any] | None: ...
    def species_data(self) -> Any: ...
    def battle_record(self) -> dict[str, Any]: ...
    def prepare_battle(self, on_progress: ProgressCallback | None = None) -> Any: ...
    def start_battle(self) -> Any: ...
    def persisted_session(self, view: Any = None, *, opening: Sequence[Any] = ()) -> Any: ...
    def exhibition_session(
        self,
        left: str | None = None,
        right: str | None = None,
        level: int | None = None,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> Any: ...
    def sync_training(self, *, force_repo_scan: bool = False) -> Any: ...
    def ball_catalog(self) -> Mapping[str, Any]: ...
    def stock_count(self, current_inventory: dict[str, Any], slug: str) -> int | None: ...
    def manage_moveset(self) -> Any: ...
    def record_failure(self, context: str, error: BaseException) -> None: ...


def _composition() -> BattleGateway:
    from pokedex_cli import composition

    return composition


def default_options() -> LauncherOptions:
    return launcher_options(_composition())


def default_open_session(choice: BattleChoice, on_progress: ProgressCallback) -> OpenedBattle | str:
    return open_battle_session(choice, on_progress, _composition())


def default_bag() -> list[BagItem]:
    return bag_items(_composition())


def default_record() -> Mapping[str, Any]:
    return _composition().battle_record()


def default_moveset(capture_id: int) -> MovesetView | None:
    view: MovesetView | None = _composition().manage_moveset().view(str(capture_id))
    return view


def default_report(context: str, error: BaseException) -> None:
    _composition().record_failure(context, error)


@dataclass(frozen=True)
class BattleServices:
    """Dependencias de las pantallas de combate; los tests pasan dobles.

    ``speed`` divide todos los tiempos de la reproducción (×2 va el doble de
    rápido); Enter acelera además frase a frase.
    """

    options: Callable[[], LauncherOptions] = default_options
    open_session: Callable[[BattleChoice, ProgressCallback], OpenedBattle | str] = (
        default_open_session
    )
    bag: Callable[[], Sequence[BagItem]] = default_bag
    record: Callable[[], Mapping[str, Any]] = default_record
    moveset: Callable[[int], MovesetView | None] = default_moveset
    report: Callable[[str, BaseException], None] = default_report
    speed: float = 1.0


# --- Pegamento con la aplicación ------------------------------------------------


_STATUS_MESSAGES: dict[BattleStatus, str] = {
    BattleStatus.NO_TEAM: NO_TEAM_NOTICE,
    BattleStatus.NO_ENCOUNTER: "No hay ningún Pokémon salvaje a la vista. Abre una terminal nueva.",
    BattleStatus.ALREADY_CAPTURED: "Ya capturaste a ese Pokémon. Espera a que aparezca otro.",
    BattleStatus.NO_SPECIES_DATA: "Faltan los datos de esa especie. Prueba de nuevo con conexión.",
    BattleStatus.NO_TRAINER_POOL: (
        "Aún no hay especies en tu caché para formar un rival: sigue descubriendo Pokémon."
    ),
    BattleStatus.NO_CAPTURE: "No tienes esa captura.",
    BattleStatus.BATTLE_IN_PROGRESS: "Ya hay un combate a medias: reanúdalo primero.",
    BattleStatus.STALE: STALE_REASON,
    BattleStatus.NO_BATTLE: "Ya no hay ningún combate a medias.",
}


def status_message(status: BattleStatus) -> str:
    """Por qué no se pudo empezar o seguir, en español y listo para mostrar."""
    return _STATUS_MESSAGES.get(status, OPEN_FAILED)


def wild_level(data: Mapping[str, Any] | None) -> int | None:
    """Nivel con el que saldrá el salvaje: el mismo que usan la captura y el
    combate (``max(5, encuentro, 50 si legendario)``). Sin datos, ``None``."""
    if data is None:
        return None
    raw = data.get("encounter_level")
    level = raw if isinstance(raw, int) and not isinstance(raw, bool) else STARTING_LEVEL
    if data.get("is_legendary"):
        level = max(level, LEGENDARY_LEVEL)
    return max(STARTING_LEVEL, min(MAX_LEVEL, level))


def opponent_label(kind: BattleKind, state: BattleState, trainer: str | None) -> str:
    """Contra quién es el combate: «Pidgey salvaje», «Montañero Bruno»…"""
    if kind is BattleKind.TRAINER:
        return trainer or "un entrenador"
    return display_label(kind, "opponent", state.opponent.current.name)


def _count(record: Mapping[str, Any], key: str) -> int:
    try:
        return max(0, int(record.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _amount(record: Mapping[str, Any], key: str, noun: str) -> str:
    """«1 combate», «3 combates»: el número con su nombre en singular o plural."""
    count = _count(record, key)
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def record_line(record: Mapping[str, Any] | None, kind: BattleKind | None = None) -> str | None:
    """Resumen del récord; sin ``kind``, solo lo que ya tiene algo."""
    if not record:
        return None
    wild = (
        f"Salvajes: {_amount(record, 'wild_battles', 'combate')} · "
        f"{_amount(record, 'wild_wins', 'victoria')} · "
        f"{_amount(record, 'wild_captures', 'captura')}"
    )
    trainer = (
        f"Entrenadores: racha {_count(record, 'streak')} (mejor {_count(record, 'best_streak')})"
        f" · {_amount(record, 'trainer_wins', 'victoria')} · "
        f"{_amount(record, 'trainer_losses', 'derrota')}"
    )
    if kind is BattleKind.WILD:
        return wild
    if kind is BattleKind.TRAINER:
        return trainer
    if kind is not None:
        return None
    parts = []
    if _count(record, "wild_battles"):
        parts.append(wild)
    if _count(record, "trainer_wins") or _count(record, "trainer_losses"):
        parts.append(trainer)
    return "\n".join(parts) or None


def _wild_preview(gateway: BattleGateway) -> WildPreview | None:
    encounter = gateway.read_encounter()
    if not encounter or not encounter.get("species") or encounter.get("captured"):
        return None
    species = str(encounter["species"])
    form = str(encounter.get("form") or "regular")
    try:
        data = gateway.species_data().execute(species, form)
    except Exception:  # sin datos el lanzador anuncia el salvaje sin nivel
        data = None
    return WildPreview(
        display.display_name(species, form), wild_level(data), bool(encounter.get("shiny"))
    )


def launcher_options(gateway: BattleGateway) -> LauncherOptions:
    """Opciones posibles ahora: con un combate a medias solo se puede reanudar
    (o jugar una exhibición, que no guarda nada); sin equipo, nada."""
    try:
        record = record_line(gateway.battle_record())
    except Exception:  # el récord es decorativo
        record = None
    view = gateway.describe_battle().execute()
    team = gateway.collection_queries().team()
    if view is not None:
        resume = opponent_label(view.kind, view.state, view.trainer)
        return LauncherOptions(resume=resume, exhibition=bool(team), record=record)
    if not team:
        return LauncherOptions(notice=NO_TEAM_NOTICE, record=record)
    return LauncherOptions(
        wild=_wild_preview(gateway), trainer=True, exhibition=True, record=record
    )


def open_battle_session(
    choice: BattleChoice, on_progress: ProgressCallback, gateway: BattleGateway
) -> OpenedBattle | str:
    """Abre la sesión elegida o devuelve por qué no se puede.

    Salvaje y entrenador se preparan (puede haber red), se empiezan en la BD y
    se juegan sobre el combate guardado; si ya había uno a medias, se retoma.
    Si ``on_progress`` lanza (la pantalla se canceló), la excepción sube y el
    combate no llega a empezarse: el último aviso va justo antes de guardarlo.
    """
    if choice is BattleChoice.RESUME:
        on_progress("Recuperando el combate guardado…")
        session = gateway.persisted_session()
        return status_message(BattleStatus.NO_BATTLE) if session is None else OpenedBattle(session)
    if choice is BattleChoice.EXHIBITION:
        made = gateway.exhibition_session(None, None, EXHIBITION_LEVEL, on_progress=on_progress)
        return status_message(made) if isinstance(made, BattleStatus) else OpenedBattle(made)
    preparer = gateway.prepare_battle(on_progress)
    if choice is BattleChoice.WILD:
        setup = preparer.wild(gateway.read_encounter())
    else:
        setup = preparer.trainer()
    if isinstance(setup, BattleStatus):
        return status_message(setup)
    on_progress("¡Todo listo!")
    started: TurnOutcome = gateway.start_battle().execute(setup, now=datetime.now(timezone.utc))
    notice = None
    if started.status is BattleStatus.STARTED:
        session = gateway.persisted_session(opening=started.events)
    elif started.status is BattleStatus.BATTLE_IN_PROGRESS:
        session = gateway.persisted_session()
        notice = "Ya tenías un combate a medias: lo retomamos."
    else:
        return started.reason or status_message(started.status)
    if session is None:
        return status_message(BattleStatus.NO_BATTLE)
    return OpenedBattle(session, notice)


def bag_items(gateway: BattleGateway) -> list[BagItem]:
    """Poké Balls con su stock sobre el inventario sincronizado (como la CLI)."""
    activity, _training = gateway.sync_training()
    inventory = activity.inventory
    return [
        BagItem(
            ball.slug, ball.name, float(ball.multiplier), gateway.stock_count(inventory, ball.slug)
        )
        for ball in gateway.ball_catalog().values()
    ]


# --- Reproducción del guion ---------------------------------------------------


class BeatPlayer:
    """Reproduce beats contra un reloj que le dan desde fuera.

    Cambia de escena al empezar cada beat, escribe cada frase a máquina
    (``typing_delay`` por carácter) y respeta la espera de cada beat; ``speed``
    divide todos los tiempos. ``skip`` es Enter: termina de escribir la frase,
    corta su pausa o, en mitad de una animación, salta hasta la frase
    siguiente. Es puro (ni duerme ni pinta) para probarlo sin Textual.
    """

    def __init__(self, *, speed: float = 1.0, typing_delay: float = TYPING_DELAY) -> None:
        self.speed = max(0.01, speed)
        self._typing_delay = max(0.0, typing_delay)
        self._queue: deque[Beat] = deque()
        self._beat: Beat | None = None
        self._typed = 0.0
        self._wait = 0.0
        self.scene: SceneModel | None = None
        self.message: str | None = None
        self.previous: str | None = None
        # Todas las frases que han pasado por la caja, en orden.
        self.transcript: list[str] = []

    @property
    def playing(self) -> bool:
        return self._beat is not None

    @property
    def typing(self) -> bool:
        return (
            self._beat is not None
            and self._beat.message is not None
            and self.message is not None
            and self._typed < len(self.message)
        )

    @property
    def waiting(self) -> bool:
        """Frase ya escrita esperando su pausa: la caja enseña ▼."""
        return self._beat is not None and self._beat.message is not None and not self.typing

    @property
    def visible(self) -> str:
        """Lo que se ve de la frase actual."""
        if self.message is None:
            return ""
        if self.typing:
            return self.message[: int(self._typed)]
        return self.message

    def play(self, beats: Iterable[Beat]) -> None:
        self._queue.extend(beats)
        if self._beat is None:
            self._next()

    def show(self, message: str) -> None:
        """Pone una frase entera sin escribirla (el «¿Qué debería hacer…?»)."""
        if message != self.message:
            self.previous = self.message
            self.message = message
            self.transcript.append(message)

    def clear_text(self) -> None:
        self.message = None
        self.previous = None

    def advance(self, seconds: float) -> None:
        budget = max(0.0, seconds) * self.speed
        while self._beat is not None:
            if self.typing and self.message is not None:
                missing = len(self.message) - self._typed
                if self._typing_delay <= 0:
                    self._typed = float(len(self.message))
                else:
                    need = missing * self._typing_delay
                    if need > budget:
                        self._typed += budget / self._typing_delay
                        return
                    budget -= need
                    self._typed = float(len(self.message))
            if self._wait > budget:
                self._wait -= budget
                return
            budget -= self._wait
            self._next()

    def skip(self) -> None:
        beat = self._beat
        if beat is None:
            return
        if beat.message is not None:
            if self.typing and self.message is not None:
                self._typed = float(len(self.message))
            else:
                self._next()
            return
        while self._beat is not None and self._beat.message is None:
            self._next()

    def finish(self) -> None:
        """Aplica todo lo pendiente de golpe."""
        while self._beat is not None:
            self._next()

    def _next(self) -> None:
        if not self._queue:
            self._beat = None
            if self.message is not None:
                self._typed = float(len(self.message))
            return
        beat = self._queue.popleft()
        self._beat = beat
        self.scene = beat.scene
        self._wait = max(0.0, beat.delay)
        if beat.message is not None:
            self.previous = self.message
            self.message = beat.message
            self.transcript.append(beat.message)
            self._typed = 0.0


# --- Lógica de presentación -----------------------------------------------------


def grid_step(index: int, direction: str, count: int, columns: int = 2) -> int:
    """Mueve el cursor por una rejilla de ``count`` celdas en filas de ``columns``."""
    if count <= 0:
        return 0
    row, col = divmod(index, columns)
    if direction == "left":
        col = max(0, col - 1)
    elif direction == "right":
        col = min(columns - 1, col + 1)
    elif direction == "up":
        row = max(0, row - 1)
    elif direction == "down":
        row += 1
    target = row * columns + col
    return target if 0 <= target < count else index


def battle_identity(state: BattleState) -> tuple[object, ...]:
    """Lo que no cambia en todo un combate: si cambia, es otro combate."""
    return (
        state.kind,
        tuple((member.key, member.species, member.form) for member in state.opponent.team),
        tuple((member.key, member.capture_id) for member in state.player.team),
    )


def party_balls(side: Side) -> str:
    """Una bola por miembro: ● puede luchar, ○ debilitado."""
    return "".join("○" if member.fainted else "●" for member in side.team)


def kind_title(kind: BattleKind, trainer: str | None) -> str:
    if kind is BattleKind.TRAINER:
        return trainer or "Entrenador"
    if kind is BattleKind.WILD:
        return "Pokémon salvaje"
    return "Exhibición"


_OUTCOME_TITLES: dict[Outcome, str] = {
    Outcome.WON: "¡VICTORIA!",
    Outcome.LOST: "DERROTA",
    Outcome.ESCAPED: "HAS ESCAPADO",
    Outcome.CAPTURED: "¡CAPTURADO!",
    Outcome.OPPONENT_FLED: "HA HUIDO",
    Outcome.DRAW: "TABLAS",
}


PAUSED_NOTICE = "El combate queda en pausa: vuelve con c → Reanudar."


@dataclass(frozen=True)
class BattleSummary:
    """Lo que enseña el panel de resultado (y lo que recibe la Pokédex).

    ``notice`` es un aviso breve para quien abrió el combate (la Pokédex lo
    muestra al volver): por ejemplo, que el combate quedó en pausa.
    """

    finished: bool
    outcome: Outcome | None
    title: str
    lines: tuple[str, ...]
    notice: str | None = None


def summarize(
    events: Sequence[BattleEvent],
    *,
    state: BattleState | None,
    kind: BattleKind,
    trainer: str | None,
    capture_id: int | None = None,
    record: Mapping[str, Any] | None = None,
    reason: str | None = None,
) -> BattleSummary:
    """Resultado, EXP, niveles, movimientos aprendidos, captura y récord."""
    outcome = state.outcome if state is not None and state.finished else None
    lines: list[str] = [reason] if reason else []
    if outcome is not None and state is not None:
        opponent = display_label(kind, "opponent", state.opponent.current.name)
        if outcome is Outcome.CAPTURED:
            number = f" (captura #{capture_id})" if capture_id is not None else ""
            lines.append(f"¡{state.opponent.current.name} ya está en tu colección{number}!")
        elif outcome is Outcome.ESCAPED:
            lines.append("Escapaste sin problemas.")
        elif outcome is Outcome.OPPONENT_FLED:
            lines.append(f"¡{opponent} ha huido!")
        else:
            lines.extend(messages_for(BattleEnded(outcome), kind=kind, trainer=trainer))
    gained: dict[str, list[Any]] = {}
    levels: dict[str, tuple[str, int]] = {}
    learned: list[str] = []
    for event in events:
        if isinstance(event, ExperienceGained):
            gained.setdefault(event.key, [event.name, 0])[1] += event.amount
        elif isinstance(event, LeveledUp):
            levels[event.key] = (event.name, event.level)
        elif isinstance(event, MoveLearned):
            instead = f" en lugar de {event.forgot}" if event.forgot else ""
            learned.append(f"{event.name} aprendió {event.move_name}{instead}.")
    progress: list[str] = []
    for key, (name, amount) in gained.items():
        level = f" · ¡Nv. {levels[key][1]}!" if key in levels else ""
        progress.append(f"{name}: +{amount} EXP{level}")
    progress.extend(learned)
    if levels:
        progress.append("Si alguno puede evolucionar, lo hará al abrir tu próxima terminal.")
    if progress:
        lines.extend(["", *progress])
    summary_record = record_line(record, kind) if kind is not BattleKind.EXHIBITION else None
    if summary_record:
        lines.extend(["", summary_record])
    if outcome is not None:
        title = _OUTCOME_TITLES[outcome]
    elif reason:
        title = "COMBATE TERMINADO"
    else:
        title = "COMBATE EN PAUSA" if kind is not BattleKind.EXHIBITION else "EXHIBICIÓN"
    return BattleSummary(outcome is not None, outcome, title, tuple(lines))


def _ink_for(hex_color: str) -> str:
    """Tinta legible sobre un fondo de color (oscura sobre claros)."""
    value = hex_color.lstrip("#")
    try:
        red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return LCD_INK
    luminance = 0.299 * red + 0.587 * green + 0.114 * blue
    return LCD_INK if luminance > 150 else "#ffffff"


def type_chip(type_slug: str) -> Text:
    background = TYPE_HEX.get(type_slug, DEFAULT_ACCENT)
    return Text(f" {type_name(type_slug)} ", style=f"bold {_ink_for(background)} on {background}")


def status_chip(member: Combatant) -> Text:
    badge = STATUS_BADGES.get(member.status)
    if badge is None:
        return Text("")
    label, colour = badge
    return Text(f" {label} ", style=f"bold {_ink_for(colour)} on {colour}")


def command_menu(selected: int, enabled: Sequence[bool]) -> Text:
    """Menú 2×2 con el cursor ▶; las órdenes que no valen salen apagadas."""
    width = max(len(label) for label in COMMANDS) + 2
    text = Text(no_wrap=True)
    for index, label in enumerate(COMMANDS):
        if index == 2:
            text.append("\n")
        chosen = index == selected
        text.append(f"{CURSOR} " if chosen else "  ", style=f"bold {CURSOR_HEX}")
        style = f"bold {LCD_INK}" if enabled[index] else DISABLED
        text.append(label.ljust(width) if index % 2 == 0 else label, style=style)
    return text


def move_grid(slots: Sequence[MoveSlot], selected: int, defender_types: Sequence[str]) -> Text:
    """Las chapas 2×2 de ``moves_panel`` y la descripción del elegido."""
    panel = moves_panel(slots, selected, defender_types)
    chips = Text("\n").join(panel.split("\n")[:2])
    if slots:
        description = slots[max(0, min(len(slots) - 1, selected))].move.description
        if description:
            chips.append("\n")
            chips.append(
                Text(description, style=f"italic {MUTED}", no_wrap=True, overflow="ellipsis")
            )
    chips.no_wrap = True
    chips.overflow = "ellipsis"
    return chips


def move_detail(slot: MoveSlot, defender_types: Sequence[str]) -> Text:
    """Detalle del movimiento elegido: PP, tipo, categoría, potencia, precisión y eficacia."""
    move = slot.move
    if slot.pp <= 0:
        pp_colour = presenter.DARK_RED
    elif slot.pp * 4 <= slot.max_pp:
        pp_colour = presenter.DARK_AMBER
    else:
        pp_colour = LCD_INK
    power = "—" if move.power is None else str(move.power)
    accuracy = "—" if move.accuracy is None else str(move.accuracy)
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append("PP ", style=MUTED)
    text.append(f"{slot.pp}/{slot.max_pp}\n", style=f"bold {pp_colour}")
    text.append(
        f"{type_name(move.type)}",
        style=f"bold {presenter.TYPE_TEXT_COLORS.get(move.type, LCD_INK)}",
    )
    text.append(f" · {DAMAGE_CLASS_NAMES_ES.get(move.damage_class, 'Estado')}\n")
    text.append(f"Pot {power} · Prec {accuracy}")
    hint = effectiveness_hint(move, defender_types)
    if hint:
        colour = {
            "Muy eficaz": presenter.DARK_GREEN,
            "Poco eficaz": presenter.DARK_AMBER,
        }.get(hint, MUTED)
        text.append(f"\n{hint}", style=f"bold {colour}")
    return text


def message_text(previous: str | None, current: str, *, waiting: bool) -> Text:
    """Caja de texto: la frase anterior apagada encima de la que se escribe."""
    text = Text()
    if previous:
        text.append(previous, style=MUTED)
        text.append("\n")
    text.append(current, style=f"bold {LCD_INK}")
    if waiting:
        text.append(" ▼", style=f"bold {CURSOR_HEX}")
    return text


def party_prompt(
    member: Combatant, *, active: bool, enabled: bool = True, bar_width: int = 14
) -> Text:
    """Dos líneas por miembro del equipo: nombre, nivel, PS y estado; tipos.

    Quien no puede salir ahora (el que ya lucha o un debilitado) va apagado.
    """
    ink = LCD_INK if enabled else DISABLED
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(member.name, style=f"bold {ink}")
    text.append_text(Text.from_markup(presenter.gender_suffix(member.gender)))
    if member.shiny:
        text.append(" ★", style=f"bold {presenter.DARK_AMBER}")
    text.append(f"  Nv.{member.level}  ", style=ink)
    text.append_text(hp_bar(member.hp, member.max_hp, bar_width))
    text.append(f" {max(0, member.hp):>3}/{member.max_hp:<3} ", style=f"bold {ink}")
    text.append_text(status_chip(member))
    text.append("\n   ")
    for type_slug in member.types:
        text.append_text(type_chip(type_slug))
        text.append(" ")
    if member.fainted:
        text.append(" debilitado", style=f"italic {presenter.DARK_RED}")
    elif active:
        text.append(" en combate", style=f"italic {presenter.DARK_GREEN}")
    return text


def bag_prompt(item: BagItem) -> Text:
    stock = "∞" if item.stock is None else str(item.stock)
    if item.multiplier >= 255:
        effect = "captura garantizada"
    else:
        effect = f"captura ×{item.multiplier:g}".replace(".", ",")
    text = Text(no_wrap=True)
    text.append(f"{item.name:<11}", style=f"bold {LCD_INK}")
    text.append(f" ×{stock:<3}", style=f"bold {LCD_INK}" if item.available else MUTED)
    text.append(f" {effect}" if item.available else " sin existencias", style=MUTED)
    return text


def moveset_markup(view: MovesetView | None, *, loading: bool = False) -> list[str]:
    """Sección «MOVIMIENTOS» de la ficha de un individuo (markup de rich)."""
    header = f"[bold {presenter.SECTION_TEXT}]MOVIMIENTOS[/]"
    if loading:
        return [f"{header} [{MUTED}]cargando…[/]"]
    if view is None or not view.moves:
        return [header, f"[{MUTED}]Sin datos de movimientos por ahora.[/]"]
    mode = "elegidos por ti" if view.custom else "automáticos"
    # Tabla compacta (48 columnas): cabe en la ficha sin partir líneas.
    lines = [
        f"{header} [{MUTED}]{mode}[/]",
        f"{'':<36}[{MUTED}]Pot Prec  PP[/]",
    ]
    for move in view.moves:
        colour = presenter.TYPE_TEXT_COLORS.get(move.type, presenter.TEXT_PRIMARY)
        power = "—" if move.power is None else str(move.power)
        accuracy = "—" if move.accuracy is None else str(move.accuracy)
        category = DAMAGE_CLASS_NAMES_ES.get(move.damage_class, "Estado")
        name = escape(_clip(move.name, 15))
        lines.append(
            f" [bold]{name:<15}[/] [{colour}]{type_name(move.type):<9}[/]"
            f" [{presenter.TEXT_SECONDARY}]{category:<8}[/]"
            f" {power:>3} {accuracy:>4} {move.pp:>3}"
        )
    upcoming = next((entry for entry in view.upcoming if entry.supported), None)
    if upcoming is not None and upcoming.move is not None:
        lines.append(f"[{MUTED}]Próximo: {escape(upcoming.move.name)} al Nv. {upcoming.level}[/]")
    return lines


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


@lru_cache(maxsize=1)
def _loading_art() -> tuple[Text, int]:
    """La Poké Ball del arranque con todas sus filas del mismo ancho (para
    centrarla sin deformarla) y ese ancho."""
    lines = [Text.from_markup(line) for line in POKEBALL_BRAILLE]
    width = max(line.cell_len for line in lines)
    for line in lines:
        line.pad_right(width - line.cell_len)
    return Text("\n").join(lines), width


# --- Paneles modales ------------------------------------------------------------


def _panel_css(name: str, width: int = 66) -> str:
    return f"""
    {name} {{ align: center middle; background: #17140c 60%; }}
    {name} > Vertical {{
        width: {width};
        max-width: 96%;
        height: auto;
        max-height: 90%;
        background: {DEX_PAPER};
        color: {LCD_INK};
        border: double {DEX_RULE};
        border-title-color: {DEX_RULE};
        border-subtitle-color: {DEX_RULE};
        padding: 0 1;
    }}
    {name} OptionList {{
        height: auto;
        max-height: 20;
        border: none;
        padding: 0;
        background: {DEX_PAPER};
        color: {LCD_INK};
        scrollbar-color: {DEX_OLIVE};
        scrollbar-background: #c4b99d;
    }}
    {name} OptionList:focus {{ border: none; background-tint: transparent; }}
    /* Con :focus para ganar a la regla de serie de OptionList, como la lista. */
    {name} OptionList > .option-list--option-highlighted,
    {name} OptionList:focus > .option-list--option-highlighted {{
        background: {DEX_OLIVE};
        color: #111108;
        text-style: bold;
    }}
    {name} OptionList > .option-list--option-hover {{ background: #d3c9ab; }}
    {name} OptionList > .option-list--option-disabled {{ color: {DISABLED}; }}
    {name} .nota {{ color: {presenter.TEXT_SECONDARY}; height: auto; }}
    """


def _first_enabled(options: Sequence[Option]) -> int | None:
    return next((index for index, option in enumerate(options) if not option.disabled), None)


def _dismiss_once(panel: ModalScreen[Any], result: Any) -> None:
    """Cierra el panel con ``result`` solo si sigue siendo la pantalla activa.

    Con teclas encoladas (dos Enter seguidos, una tecla apretada) llega otra
    selección cuando el panel ya se ha cerrado, y ``dismiss`` de Textual
    sacaría entonces la pantalla de debajo: el combate, o la Pokédex entera
    con ``ScreenStackError``.
    """
    if panel.is_active:
        panel.dismiss(result)


class BattleLauncher(ModalScreen[BattleChoice | None]):
    """Lanzador de la tecla ``c``: solo ofrece lo que se puede hacer ahora."""

    DEFAULT_CSS = _panel_css("BattleLauncher")
    BINDINGS = [Binding("escape,q,x", "close", "Volver", show=False)]

    def __init__(self, services: BattleServices) -> None:
        super().__init__()
        self._services = services
        self.options: LauncherOptions | None = None
        self._app_ref: App[Any] | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="lanzador"):
            yield Static("Consultando el campo…", id="lanzador-estado", classes="nota")
            yield OptionList(id="lanzador-lista")
            yield Static("", id="lanzador-nota", classes="nota")

    def on_mount(self) -> None:
        self._app_ref = self.app
        box = self.query_one("#lanzador")
        box.border_title = "◓ COMBATE"
        box.border_subtitle = "Enter elegir · Esc volver"
        self.query_one("#lanzador-lista").display = False
        self._load()

    @work(thread=True, exclusive=True, group="lanzador", exit_on_error=False)
    def _load(self) -> None:
        try:
            options = self._services.options()
        except Exception as error:
            _safe_report(self._services, "TUI battle launcher", error)
            options = LauncherOptions(notice="No se pudo consultar el combate. Inténtalo otra vez.")
        _call_from_thread(self._app_ref, self, self._loaded, options)

    def _loaded(self, options: LauncherOptions) -> None:
        self.options = options
        choices = options.choices()
        status = self.query_one("#lanzador-estado", Static)
        status.update("¿Contra quién quieres combatir?" if choices else "")
        status.display = bool(choices)
        notes = [line for line in (options.notice, options.record) if line]
        self.query_one("#lanzador-nota", Static).update("\n".join(notes))
        option_list = self.query_one("#lanzador-lista", OptionList)
        option_list.clear_options()
        option_list.add_options([Option(label, id=choice.value) for choice, label in choices])
        option_list.display = bool(choices)
        if choices:
            option_list.highlighted = 0
            option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id is not None:
            _dismiss_once(self, BattleChoice(event.option.id))

    def action_close(self) -> None:
        _dismiss_once(self, None)


class PartyPanel(ModalScreen[BattleCommand | None]):
    """El equipo con sus PS. Devuelve ``Switch``, ``Replace`` (relevo
    obligatorio) o ``Auto``; ``None`` si se vuelve sin elegir."""

    DEFAULT_CSS = _panel_css("PartyPanel", width=72)
    BINDINGS = [
        Binding("escape,q,x", "close", "Volver", show=False),
        Binding("a", "auto", "Automático", show=False),
    ]

    def __init__(self, state: BattleState, *, forced: bool) -> None:
        super().__init__()
        self._state = state
        self._forced = forced

    def compose(self) -> ComposeResult:
        team = self._state.player.team
        targets = set(engine.switch_targets(self._state, "player"))
        options = [
            Option(
                party_prompt(
                    member, active=index == self._state.player.active, enabled=index in targets
                ),
                id=str(index),
                disabled=index not in targets,
            )
            for index, member in enumerate(team)
        ]
        with Vertical(id="equipo"):
            yield OptionList(*options, id="equipo-lista")
            note = "" if targets else "No hay otro Pokémon en condiciones de luchar."
            yield Static(note, id="equipo-nota", classes="nota")

    def on_mount(self) -> None:
        box = self.query_one("#equipo")
        box.border_title = "◓ ¿A QUIÉN SACAS?" if self._forced else "◓ TU EQUIPO"
        # «a» solo elige por ti el relevo obligatorio (``action_auto``).
        auto = " · a automático" if self._forced else ""
        box.border_subtitle = f"Enter sacar{auto} · Esc volver"
        option_list = self.query_one("#equipo-lista", OptionList)
        option_list.highlighted = _first_enabled(option_list.options)
        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id is None:
            return
        index = int(event.option.id)
        _dismiss_once(self, Replace(index) if self._forced else Switch(index))

    def action_auto(self) -> None:
        if self._forced:
            _dismiss_once(self, Auto())

    def action_close(self) -> None:
        _dismiss_once(self, None)


class BagPanel(ModalScreen[BagItem | None]):
    """La bolsa con el stock de cada Poké Ball; se puede abrir aún cargando."""

    DEFAULT_CSS = _panel_css("BagPanel", width=54)
    BINDINGS = [Binding("escape,q,x", "close", "Volver", show=False)]

    def __init__(self, items: Sequence[BagItem] | None) -> None:
        super().__init__()
        self._items: tuple[BagItem, ...] | None = None if items is None else tuple(items)

    def compose(self) -> ComposeResult:
        with Vertical(id="bolsa"):
            yield OptionList(id="bolsa-lista")
            yield Static("", id="bolsa-nota", classes="nota")

    def on_mount(self) -> None:
        box = self.query_one("#bolsa")
        box.border_title = "◓ BOLSA"
        box.border_subtitle = "Enter lanzar · Esc volver"
        self._fill()

    def show_items(self, items: Sequence[BagItem] | None, *, error: str | None = None) -> None:
        self._items = None if items is None else tuple(items)
        self._fill(error=error)

    def _fill(self, *, error: str | None = None) -> None:
        option_list = self.query_one("#bolsa-lista", OptionList)
        note = self.query_one("#bolsa-nota", Static)
        option_list.clear_options()
        if self._items is None:
            note.update(error or "Abriendo la bolsa…")
            option_list.display = False
            return
        option_list.add_options(
            [
                Option(bag_prompt(item), id=item.slug, disabled=not item.available)
                for item in self._items
            ]
        )
        option_list.display = bool(self._items)
        note.update("" if self._items else "La bolsa está vacía.")
        option_list.highlighted = _first_enabled(option_list.options)
        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        chosen = next((item for item in self._items or () if item.slug == event.option.id), None)
        if chosen is not None and chosen.available:
            _dismiss_once(self, chosen)

    def action_close(self) -> None:
        _dismiss_once(self, None)


class ResultPanel(ModalScreen[None]):
    """Resultado del combate encima de la última escena.

    Se cierra con Esc (q, x), no con Enter, espacio ni z: con esas teclas se
    acelera el guion y las pulsaciones de más lo cerrarían sin leerlo.
    """

    DEFAULT_CSS = _panel_css("ResultPanel", width=64)
    BINDINGS = [Binding("escape,q,x", "close", "Volver", show=False)]

    def __init__(self, summary: BattleSummary) -> None:
        super().__init__()
        self.summary = summary

    def compose(self) -> ComposeResult:
        with Vertical(id="resultado"):
            yield Static(self._body(), id="resultado-texto")

    def _body(self) -> Text:
        text = Text()
        for index, line in enumerate(self.summary.lines):
            if index:
                text.append("\n")
            text.append(line, style=f"bold {LCD_INK}" if index == 0 else LCD_INK)
        return text

    def on_mount(self) -> None:
        box = self.query_one("#resultado")
        box.border_title = f"◓ {self.summary.title}"
        box.border_subtitle = "Esc volver a la Pokédex"

    def action_close(self) -> None:
        _dismiss_once(self, None)


# --- Pantalla de combate --------------------------------------------------------


class _Mode(str, Enum):
    LOADING = "loading"  # preparando el combate
    BUSY = "busy"  # esperando a la sesión
    PLAYING = "playing"  # reproduciendo el guion
    MAIN = "main"  # menú de órdenes
    MOVES = "moves"  # eligiendo movimiento
    REPLACE = "replace"  # hay que sacar a otro Pokémon
    FINISHED = "finished"  # panel de resultado
    FAILED = "failed"  # no se pudo abrir


class _SpriteStore:
    """Sprites de krabby ya traídos (en un hilo); el director solo lee de aquí."""

    def __init__(self, fetch: SpriteFetcher) -> None:
        self._fetch = fetch
        self._sprites: dict[tuple[str, str, bool], str | None] = {}

    def preload(self, state: BattleState) -> None:
        for member in (*state.player.team, *state.opponent.team):
            key = (member.species, member.form, member.shiny)
            if key in self._sprites:
                continue
            try:
                self._sprites[key] = self._fetch(*key)
            except Exception:  # el sprite es decorativo: sin él sale el sustituto
                self._sprites[key] = None

    def lookup(self, species: str, form: str, shiny: bool) -> str | None:
        return self._sprites.get((species, form, shiny))


class _Cancelled(Exception):
    """Se cerró la pantalla mientras se preparaba el combate: ya no se empieza."""


def _safe_report(services: BattleServices, context: str, error: BaseException) -> None:
    try:
        services.report(context, error)
    except Exception:  # registrar un fallo nunca debe romper la TUI
        pass


def _call_from_thread(
    app: App[Any] | None, screen: Any, callback: Callable[..., Any], *args: Any
) -> None:
    """Vuelve al hilo de la app; si la pantalla ya se cerró, no hace nada."""
    if app is None:
        return

    def guarded() -> None:
        if getattr(screen, "_dismissed", False) or not screen.is_attached:
            return
        try:
            callback(*args)
        except NoMatches:  # la pantalla se está desmontando
            pass

    try:
        app.call_from_thread(guarded)
    except RuntimeError:  # la app ya ha terminado
        pass


class BattleScreen(ModalScreen[BattleSummary]):
    """Combate estilo GBA: campo animado, caja de texto y menú de órdenes."""

    DEFAULT_CSS = f"""
    BattleScreen {{ background: #17140c; overflow: hidden hidden; }}
    BattleScreen #combate-carcasa {{
        height: 100%;
        border-top: thick #f6a700;
        border-left: thick #f6a700;
        border-bottom: thick #9d4f00;
        border-right: thick #9d4f00;
        background: {DEX_OLIVE};
    }}
    BattleScreen #combate-cabecera {{
        height: 1;
        padding: 0 1;
        background: {DEX_OLIVE};
        color: {LCD_INK};
    }}
    BattleScreen #combate-campo {{
        height: 1fr;
        margin: 0 1;
        border: double {DEX_RULE};
        border-title-color: {DEX_RULE};
        border-subtitle-color: {DEX_RULE};
        background: {DEX_PAPER};
    }}
    BattleScreen #combate-escena {{
        width: 1fr;
        height: 1fr;
        background: {DEX_PAPER};
        content-align: center middle;
    }}
    BattleScreen #combate-consola {{ height: 6; margin: 0 1; }}
    BattleScreen #combate-mensaje {{
        width: 1fr;
        height: 100%;
        border: double {DEX_RULE};
        border-title-color: {DEX_RULE};
        background: {LCD_PAPER};
        color: {LCD_INK};
        padding: 0 1;
    }}
    BattleScreen #combate-menu {{
        width: 34;
        height: 100%;
        border: double {DEX_RULE};
        border-title-color: {DEX_RULE};
        background: {DEX_PAPER};
        color: {LCD_INK};
        padding: 0 1;
        content-align: left middle;
    }}
    BattleScreen #combate-pie {{
        height: 1;
        padding: 0 1;
        background: {DEX_OLIVE};
        color: {LCD_INK};
        content-align: center middle;
    }}
    """

    BINDINGS = [
        Binding("enter,z,space", "confirm", "Elegir", show=False),
        Binding("escape,x,q,backspace", "back", "Atrás", show=False),
        Binding("up,k", "cursor('up')", show=False),
        Binding("down,j", "cursor('down')", show=False),
        Binding("left,h", "cursor('left')", show=False),
        Binding("right,l", "cursor('right')", show=False),
        Binding("1", "pick(0)", show=False),
        Binding("2", "pick(1)", show=False),
        Binding("3", "pick(2)", show=False),
        Binding("4", "pick(3)", show=False),
        Binding("a", "auto", show=False),
    ]

    def __init__(
        self,
        choice: BattleChoice,
        *,
        services: BattleServices,
        sprites: SpriteFetcher,
    ) -> None:
        super().__init__()
        self._choice = choice
        self._services = services
        self._store = _SpriteStore(sprites)
        self._player = BeatPlayer(speed=services.speed)
        self._app_ref: App[Any] | None = None
        self._session: BattleSession | None = None
        self._state: BattleState | None = None  # lo que enseña la pantalla
        self._director: Director | None = None
        self._bag: tuple[BagItem, ...] | None = None
        self._bag_panel: BagPanel | None = None
        self._mode = _Mode.LOADING
        self._cursor = FIGHT
        self._move_cursor = 0
        self._origin = _Mode.MAIN
        self._before: BattleState | None = None
        self._then: Callable[[], None] | None = None
        self._ticker: Timer | None = None
        self._spinner: Timer | None = None
        self._spin_frame = 0
        self._last_tick = 0.0
        self._progress = "Preparando el combate…"
        self._scene_size: tuple[int, int] | None = None
        self._relayout_pending = False
        self._painted_scene: SceneModel | None = None
        self._painted_text: Text | None = None
        self._events: list[BattleEvent] = []
        self._capture_id: int | None = None
        self._record: Mapping[str, Any] | None = None
        self._ended_reason: str | None = None
        self._failure: str | None = None
        self._dismissed = False
        self.summary: BattleSummary | None = None
        self.busy_ticks = 0

    # --- composición -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="combate-carcasa"):
            yield Static("", id="combate-cabecera")
            with Vertical(id="combate-campo"):
                yield Static("", id="combate-escena")
            with Horizontal(id="combate-consola"):
                yield Static("", id="combate-mensaje")
                yield Static("", id="combate-menu")
            yield Static("", id="combate-pie")

    def on_mount(self) -> None:
        self._app_ref = self.app
        self.query_one("#combate-campo").border_title = "◓ COMBATE"
        self._spinner = self.set_interval(SPINNER_INTERVAL, self._spin)
        self._enter(_Mode.LOADING)
        self.call_after_refresh(self._relayout)
        self._open()

    def on_resize(self, event: events.Resize) -> None:
        self.call_after_refresh(self._relayout)

    # --- estado visible ----------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode.value

    @property
    def state(self) -> BattleState | None:
        return self._state

    @property
    def scene(self) -> SceneModel | None:
        return self._painted_scene

    @property
    def transcript(self) -> tuple[str, ...]:
        """Frases que han pasado por la caja de texto, en orden."""
        return tuple(self._player.transcript)

    def field_size(self) -> tuple[int, int]:
        size = self.query_one("#combate-escena", Static).content_size
        return max(1, size.width), max(1, size.height)

    # --- apertura ----------------------------------------------------------------

    @work(thread=True, exclusive=True, group="combate-abrir", exit_on_error=False)
    def _open(self) -> None:
        def show(message: str) -> None:
            _call_from_thread(self._app_ref, self, self._show_progress, message)

        def progress(message: str) -> None:
            # Esc («cancelar») mientras se prepara: el siguiente aviso corta la
            # preparación, y ``open_battle_session`` avisa justo antes de
            # guardar el combate, así que ya no se empieza.
            if self._dismissed:
                raise _Cancelled
            show(message)

        try:
            opened = self._services.open_session(self._choice, progress)
            bag: tuple[BagItem, ...] | None = None
            if isinstance(opened, OpenedBattle) and not self._dismissed:
                show("Buscando a los Pokémon…")
                self._store.preload(opened.session.state)
                if opened.session.kind is BattleKind.WILD:
                    show("Revisando la bolsa…")
                    bag = self._fetch_bag()
        except _Cancelled:
            return
        except Exception as error:
            _safe_report(self._services, "TUI battle open", error)
            _call_from_thread(self._app_ref, self, self._open_failed, OPEN_FAILED)
            return
        if isinstance(opened, str):
            _call_from_thread(self._app_ref, self, self._open_failed, opened)
            return
        # Sin la guarda de ``_call_from_thread``: si se canceló, hay que avisar.
        app = self._app_ref
        if app is not None:
            try:
                app.call_from_thread(self._opened, opened, bag)
            except RuntimeError:  # la app ya ha terminado
                pass

    def _opened(self, opened: OpenedBattle, bag: tuple[BagItem, ...] | None) -> None:
        """Ya en el hilo de la app: se juega o, si se canceló tarde, se avisa."""
        if not self._dismissed and self.is_attached:
            self._battle_ready(opened, bag)
            return
        # Esc llegó con el combate ya empezado: está guardado (en pausa) y la
        # pantalla ya no está para decirlo.
        if self._app_ref is not None and opened.session.kind is not BattleKind.EXHIBITION:
            self._app_ref.notify(PAUSED_NOTICE, timeout=4)

    def _fetch_bag(self) -> tuple[BagItem, ...] | None:
        try:
            return tuple(self._services.bag())
        except Exception as error:
            _safe_report(self._services, "TUI battle bag", error)
            return None

    def _show_progress(self, message: str) -> None:
        self._progress = message
        if self._mode is _Mode.LOADING:
            self._paint_loading()

    def _open_failed(self, message: str) -> None:
        self._failure = message
        self._player.clear_text()
        self._player.show(message)
        self._enter(_Mode.FAILED)
        self._paint_loading()

    def _battle_ready(
        self, opened: OpenedBattle, bag: tuple[BagItem, ...] | None, attempts: int = 0
    ) -> None:
        width, height = self.field_size()
        if (width < 8 or height < 4) and attempts < 5:
            # Aún sin maquetar: el director necesita el tamaño real del campo.
            self.call_after_refresh(self._battle_ready, opened, bag, attempts + 1)
            return
        session = opened.session
        self._session = session
        self._state = session.state
        self._bag = bag
        director = self._new_director(session.state)
        self._update_titles()
        if session.opening:
            beats = director.direct(session.opening, kind=session.kind, trainer=session.trainer)
        else:
            label = opponent_label(session.kind, session.state, session.trainer)
            beats = [Beat(director.scene, f"¡Sigue el combate contra {label}!", MESSAGE_DELAY)]
        if opened.notice:
            beats.insert(
                0, Beat(beats[0].scene if beats else director.scene, opened.notice, MESSAGE_DELAY)
            )
        rest = director.scene_for(session.state)
        self._play([*beats, Beat(rest, None, 0.0)], then=self._next_step)

    # --- turnos ------------------------------------------------------------------

    def _send(self, command: BattleCommand, *, origin: _Mode) -> None:
        if self._session is None or self._state is None:
            return
        self._origin = origin
        self._before = self._state
        self.busy_ticks = 0
        self._enter(_Mode.BUSY)
        self._take(command)

    @work(thread=True, exclusive=True, group="combate-turno", exit_on_error=False)
    def _take(self, command: BattleCommand) -> None:
        session = self._session
        if session is None:
            return
        try:
            outcome = session.take(command)
        except Exception as error:
            _safe_report(self._services, "TUI battle turn", error)
            _call_from_thread(self._app_ref, self, self._turn_failed)
            return
        record = None
        if outcome.status is BattleStatus.FINISHED and session.kind is not BattleKind.EXHIBITION:
            try:
                record = self._services.record()
            except Exception as error:  # el récord es decorativo
                _safe_report(self._services, "TUI battle record", error)
        _call_from_thread(self._app_ref, self, self._turn_ready, command, outcome, record)

    def _turn_failed(self) -> None:
        # El turno se deshizo entero (una transacción): se puede reintentar. Se
        # vuelve al menú principal para que el aviso siga a la vista.
        origin = _Mode.MAIN if self._origin is _Mode.MOVES else self._origin
        self._say(TURN_FAILED, then=lambda: self._enter(origin))

    def _turn_ready(
        self, command: BattleCommand, outcome: TurnOutcome, record: Mapping[str, Any] | None
    ) -> None:
        session, director = self._session, self._director
        if session is None or director is None:
            return
        status = outcome.status
        if status in (BattleStatus.INVALID_ACTION, BattleStatus.NO_STOCK):
            if status is BattleStatus.NO_STOCK and isinstance(command, ThrowBall):
                self._set_stock(command.ball_slug, 0)
            reason = outcome.reason or "Ahora no se puede hacer eso."
            self._say(reason, then=lambda: self._enter(self._origin))
            return
        if outcome.state is None or status in (BattleStatus.STALE, BattleStatus.NO_BATTLE):
            self._ended_reason = outcome.reason or status_message(status)
            self._say(self._ended_reason, then=self._finish)
            return
        before = self._before or self._state
        if before is not None and battle_identity(outcome.state) != battle_identity(before):
            self._ended_reason = CHANGED_ELSEWHERE
            self._say(CHANGED_ELSEWHERE, then=self._finish)
            return
        self._events.extend(outcome.events)
        if outcome.capture_id is not None:
            self._capture_id = outcome.capture_id
        if record is not None:
            self._record = record
        if isinstance(command, ThrowBall) and any(
            isinstance(event, BallThrown) for event in outcome.events
        ):
            self._spend_ball(command.ball_slug)
        beats = director.direct(outcome.events, kind=session.kind, trainer=session.trainer)
        rest = director.scene_for(outcome.state)
        self._state = outcome.state
        self._update_titles()
        self._player.clear_text()
        self._play([*beats, Beat(rest, None, 0.0)], then=self._next_step)

    def _next_step(self) -> None:
        """Qué toca cuando el guion termina."""
        state = self._state
        if state is None:
            return
        if state.finished:
            self._finish()
        elif state.phase is Phase.CHOOSE_REPLACEMENT:
            self._enter(_Mode.REPLACE)
            self._open_party(forced=True)
        elif engine.locked_in(state, "player"):
            # Carga o recarga: el turno sigue solo, como en los juegos.
            self._send(Fight(0), origin=_Mode.MAIN)
        else:
            self._enter(_Mode.MAIN)

    # --- reproducción ------------------------------------------------------------

    def _play(self, beats: Sequence[Beat], *, then: Callable[[], None] | None) -> None:
        self._then = then
        self._enter(_Mode.PLAYING)
        self._player.play(beats)
        self._paint_player()
        if not self._player.playing:
            self._playback_done()
            return
        self._last_tick = time.monotonic()
        if self._ticker is None:
            self._ticker = self.set_interval(TICK, self._tick)

    def _say(self, message: str, *, then: Callable[[], None] | None) -> None:
        scene = self._player.scene or (self._director.scene if self._director else None)
        if scene is None:
            self._player.show(message)
            self._paint_message()
            if then is not None:
                then()
            return
        self._play([Beat(scene, message, MESSAGE_DELAY)], then=then)

    def _tick(self) -> None:
        now = time.monotonic()
        elapsed, self._last_tick = now - self._last_tick, now
        self._player.advance(elapsed)
        self._paint_player()
        if not self._player.playing:
            self._playback_done()

    def _playback_done(self) -> None:
        if self._ticker is not None:
            self._ticker.stop()
            self._ticker = None
        then, self._then = self._then, None
        if self._relayout_pending:
            self._relayout()
        if then is not None:
            then()

    # --- órdenes del jugador -------------------------------------------------------

    def action_confirm(self) -> None:
        if self._mode is _Mode.PLAYING:
            self._player.skip()
            self._paint_player()
        elif self._mode is _Mode.MAIN:
            self._choose_command(self._cursor)
        elif self._mode is _Mode.MOVES:
            self._choose_move(self._move_cursor)
        elif self._mode is _Mode.REPLACE:
            self._open_party(forced=True)
        elif self._mode is _Mode.FINISHED and self.summary is not None:
            self._show_result(self.summary)

    def action_back(self) -> None:
        if self._mode is _Mode.MOVES:
            self._enter(_Mode.MAIN)
        elif self._mode in (_Mode.MAIN, _Mode.REPLACE):
            self._pause()
        elif self._mode in (_Mode.LOADING, _Mode.FAILED, _Mode.FINISHED):
            self._close()

    def action_cursor(self, direction: str) -> None:
        if self._mode is _Mode.MAIN:
            self._cursor = grid_step(self._cursor, direction, len(COMMANDS))
            self._paint_menu()
        elif self._mode is _Mode.MOVES:
            self._move_cursor = grid_step(self._move_cursor, direction, len(self._slots()))
            self._paint_menu()
            self._paint_message()

    def action_pick(self, index: int) -> None:
        if self._mode is _Mode.MAIN:
            self._cursor = index
            self._choose_command(index)
        elif self._mode is _Mode.MOVES and index < len(self._slots()):
            self._move_cursor = index
            self._choose_move(index)
        elif self._mode is _Mode.REPLACE and index == PARTY:
            self._open_party(forced=True)

    def action_auto(self) -> None:
        if self._mode in (_Mode.MAIN, _Mode.MOVES):
            self._send(Auto(), origin=_Mode.MAIN)
        elif self._mode is _Mode.REPLACE:
            self._send(Auto(), origin=_Mode.REPLACE)

    def _slots(self) -> tuple[MoveSlot, ...]:
        return () if self._state is None else self._state.player.current.moves

    @property
    def _exhibition(self) -> bool:
        return self._session is not None and self._session.kind is BattleKind.EXHIBITION

    def _choose_command(self, index: int) -> None:
        if index == FIGHT:
            self._move_cursor = min(self._move_cursor, max(0, len(self._slots()) - 1))
            self._enter(_Mode.MOVES)
        elif index == BAG:
            if self._exhibition:
                self._say(EXHIBITION_NO_BAG, then=lambda: self._enter(_Mode.MAIN))
            else:
                self._open_bag()
        elif index == PARTY:
            self._open_party(forced=False)
        elif index == RUN:
            if self._exhibition:
                # En una exhibición no hay a quién huir: se sale de ella.
                self._say(EXHIBITION_LEFT, then=self._close)
            else:
                self._send(Flee(), origin=_Mode.MAIN)

    def _choose_move(self, slot: int) -> None:
        if self._state is None:
            return
        reason = engine.action_error(self._state, UseMove(slot))
        if reason is not None:
            self._say(reason, then=lambda: self._enter(_Mode.MOVES))
            return
        self._send(Fight(slot), origin=_Mode.MOVES)

    def _open_party(self, *, forced: bool) -> None:
        if self._state is None:
            return
        self.app.push_screen(PartyPanel(self._state, forced=forced), self._party_closed)

    def _party_closed(self, command: BattleCommand | None) -> None:
        if self._dismissed:
            return
        if command is None:
            self._enter(self._mode if self._mode is _Mode.REPLACE else _Mode.MAIN)
            return
        origin = _Mode.REPLACE if isinstance(command, (Replace, Auto)) else _Mode.MAIN
        self._send(command, origin=origin)

    def _open_bag(self) -> None:
        panel = BagPanel(self._bag)
        self._bag_panel = panel
        self.app.push_screen(panel, self._bag_closed)
        if self._bag is None:
            self._load_bag()

    @work(thread=True, exclusive=True, group="combate-bolsa", exit_on_error=False)
    def _load_bag(self) -> None:
        items = self._fetch_bag()
        _call_from_thread(self._app_ref, self, self._bag_loaded, items)

    def _bag_loaded(self, items: tuple[BagItem, ...] | None) -> None:
        if items is not None:
            self._bag = items
        panel = self._bag_panel
        if panel is not None and panel.is_attached:
            panel.show_items(
                items, error=None if items is not None else "No se pudo abrir la bolsa."
            )

    def _bag_closed(self, item: BagItem | None) -> None:
        self._bag_panel = None
        if self._dismissed:
            return
        if item is None:
            self._enter(_Mode.MAIN)
            return
        self._send(item.command(), origin=_Mode.MAIN)

    def _set_stock(self, slug: str, stock: int) -> None:
        if self._bag is not None:
            self._bag = tuple(
                replace(item, stock=stock) if item.slug == slug and item.stock is not None else item
                for item in self._bag
            )

    def _spend_ball(self, slug: str) -> None:
        if self._bag is not None:
            self._bag = tuple(item.spent() if item.slug == slug else item for item in self._bag)

    # --- final -----------------------------------------------------------------

    def _summary(self) -> BattleSummary:
        session = self._session
        if session is None:
            reason = self._failure or "El combate no llegó a empezar."
            return BattleSummary(False, None, "SIN COMBATE", (reason,))
        return summarize(
            self._events,
            state=self._state,
            kind=session.kind,
            trainer=session.trainer,
            capture_id=self._capture_id,
            record=self._record,
            reason=self._ended_reason,
        )

    def _finish(self) -> None:
        self._enter(_Mode.FINISHED)
        self.summary = self._summary()
        self._show_result(self.summary)

    def _show_result(self, summary: BattleSummary) -> None:
        self.app.push_screen(ResultPanel(summary), self._result_closed)

    def _result_closed(self, _: None) -> None:
        self._close()

    def _pause(self) -> None:
        summary = self._summary()
        session = self._session
        if session is not None and session.kind is not BattleKind.EXHIBITION:
            # El aviso lo da quien abrió el combate: un toast montado en esta
            # pantalla se perdería al cerrarla.
            summary = replace(summary, notice=PAUSED_NOTICE)
        self.summary = summary
        self._close()

    def _close(self) -> None:
        if self._dismissed:
            return
        summary = self.summary or self._summary()
        self.summary = summary
        self._dismissed = True
        if self._ticker is not None:
            self._ticker.stop()
            self._ticker = None
        self._stop_spinner()
        self.dismiss(summary)

    # --- dibujo ------------------------------------------------------------------

    def _new_director(self, state: BattleState) -> Director:
        width, height = self.field_size()
        self._scene_size = (width, height)
        director = Director(state, self._store.lookup, width, height)
        self._director = director
        self._paint_scene(director.scene)
        return director

    def _relayout(self) -> None:
        if self._dismissed:
            return
        if self._mode is _Mode.LOADING or self._state is None:
            self._paint_loading()
            return
        if self.field_size() == self._scene_size:
            return
        if self._player.playing:
            # Los beats ya calculados tienen el tamaño viejo: al terminar se rehace.
            self._relayout_pending = True
            return
        self._relayout_pending = False
        self._new_director(self._state)

    def _enter(self, mode: _Mode) -> None:
        self._mode = mode
        if mode in (_Mode.MAIN, _Mode.REPLACE) and self._state is not None:
            if mode is _Mode.MAIN:
                prompt = f"¿Qué debería hacer {self._state.player.current.name}?"
            else:
                prompt = "¿A qué Pokémon vas a sacar?"
                self._cursor = PARTY
            self._player.show(prompt)
        try:
            self._paint_menu()
            self._paint_message()
            self._paint_footer()
        except NoMatches:
            pass

    def _paint_scene(self, scene: SceneModel) -> None:
        self._painted_scene = scene
        self.query_one("#combate-escena", Static).update(render_scene(scene))

    def _paint_player(self) -> None:
        scene = self._player.scene
        if scene is not None and scene is not self._painted_scene:
            self._paint_scene(scene)
        self._paint_message()

    def _paint_message(self) -> None:
        box = self.query_one("#combate-mensaje", Static)
        if self._mode is _Mode.MOVES and self._state is not None:
            box.border_title = "◓ MOVIMIENTOS"
            text = move_grid(self._slots(), self._move_cursor, self._state.opponent.current.types)
        else:
            box.border_title = ""
            text = message_text(
                self._player.previous, self._player.visible, waiting=self._player.waiting
            )
        if text != self._painted_text:
            self._painted_text = text
            box.update(text)

    def _paint_menu(self) -> None:
        menu = self.query_one("#combate-menu", Static)
        visible = self._mode in (_Mode.MAIN, _Mode.MOVES, _Mode.REPLACE)
        menu.display = visible
        if not visible:
            return
        if self._mode is _Mode.MOVES:
            slots = self._slots()
            if not slots or self._state is None:
                menu.update("")
                return
            slot = slots[max(0, min(len(slots) - 1, self._move_cursor))]
            menu.styles.border = ("double", TYPE_HEX.get(slot.move.type, DEX_RULE))
            menu.border_title = type_name(slot.move.type).upper()
            menu.update(move_detail(slot, self._state.opponent.current.types))
            return
        menu.styles.border = ("double", DEX_RULE)
        menu.border_title = ""
        if self._mode is _Mode.REPLACE:
            enabled = [False, False, True, False]
        else:
            enabled = [True, not self._exhibition, True, True]
        menu.update(command_menu(self._cursor, enabled))

    def _paint_footer(self) -> None:
        leave = "Esc salir" if self._exhibition else "Esc pausar"
        hints = {
            _Mode.LOADING: "Esc cancelar",
            _Mode.BUSY: "Un momento" + "·" * (1 + self.busy_ticks % 3),
            _Mode.PLAYING: "Enter acelerar",
            _Mode.MAIN: f"←↑↓→ mover · Enter elegir · 1–4 directo · a automático · {leave}",
            _Mode.MOVES: "←↑↓→ mover · Enter usar · 1–4 directo · a automático · Esc volver",
            _Mode.REPLACE: f"Enter elegir relevo · a automático · {leave}",
            _Mode.FINISHED: "Esc volver a la Pokédex",
            _Mode.FAILED: "Esc volver a la Pokédex",
        }
        self.query_one("#combate-pie", Static).update(
            Text(hints[self._mode], style=presenter.TEXT_SECONDARY, justify="center")
        )

    def _update_titles(self) -> None:
        session, state = self._session, self._state
        if session is None or state is None:
            return
        field = self.query_one("#combate-campo")
        title = kind_title(session.kind, session.trainer)
        # Como en los juegos, las bolas del rival solo se ven contra un entrenador.
        balls = f"  {party_balls(state.opponent)}" if session.kind is BattleKind.TRAINER else ""
        field.border_title = f"◓ {title}{balls}"
        turn = f" · Turno {state.turn}" if state.turn else ""
        field.border_subtitle = f"Tu equipo {party_balls(state.player)}{turn}"
        header = Text.from_markup(
            "[#85ddff]◉[/][#28aafd]◉[/]  [red1]●[/][yellow1]●[/][green1]●[/]"
            f"   [bold {LCD_INK}]COMBATE[/]   [#352a18]{escape(title)}[/]"
        )
        self.query_one("#combate-cabecera", Static).update(header)

    def _paint_loading(self) -> None:
        """Poké Ball del arranque y lo que se está preparando (o por qué no se pudo)."""
        try:
            scene = self.query_one("#combate-escena", Static)
        except NoMatches:
            return
        width, height = self.field_size()
        text = Text(justify="center")
        art, art_width = _loading_art()
        if height >= len(POKEBALL_BRAILLE) + 3 and width >= art_width + 2:
            text.append_text(art)
            text.append("\n\n")
        if self._failure is not None:
            text.append(f"✖ {self._failure}", style=f"bold {presenter.DARK_RED}")
        else:
            spinner = SPINNER[self._spin_frame % len(SPINNER)]
            text.append(f"{spinner} {self._progress}", style=f"bold {LCD_INK}")
        self._painted_scene = None
        scene.update(text)

    def _spin(self) -> None:
        self._spin_frame += 1
        if self._mode is _Mode.LOADING:
            self._paint_loading()
        elif self._mode is _Mode.BUSY:
            self.busy_ticks += 1
            self._paint_footer()

    def _stop_spinner(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None
