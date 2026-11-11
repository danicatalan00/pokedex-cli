"""Director de animaciones del combate: eventos → guion de fotogramas.

Recibe los eventos semánticos de un turno y los convierte en ``Beat``: una
escena, el texto nuevo que hay que escribir a máquina (si lo hay) y cuánto
esperar después. Es puro y determinista: no duerme, no pinta y no usa azar,
así que el mismo turno produce siempre la misma película. La TUI reproduce los
beats a su ritmo; la CLI puede usarlos o quedarse solo con los mensajes, que
son exactamente los de ``battle_text.messages_for`` (con el evento siguiente
como ``upcoming``) y en el mismo orden (salvo el separador «— Turno N —», que
la caja de texto no escribe, como en los juegos).

El director recuerda la escena entre turnos (quién está en el campo, sus PS,
su estado…) a partir de los propios eventos, y ``scene_for`` la resincroniza
con el estado definitivo cuando el turno termina.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from pokedex_cli.domain.battle.model import (
    SIDES,
    BallThrown,
    BattleEnded,
    BattleEvent,
    BattleKind,
    BattleStarted,
    BattleState,
    Charging,
    Combatant,
    Confused,
    ConfusionCheck,
    DamageCause,
    DamageClass,
    Damaged,
    EscapeAttempted,
    ExperienceGained,
    Fainted,
    ForcedOut,
    HealCause,
    Healed,
    ImmobileReason,
    Immobilized,
    LeveledUp,
    MoveFailed,
    MoveMissed,
    MoveUsed,
    NoEffect,
    OpponentFled,
    Protected,
    ProtectionRaised,
    PumpedUp,
    Recalled,
    ScreenRaised,
    Seeded,
    SentOut,
    SideId,
    StatChanged,
    StatsReset,
    Status,
    StatusBlocked,
    StatusCured,
    StatusInflicted,
    Teleported,
    Trapped,
    TurnStarted,
    other_side,
)
from pokedex_cli.presentation.animation import BallAnimationStyle, _ball_style
from pokedex_cli.presentation.battle_scene import (
    FLASH_RGB,
    RGB,
    Box,
    Grid,
    Hud,
    Particle,
    SceneModel,
    SpriteLookup,
    experience_fraction,
    grid_size,
    layout,
    prepare_sprite,
    replace_side,
    scale_grid,
    scene_from_state,
    side_view,
    sprite_box,
    tint_grid,
)
from pokedex_cli.presentation.battle_text import messages_for

# Duraciones a velocidad ×1, en segundos.
FRAME_DELAY = 0.045  # fotograma de animación
BAR_DELAY = 0.03  # paso de una barra que baja o sube
MESSAGE_DELAY = 0.7  # pausa tras escribir una frase
LONG_MESSAGE_DELAY = 1.1  # frases que cierran algo (debilitado, fin)
WOBBLE_DELAY = 0.3  # cada bamboleo de la Poké Ball

Palette = tuple[RGB, RGB, RGB]  # oscuro, medio y claro: alguno contrasta con cualquier fondo


@dataclass(frozen=True)
class Beat:
    """Un paso del guion: escena a mostrar, frase nueva (o None) y espera."""

    scene: SceneModel
    message: str | None = None
    delay: float = FRAME_DELAY


@dataclass(frozen=True)
class _TypeFx:
    head: str  # glifo que acompaña al proyectil
    trail: str  # glifo de la estela
    palette: Palette
    path: str  # line, arc, wave, zigzag, spiral, rings, rise, fall, impact
    burst: str  # glifo del impacto
    size: float = 2.2  # radio del proyectil en píxeles de medio bloque


_TYPE_FX: dict[str, _TypeFx] = {
    "fire": _TypeFx("▲", "•", ((214, 52, 24), (255, 140, 40), (255, 214, 90)), "line", "▲", 2.8),
    "water": _TypeFx("○", "◦", ((40, 104, 216), (90, 160, 250), (170, 214, 255)), "wave", "○", 2.5),
    "electric": _TypeFx(
        "ϟ", "·", ((222, 170, 0), (255, 214, 40), (255, 246, 150)), "zigzag", "ϟ", 2.0
    ),
    "grass": _TypeFx("❦", "·", ((40, 140, 56), (90, 190, 80), (160, 226, 110)), "spiral", "❦", 1.4),
    "ice": _TypeFx("❄", "·", ((40, 150, 200), (100, 200, 232), (186, 238, 252)), "line", "❄", 2.0),
    "psychic": _TypeFx(
        "◎", "○", ((200, 48, 112), (248, 88, 136), (255, 160, 200)), "rings", "◎", 2.4
    ),
    "ghost": _TypeFx("∿", "∿", ((90, 60, 140), (130, 92, 182), (180, 146, 226)), "wave", "∿", 3.0),
    "dragon": _TypeFx("≈", "≈", ((80, 36, 200), (116, 64, 248), (164, 128, 255)), "wave", "≈", 2.7),
    "dark": _TypeFx("●", "•", ((52, 38, 60), (98, 72, 106), (150, 120, 160)), "line", "●", 2.7),
    "fairy": _TypeFx("✧", "·", ((214, 96, 150), (240, 148, 178), (255, 206, 226)), "arc", "✧", 1.7),
    "poison": _TypeFx("●", "°", ((118, 40, 128), (164, 70, 166), (208, 128, 210)), "arc", "●", 2.3),
    "ground": _TypeFx(
        "▓", "▒", ((146, 104, 44), (200, 160, 80), (232, 204, 128)), "rise", "▓", 2.0
    ),
    "rock": _TypeFx("◆", "▪", ((112, 100, 84), (160, 148, 128), (204, 194, 172)), "fall", "◆", 2.4),
    "flying": _TypeFx(
        "≫", "»", ((88, 150, 220), (140, 196, 248), (200, 230, 255)), "line", "≫", 1.8
    ),
    "bug": _TypeFx("✱", "·", ((120, 150, 16), (168, 200, 36), (210, 236, 96)), "line", "✱", 1.3),
    "fighting": _TypeFx(
        "✸", "✶", ((180, 40, 32), (228, 86, 52), (255, 170, 96)), "impact", "✸", 2.3
    ),
    "steel": _TypeFx(
        "◇", "·", ((120, 124, 150), (176, 178, 204), (226, 228, 240)), "line", "◇", 2.1
    ),
    "normal": _TypeFx(
        "✦", "·", ((150, 140, 110), (210, 196, 150), (255, 248, 210)), "impact", "✦", 1.9
    ),
}

# Glifo y paleta de cada problema de estado para su aura.
_STATUS_FX: dict[Status, tuple[str, Palette]] = {
    Status.BURN: ("▲", ((214, 52, 24), (255, 140, 40), (255, 214, 90))),
    Status.PARALYSIS: ("ϟ", ((222, 170, 0), (255, 214, 40), (255, 246, 150))),
    Status.POISON: ("●", ((118, 40, 128), (164, 70, 166), (208, 128, 210))),
    Status.TOXIC: ("●", ((84, 22, 98), (136, 48, 148), (188, 108, 198))),
    Status.SLEEP: ("z", ((96, 96, 132), (140, 140, 176), (196, 196, 220))),
    Status.FREEZE: ("❄", ((40, 150, 200), (100, 200, 232), (186, 238, 252))),
}

_SPARK: Palette = ((196, 136, 0), (240, 180, 30), (255, 226, 120))
_HEAL: Palette = ((40, 150, 80), (90, 210, 120), (190, 250, 200))
_RISE: Palette = ((200, 64, 40), (255, 112, 72), (255, 180, 150))
_FALL: Palette = ((40, 90, 200), (72, 140, 255), (160, 200, 255))
_SHIELD: Palette = ((40, 140, 190), (120, 210, 240), (230, 250, 255))
_MIST: Palette = ((110, 110, 124), (150, 150, 164), (200, 200, 210))
_WRAP: Palette = ((132, 88, 48), (176, 124, 72), (214, 168, 110))
_STARS: Palette = ((200, 150, 0), (240, 200, 40), (255, 240, 150))
_SCREEN_COLORS: dict[str, Palette] = {
    "reflect": ((70, 120, 220), (130, 180, 255), (210, 230, 255)),
    "light-screen": ((210, 160, 30), (255, 214, 110), (255, 244, 200)),
}

# Movimientos de dos turnos que sacan al usuario del campo durante la carga.
_ASCEND = frozenset({"fly", "bounce", "sky-drop"})
_BURROW = frozenset({"dig", "dive"})
_VANISH = frozenset({"phantom-force", "shadow-force"})
_ABSORB_LIGHT = frozenset({"solar-beam", "solar-blade", "meteor-beam"})

# Tras estos eventos el movimiento no llega a animarse (como en los juegos).
_NO_MOVE_ANIMATION = (MoveMissed, Protected, NoEffect, MoveFailed, Charging, StatusBlocked)


@dataclass
class _Member:
    """Lo que el director sabe de un Pokémon; se actualiza con los eventos."""

    name: str
    species: str
    form: str
    shiny: bool
    level: int
    hp: int
    max_hp: int
    status: Status
    gender: str | None
    experience: int
    growth_rate: str

    @classmethod
    def of(cls, combatant: Combatant) -> _Member:
        return cls(
            name=combatant.name,
            species=combatant.species,
            form=combatant.form,
            shiny=combatant.shiny,
            level=combatant.level,
            hp=max(0, combatant.hp),
            max_hp=max(1, combatant.max_hp),
            status=combatant.status,
            gender=combatant.gender,
            experience=combatant.experience,
            growth_rate=combatant.growth_rate,
        )


@dataclass(frozen=True)
class _Cue:
    kind: BattleKind
    previous: BattleEvent | None
    upcoming: BattleEvent | None


class Director:
    """Convierte eventos en beats y mantiene la escena entre turnos."""

    def __init__(self, state: BattleState, sprites: SpriteLookup, width: int, height: int) -> None:
        self.width = max(1, width)
        self.height = max(1, height)
        self._sprites = sprites
        self._layout = layout(self.width, self.height)
        self._raw: dict[tuple[str, str, bool], str | None] = {}
        self._grids: dict[tuple[str, str, bool, SideId], Grid] = {}
        self._members: dict[str, _Member] = {}
        self._active: dict[SideId, str | None] = {"player": None, "opponent": None}
        self._canonical: dict[SideId, Grid] = {"player": [], "opponent": []}
        self._shown: dict[SideId, bool] = {"player": False, "opponent": False}
        self._hud_on: dict[SideId, bool] = {"player": False, "opponent": False}
        self._airborne: set[SideId] = set()
        self._departed: set[SideId] = set()
        self._award = state.award_experience
        self._beats: list[Beat] = []
        self.scene = SceneModel(self.width, self.height)
        self.scene_for(state)

    # --- API ------------------------------------------------------------

    def scene_for(self, state: BattleState) -> SceneModel:
        """Resincroniza con ``state`` y devuelve su escena en reposo.

        Tras una huida, captura o expulsión que acaba el combate, el Pokémon
        que se fue sigue fuera aunque el estado no lo diga, y el que se quedó
        sigue en el campo aunque el resultado sugiera otra cosa (un Rugido en
        salvaje puede acabar en ``ESCAPED`` aunque el que se va sea el rival).
        """
        self._award = state.award_experience
        if not state.finished:
            self._departed.clear()
        # Si el director vio quién se fue, eso manda sobre lo que el resultado
        # deja adivinar a ``scene_from_state``.
        basis = replace(state, outcome=None) if self._departed else state
        base = scene_from_state(basis, self._lookup, self.width, self.height)
        self._members = {
            member.key: _Member.of(member) for side in SIDES for member in state.side(side).team
        }
        self._airborne = set()
        for side in SIDES:
            current = state.side(side).current
            view = side_view(base, side)
            self._active[side] = current.key
            # La misma rejilla de la caché: así la escena no cambia sin motivo.
            self._canonical[side] = self._sprite(side, current.species, current.form, current.shiny)
            self._shown[side] = view.visible and side not in self._departed
            self._hud_on[side] = view.hud is not None and side not in self._departed
            if current.volatile.semi_invulnerable and not current.fainted:
                self._airborne.add(side)
        self.scene = self._rest()
        return self.scene

    def direct(
        self, events: Sequence[BattleEvent], *, kind: BattleKind, trainer: str | None
    ) -> list[Beat]:
        """Guion animado de ``events``. Acepta cualquier evento del modelo: los
        que no tienen animación propia solo escriben su frase."""
        self._beats = []
        sequence = list(events)
        for index, event in enumerate(sequence):
            cue = _Cue(
                kind=kind,
                previous=sequence[index - 1] if index else None,
                upcoming=sequence[index + 1] if index + 1 < len(sequence) else None,
            )
            lines = messages_for(
                event,
                kind=kind,
                trainer=trainer,
                opponent=self._name("opponent"),
                upcoming=cue.upcoming,
            )
            self._play(event, cue, lines)
        beats, self._beats = self._beats, []
        return beats

    # --- Infraestructura del guion ----------------------------------------

    def _lookup(self, species: str, form: str, shiny: bool) -> str | None:
        key = (species, form, shiny)
        if key not in self._raw:
            try:
                self._raw[key] = self._sprites(species, form, shiny)
            except Exception:  # el sprite es decorativo: sin él se usa el sustituto
                self._raw[key] = None
        return self._raw[key]

    def _sprite(self, side: SideId, species: str, form: str, shiny: bool) -> Grid:
        key = (species, form, shiny, side)
        if key not in self._grids:
            area = self._layout.area(side)
            ansi = self._lookup(species, form, shiny)
            self._grids[key] = prepare_sprite(
                ansi, area.height, area.width, mirror=side == "player"
            )
        return self._grids[key]

    def _member(self, side: SideId) -> _Member | None:
        key = self._active.get(side)
        return self._members.get(key) if key else None

    def _name(self, side: SideId) -> str | None:
        member = self._member(side)
        return member.name if member else None

    def _on_field(self, side: SideId, key: str) -> bool:
        return self._active.get(side) == key

    def _hud(self, side: SideId) -> Hud | None:
        member = self._member(side)
        if member is None or not self._hud_on[side]:
            return None
        player = side == "player"
        exp = (
            experience_fraction(member.level, member.experience, member.growth_rate)
            if player and self._award
            else None
        )
        return Hud(
            name=member.name,
            level=member.level,
            gender=member.gender,
            status=member.status,
            hp=member.hp,
            max_hp=member.max_hp,
            exp_fraction=exp,
            shiny=member.shiny,
            show_numbers=player,
        )

    def _rest(self) -> SceneModel:
        """La escena sin nada transitorio: lo que el director sabe que hay."""
        scene = replace(self.scene, particles=(), shake=0)
        for side in SIDES:
            scene = replace_side(
                scene,
                side,
                sprite=self._canonical[side],
                hud=self._hud(side),
                offset=(0, 0),
                visible=self._shown[side],
                flash=False,
                sink=0,
            )
        return scene

    def _frames(self, frames: Sequence[SceneModel], delay: float = FRAME_DELAY) -> None:
        for frame in frames:
            self.scene = frame
            self._beats.append(Beat(frame, None, delay))

    def _settle(self) -> None:
        rest = self._rest()
        if rest != self.scene:
            self._frames([rest])

    def _say(self, lines: Sequence[str], delay: float = MESSAGE_DELAY) -> None:
        for line in lines:
            self._beats.append(Beat(self.scene, line, delay))

    # --- Reparto de eventos -------------------------------------------------

    def _play(self, event: BattleEvent, cue: _Cue, lines: list[str]) -> None:
        match event:
            case TurnStarted():
                # La caja de texto imita a los juegos: el turno no se escribe.
                return
            case BattleStarted():
                self._shown = {"player": False, "opponent": False}
                self._hud_on = {"player": False, "opponent": False}
                self._settle()
                self._say(lines)
            case SentOut():
                self._sent_out(event, cue, lines)
            case Recalled():
                self._say(lines)
                if self._on_field(event.side, event.key):
                    if self._shown[event.side]:
                        self._frames(_into_ball(self._rest(), event.side))
                    self._leave(event.side, departed=False)
            case MoveUsed():
                self._move_used(event, cue, lines)
            case Damaged():
                self._damaged(event, cue, lines)
            case Healed():
                self._healed(event, lines)
            case StatusInflicted():
                member = self._members.get(event.key)
                if member:
                    member.status = event.status
                self._effect(event.side, event.key, _status_frames, event.status)
                self._say(lines)
            case StatusCured():
                member = self._members.get(event.key)
                if member:
                    member.status = Status.NONE
                self._effect(event.side, event.key, _sparkle_frames)
                self._say(lines)
            case Immobilized():
                self._say(lines)
                self._effect(event.side, event.key, _immobilized_frames, event.reason)
            case Confused():
                self._effect(event.side, event.key, _stars_frames)
                self._say(lines)
            case ConfusionCheck():
                self._say(lines)
                self._effect(event.side, event.key, _stars_frames)
            case StatChanged():
                self._effect(event.side, event.key, _stat_frames, event.delta)
                self._say(lines)
            case StatsReset():
                self._frames(_mist_frames(self._rest()))
                self._settle()
                self._say(lines)
            case PumpedUp():
                self._effect(event.side, event.key, _aura_frames, _RISE, "▲")
                self._say(lines)
            case ProtectionRaised():
                self._effect(event.side, event.key, _shield_frames, 4)
                self._say(lines)
            case Protected():
                self._effect(event.side, event.key, _shield_frames, 2)
                self._say(lines)
            case Charging():
                self._charging(event, lines)
            case ScreenRaised():
                palette = _SCREEN_COLORS.get(event.screen, _SHIELD)
                if self._shown[event.side]:
                    self._frames(_wall_frames(self._rest(), event.side, palette))
                    self._settle()
                self._say(lines)
            case Seeded():
                self._effect(event.side, event.key, _seed_frames)
                self._say(lines)
            case Trapped():
                self._effect(event.side, event.key, _wrap_frames)
                self._say(lines)
            case Teleported():
                if self._on_field(event.side, event.key) and self._shown[event.side]:
                    self._frames(_teleport_frames(self._rest(), event.side))
                self._leave(event.side)
                self._say(lines)
            case ForcedOut() | OpponentFled():
                side: SideId = event.side if isinstance(event, ForcedOut) else "opponent"
                if self._shown[side]:
                    self._frames(_slide_out_frames(self._rest(), side, self.width))
                self._leave(
                    side, departed=cue.kind is BattleKind.WILD or isinstance(event, OpponentFled)
                )
                self._say(lines)
            case Fainted():
                member = self._members.get(event.key)
                if member:
                    member.hp = 0
                if self._on_field(event.side, event.key):
                    if self._shown[event.side]:
                        self._frames(_faint_frames(self._rest(), event.side))
                    self._shown[event.side] = False
                    self._hud_on[event.side] = False
                    self._settle()
                self._say(lines, LONG_MESSAGE_DELAY)
            case ExperienceGained():
                self._say(lines)
                self._experience(event)
            case LeveledUp():
                self._leveled_up(event, lines)
            case EscapeAttempted():
                self._say(lines)
                if event.success and not event.forbidden:
                    if self._shown["player"]:
                        self._frames(_slide_out_frames(self._rest(), "player", self.width))
                    self._leave("player", departed=True)
            case BallThrown():
                self._ball_thrown(event, lines)
            case BattleEnded():
                self._settle()
                self._say(lines, LONG_MESSAGE_DELAY)
            case _:
                self._say(lines)

    def _effect(
        self, side: SideId, key: str, builder: Callable[..., list[SceneModel]], *args: object
    ) -> None:
        """Animación sobre un Pokémon en el campo (si se ve) y vuelta al reposo."""
        if self._on_field(side, key) and self._shown[side]:
            self._frames(builder(self._rest(), side, *args))
        self._settle()

    def _leave(self, side: SideId, *, departed: bool = True) -> None:
        self._shown[side] = False
        self._hud_on[side] = False
        if departed:
            self._departed.add(side)
        self._settle()

    # --- Eventos con coreografía propia -------------------------------------

    def _sent_out(self, event: SentOut, cue: _Cue, lines: list[str]) -> None:
        side = event.side
        member = self._members.get(event.key)
        if member is None:
            # Desconocido para el estado inicial: PS llenos hasta saber más.
            member = _Member(
                name=event.name,
                species=event.species,
                form=event.form,
                shiny=event.shiny,
                level=event.level,
                hp=1,
                max_hp=1,
                status=Status.NONE,
                gender=None,
                experience=0,
                growth_rate="medium",
            )
            self._members[event.key] = member
        member.name, member.level, member.shiny = event.name, event.level, event.shiny
        self._active[side] = event.key
        self._departed.discard(side)
        self._airborne.discard(side)
        grid = self._sprite(side, event.species, event.form, event.shiny)
        self._canonical[side] = grid
        self._shown[side] = False
        self._hud_on[side] = False
        wild = side == "opponent" and cue.kind is BattleKind.WILD
        if wild:
            self._frames(_slide_in_frames(self._rest(), side, self.width))
        else:
            self._settle()
            self._say(lines)
            self._frames(_out_of_ball(self._rest(), side, self.height, self.width))
        self._shown[side] = True
        if event.shiny:
            self._frames(_sparkle_frames(self._rest(), side))
        self._hud_on[side] = True
        self._settle()
        if wild:
            self._say(lines)

    def _move_used(self, event: MoveUsed, cue: _Cue, lines: list[str]) -> None:
        side = event.side
        if side in self._airborne and self._on_field(side, event.key):
            # Vuelve del cielo o de bajo tierra para descargar el golpe.
            self._airborne.discard(side)
            self._shown[side] = True
            self._frames(_return_frames(self._rest(), side))
            self._settle()
        self._say(lines)
        following = cue.upcoming
        if (
            following is None
            or isinstance(following, _NO_MOVE_ANIMATION)
            or not self._on_field(side, event.key)
        ):
            return
        fx = _TYPE_FX.get(event.move_type, _TYPE_FX["normal"])
        rest = self._rest()
        if event.damage_class is DamageClass.STATUS:
            target = getattr(following, "side", side)
            target = target if target in SIDES else side
            if self._shown[target]:
                self._frames(_aura_frames(rest, target, fx.palette, fx.head))
        elif fx.path in ("rise", "fall"):
            self._frames(_field_frames(rest, other_side(side), fx))
        elif event.damage_class is DamageClass.PHYSICAL:
            self._frames(_lunge_frames(rest, side, other_side(side), fx, self.width))
        else:
            self._frames(_projectile_frames(rest, side, other_side(side), fx))
        self._settle()

    def _damaged(self, event: Damaged, cue: _Cue, lines: list[str]) -> None:
        side = event.side
        member = self._members.get(event.key)
        if member is None or not self._on_field(side, event.key):
            if member:
                member.hp, member.max_hp = max(0, event.hp_after), max(1, event.max_hp)
            self._say(lines)
            return
        # La caja arranca en los PS previos del evento y solo baja en el tween.
        member.hp, member.max_hp = max(0, event.hp_before), max(1, event.max_hp)
        result_after = event.cause is DamageCause.MOVE
        if not result_after:
            self._say(lines)
        rest = self._rest()
        shown = self._shown[side]
        if shown:
            previous = cue.previous
            if (
                event.cause is DamageCause.MOVE
                and isinstance(previous, Damaged)
                and previous.key == event.key
                and previous.cause is DamageCause.MOVE
            ):
                # Golpes sucesivos de un multigolpe: cada uno con su chispazo.
                self._frames(_burst_frames(rest, side, _TYPE_FX["normal"], rings=2))
            elif event.cause in (DamageCause.BURN, DamageCause.POISON):
                status = Status.BURN if event.cause is DamageCause.BURN else Status.POISON
                self._frames(_status_frames(rest, side, status, frames=4))
            elif event.cause is DamageCause.LEECH_SEED:
                self._frames(_drain_frames(rest, side, other_side(side)))
            elif event.cause is DamageCause.TRAP:
                self._frames(_wrap_frames(rest, side, frames=3))
            elif event.cause is DamageCause.SACRIFICE:
                self._frames(_explosion_frames(rest, side))
            hit = _hit_frames(
                rest,
                side,
                critical=event.critical,
                super_effective=event.effectiveness > 1,
                weak=0 < event.effectiveness < 1 or event.cause is not DamageCause.MOVE,
            )
            self._frames(hit)
        self._settle()
        self._frames(
            _bar_frames(self.scene, side, event.hp_before, event.hp_after, event.max_hp),
            BAR_DELAY,
        )
        member.hp = max(0, event.hp_after)
        self._settle()
        if result_after:
            self._say(lines)

    def _healed(self, event: Healed, lines: list[str]) -> None:
        side = event.side
        member = self._members.get(event.key)
        if member and self._on_field(side, event.key):
            member.hp, member.max_hp = max(0, event.hp_before), max(1, event.max_hp)
            rest = self._rest()
            if self._shown[side]:
                if event.cause in (HealCause.DRAIN, HealCause.LEECH_SEED):
                    self._frames(_drain_frames(rest, other_side(side), side))
                else:
                    self._frames(_aura_frames(rest, side, _HEAL, "✚"))
            self._settle()
            self._frames(
                _bar_frames(self.scene, side, event.hp_before, event.hp_after, event.max_hp),
                BAR_DELAY,
            )
        if member:
            member.hp, member.max_hp = max(0, event.hp_after), max(1, event.max_hp)
        self._settle()
        self._say(lines)

    def _charging(self, event: Charging, lines: list[str]) -> None:
        side = event.side
        if self._on_field(side, event.key) and self._shown[side]:
            rest = self._rest()
            if event.move in _ASCEND:
                self._frames(_ascend_frames(rest, side))
            elif event.move in _BURROW:
                self._frames(_burrow_frames(rest, side, bubbles=event.move == "dive"))
            elif event.move in _VANISH:
                self._frames(_blink_out_frames(rest, side))
            elif event.move in _ABSORB_LIGHT:
                self._frames(_converge_frames(rest, side))
            else:
                self._frames(_aura_frames(rest, side, _SPARK, "✦", glow=True))
            if event.move in _ASCEND | _BURROW | _VANISH:
                self._airborne.add(side)
                self._shown[side] = False
        self._settle()
        self._say(lines)

    def _experience(self, event: ExperienceGained) -> None:
        member = self._members.get(event.key)
        if member is None:
            return
        rest = self._rest()
        before = experience_fraction(member.level, member.experience, member.growth_rate)
        member.experience += max(0, event.amount)
        after = experience_fraction(member.level, member.experience, member.growth_rate)
        if self._on_field("player", event.key) and self._hud_on["player"] and self._award:
            self._frames(_exp_frames(rest, before, after), BAR_DELAY)
        self._settle()

    def _leveled_up(self, event: LeveledUp, lines: list[str]) -> None:
        member = self._members.get(event.key)
        if member:
            member.level = event.level
            gained = dict(event.deltas).get("hp", 0)
            member.max_hp = max(1, member.max_hp + gained)
            if member.hp > 0:
                member.hp = min(member.max_hp, member.hp + gained)
        if self._on_field("player", event.key) and self._shown["player"]:
            self._frames(_aura_frames(self._rest(), "player", _SPARK, "✦"))
        self._settle()
        self._say(lines)

    def _ball_thrown(self, event: BallThrown, lines: list[str]) -> None:
        self._say(lines[:1])
        side: SideId = "opponent"
        style = _ball_style(event.ball_slug)
        if not (self._shown[side] and self._member(side)):
            # Oculto (en el aire, bajo tierra…): no hay animación, pero si cae
            # atrapado su caja se va igual.
            if event.caught:
                self._leave(side)
            self._say(lines[1:])
            return
        rest = self._rest()
        center = _center(sprite_box(rest, side))
        # La bola se posa en la plataforma, con su base a ras del suelo.
        landing = (self._layout.area(side).bottom, center[1])
        self._frames(_throw_frames(rest, center, self.height, style))
        grid = self._canonical[side]
        self._frames(_absorb_frames(rest, side, grid, center, style))
        self._shown[side] = False
        hidden = self._rest()
        self._frames(_drop_frames(hidden, center, landing, style))
        wobbles = 3 if event.caught else max(0, min(3, event.shakes))
        self._frames(_wobble_frames(hidden, landing, style, wobbles), WOBBLE_DELAY)
        if event.caught:
            self._frames(_click_frames(hidden, landing, style))
            self._hud_on[side] = False
            self._departed.add(side)
            self._settle()
        else:
            self._frames(_breakout_frames(hidden, side, grid, landing, style.accent))
            self._shown[side] = True
            self._settle()
        self._say(lines[1:])


# --- Geometría y píxeles -------------------------------------------------------
#
# Los efectos se dibujan como pixel art de medio bloque: x es la columna e y la
# media fila (dos por celda), así que los píxeles salen cuadrados y un círculo
# es redondo. ``_to_particles`` los empaqueta en partículas ▀/▄ que la escena
# compone con transparencia, igual que los sprites. Los glifos del tipo (▲, ϟ,
# ❦…) van encima como acentos.

_Pixels = dict[tuple[int, int], RGB]


def _center(box: Box) -> tuple[int, int]:
    return box.row + box.height // 2, box.col + box.width // 2


def _mouth(box: Box, side: SideId) -> tuple[int, int]:
    """Por dónde sale un proyectil: la parte del sprite que mira al rival."""
    row = box.row + box.height // 3
    col = box.right - box.width // 4 if side == "player" else box.col + box.width // 4
    return row, col


def _particle(row: float, col: float, glyph: str, rgb: RGB) -> Particle:
    return Particle(round(row), round(col), glyph, rgb)


def _hash01(value: int) -> float:
    """Ruido determinista en [0, 1): da variedad sin depender del azar."""
    return ((value * 2654435761) & 0xFFFFFFFF) / 0x100000000


def _to_pixel(point: tuple[float, float]) -> tuple[float, float]:
    """(fila, columna) de la escena → (x, y) en píxeles de medio bloque."""
    row, col = point
    return col + 0.5, row * 2 + 1.0


def _to_cell(x: float, y: float) -> tuple[float, float]:
    return (y - 1.0) / 2, x - 0.5


def _disc(
    pixels: _Pixels, x: float, y: float, radius: float, palette: Palette, *, hollow: float = 0.0
) -> None:
    """Disco con núcleo claro y borde oscuro; con ``hollow`` es un anillo
    (``hollow`` es el radio interior relativo) de color chispeante."""
    reach = max(0.6, radius)
    for py in range(math.floor(y - reach), math.ceil(y + reach) + 1):
        for px in range(math.floor(x - reach), math.ceil(x + reach) + 1):
            distance = math.hypot(px + 0.5 - x, py + 0.5 - y) / reach
            if distance > 1.0 or distance < hollow:
                continue
            if hollow:
                angle = math.atan2(py + 0.5 - y, px + 0.5 - x) + math.pi
                color = palette[int(angle / (2 * math.pi) * 12) % 3]
            elif distance < 0.45:
                color = palette[2]
            elif distance < 0.8:
                color = palette[1]
            else:
                color = palette[0]
            pixels[(px, py)] = color


def _segment(
    pixels: _Pixels,
    start: tuple[float, float],
    end: tuple[float, float],
    color: RGB,
    width: int = 1,
) -> None:
    (x0, y0), (x1, y1) = start, end
    steps = max(1, round(max(abs(x1 - x0), abs(y1 - y0))))
    for k in range(steps + 1):
        px = math.floor(x0 + (x1 - x0) * k / steps)
        py = math.floor(y0 + (y1 - y0) * k / steps)
        for extra in range(width):
            pixels[(px, py + extra)] = color


def _to_particles(pixels: _Pixels) -> list[Particle]:
    cells: dict[tuple[int, int], list[RGB | None]] = {}
    for (x, y), color in pixels.items():
        row, half = divmod(y, 2)
        cells.setdefault((row, x), [None, None])[half] = color
    particles = []
    for (row, col), (top, bottom) in cells.items():
        if top is not None:
            particles.append(Particle(row, col, "▀", top, bottom))
        elif bottom is not None:
            particles.append(Particle(row, col, "▄", bottom))
    return particles


def _ring(
    center: tuple[float, float],
    radius: float,
    glyph: str,
    palette: Sequence[RGB],
    *,
    points: int = 10,
    phase: float = 0.0,
    squash: float = 2.0,
) -> list[Particle]:
    """Anillo de glifos; ``squash`` compensa que las celdas son altas."""
    row, col = center
    return [
        _particle(
            row + radius * math.sin(phase + 2 * math.pi * k / points),
            col + squash * radius * math.cos(phase + 2 * math.pi * k / points),
            glyph,
            palette[k % len(palette)],
        )
        for k in range(points)
    ]


def _around(
    box: Box, glyph: str, palette: Sequence[RGB], phase: float, points: int = 14
) -> list[Particle]:
    """Elipse de glifos alrededor del sprite (aura)."""
    row, col = box.row + (box.height - 1) / 2, box.col + (box.width - 1) / 2
    radius_r = box.height / 2 + 0.6
    radius_c = box.width / 2 + 1.5
    return [
        _particle(
            row + radius_r * math.sin(phase + 2 * math.pi * k / points),
            col + radius_c * math.cos(phase + 2 * math.pi * k / points),
            glyph,
            palette[k % len(palette)],
        )
        for k in range(points)
    ]


def _with(scene: SceneModel, particles: Sequence[Particle], shake: int = 0) -> SceneModel:
    return replace(scene, particles=tuple(particles), shake=shake)


# --- Ataques ------------------------------------------------------------------


def _burst_frames(scene: SceneModel, side: SideId, fx: _TypeFx, rings: int = 3) -> list[SceneModel]:
    """Impacto: un fogonazo y anillos de píxeles que se abren con chispas del tipo."""
    center = _center(sprite_box(scene, side))
    x, y = _to_pixel(center)
    pixels: _Pixels = {}
    _disc(pixels, x, y, 3.4, (fx.palette[1], fx.palette[2], FLASH_RGB))
    frames = [_with(scene, _to_particles(pixels) + [_particle(*center, "✺", fx.palette[0])])]
    for step in range(1, rings + 1):
        pixels = {}
        radius = 2.5 + 2.8 * step
        _disc(pixels, x, y, radius, fx.palette, hollow=1 - 1.7 / radius)
        sparks = _ring(
            center, radius / 2 + 0.6, fx.burst, fx.palette, points=4 + 2 * step, phase=step * 0.5
        )
        frames.append(_with(scene, _to_particles(pixels) + sparks))
    return frames


def _path_point(
    start: tuple[int, int], end: tuple[int, int], t: float, path: str, distance: float
) -> tuple[float, float]:
    row = start[0] + (end[0] - start[0]) * t
    col = start[1] + (end[1] - start[1]) * t
    if path == "arc":
        row -= max(2.0, distance / 10) * 4 * t * (1 - t)
    elif path == "wave":
        row += 1.4 * math.sin(t * 3 * math.pi)
    return row, col


def _projectile_frames(
    scene: SceneModel, attacker: SideId, defender: SideId, fx: _TypeFx
) -> list[SceneModel]:
    """Proyectil de pixel art del atacante al defensor, con estela e impacto."""
    start = _mouth(sprite_box(scene, attacker), attacker)
    end = _center(sprite_box(scene, defender))
    distance = max(abs(end[0] - start[0]) * 2, abs(end[1] - start[1]), 1)
    steps = max(7, min(12, distance // 4))
    if fx.path == "zigzag":
        return _bolt_frames(scene, start, end, fx, steps) + _burst_frames(scene, defender, fx)
    path = "line" if fx.path in ("impact", "rise", "fall") else fx.path
    dim: Palette = (fx.palette[0], fx.palette[0], fx.palette[1])
    frames: list[SceneModel] = []
    for k in range(1, steps + 1):
        t = k / steps
        pixels: _Pixels = {}
        glyphs: list[Particle] = []
        for age in (3, 2, 1):  # estela: más pequeña y oscura cuanto más vieja
            tail_t = t - age * 0.07
            if tail_t <= 0:
                continue
            tail = _path_point(start, end, tail_t, path, distance)
            _disc(pixels, *_to_pixel(tail), fx.size * (1 - 0.22 * age), dim)
            if age == 3:
                glyphs.append(_particle(*tail, fx.trail, fx.palette[1]))
        head = _path_point(start, end, t, path, distance)
        x, y = _to_pixel(head)
        if path == "spiral":
            for j in range(3):
                angle = k * 0.9 + j * 2 * math.pi / 3
                leaf = (x + 4.5 * math.cos(angle), y + 3.5 * math.sin(angle))
                _disc(pixels, *leaf, fx.size, fx.palette)
                glyphs.append(_particle(*_to_cell(*leaf), fx.head, fx.palette[2]))
        elif path == "rings":
            _disc(pixels, x, y, fx.size + 1.6, fx.palette, hollow=0.62)
            glyphs.append(_particle(*head, fx.head, fx.palette[1]))
        else:
            _disc(pixels, x, y, fx.size, fx.palette)
            for j, (dr, dc) in enumerate(((-1, -2), (1, -1), (0, -3))):
                if (k + j) % 2 == 0:
                    glyphs.append(_particle(head[0] + dr, head[1] + dc, fx.head, fx.palette[j]))
        frames.append(_with(scene, _to_particles(pixels) + glyphs))
    return frames + _burst_frames(scene, defender, fx)


def _bolt_frames(
    scene: SceneModel, start: tuple[int, int], end: tuple[int, int], fx: _TypeFx, steps: int
) -> list[SceneModel]:
    """Rayo en zigzag que crece a saltos y, completo, destella antes del impacto."""
    (sx, sy), (ex, ey) = _to_pixel(start), _to_pixel(end)
    length = math.hypot(ex - sx, ey - sy) or 1.0
    normal = (-(ey - sy) / length, (ex - sx) / length)
    vertices = []
    for k in range(steps + 1):
        t = k / steps
        swing = 0.0 if k in (0, steps) else (1 if k % 2 else -1) * (2.5 + 1.5 * _hash01(k))
        vertices.append(
            (sx + (ex - sx) * t + normal[0] * swing, sy + (ey - sy) * t + normal[1] * swing)
        )
    frames = []
    for k in range(1, steps + 1):
        pixels: _Pixels = {}
        for a, b in zip(vertices[:k], vertices[1 : k + 1]):
            _segment(pixels, a, b, fx.palette[1], width=2)
            _segment(pixels, a, b, fx.palette[2])
        frames.append(
            _with(
                scene,
                _to_particles(pixels)
                + [_particle(*_to_cell(*vertices[k]), fx.head, fx.palette[0])],
            )
        )
    for flicker in range(2):
        pixels = {}
        for a, b in zip(vertices, vertices[1:]):
            _segment(pixels, a, b, FLASH_RGB if flicker == 0 else fx.palette[1], width=2)
        sparks = [_particle(*_to_cell(*v), fx.head, fx.palette[flicker]) for v in vertices[1:-1:2]]
        frames.append(_with(scene, _to_particles(pixels) + sparks))
    return frames


def _lunge_frames(
    scene: SceneModel, attacker: SideId, defender: SideId, fx: _TypeFx, width: int
) -> list[SceneModel]:
    """Embestida: el atacante se lanza hacia el rival, golpea y vuelve."""
    reach = max(3, min(7, width // 14))
    row_dir, col_dir = (-1, 1) if attacker == "player" else (1, -1)
    path = [(0, reach // 3), (row_dir, 2 * reach // 3), (row_dir, reach)]
    frames = [replace_side(scene, attacker, offset=(r, col_dir * c)) for r, c in path]
    apex = frames[-1]
    impact = (
        _impact_frames(scene, defender, fx)
        if fx.path == "impact"
        else _burst_frames(scene, defender, fx, rings=2)
    )
    frames += [
        replace(frame, opponent_offset=apex.opponent_offset, player_offset=apex.player_offset)
        for frame in impact
    ]
    frames.append(replace_side(scene, attacker, offset=(row_dir, col_dir * reach // 2)))
    return frames


def _impact_frames(scene: SceneModel, side: SideId, fx: _TypeFx) -> list[SceneModel]:
    """Estrellas de impacto que caen en varios puntos del objetivo (Lucha, Normal)."""
    box = sprite_box(scene, side)
    spots = ((0.5, 0.5), (0.3, 0.3), (0.65, 0.72), (0.38, 0.78))
    frames = []
    previous: tuple[float, float] | None = None
    for index, (row_part, col_part) in enumerate(spots):
        row = box.row + row_part * (box.height - 1)
        col = box.col + col_part * (box.width - 1)
        x, y = _to_pixel((row, col))
        pixels: _Pixels = {}
        for ray in range(8):
            angle = ray * math.pi / 4 + index * 0.2
            length = 5.5 if ray % 2 == 0 else 3.0
            tip = (x + length * math.cos(angle), y + length * math.sin(angle))
            _segment(pixels, (x, y), tip, fx.palette[1 if ray % 2 else 0])
        _disc(pixels, x, y, 1.8, (fx.palette[1], fx.palette[2], FLASH_RGB))
        glyphs = [_particle(row, col, fx.burst, fx.palette[0])]
        if previous is not None:
            glyphs.append(_particle(*previous, fx.trail, fx.palette[0]))
        previous = (row, col)
        frames.append(_with(scene, _to_particles(pixels) + glyphs))
    return frames


def _field_frames(scene: SceneModel, defender: SideId, fx: _TypeFx) -> list[SceneModel]:
    """Tierra: pilares que brotan bajo el objetivo con temblor. Roca: pedruscos
    que le caen encima."""
    box = sprite_box(scene, defender)
    frames: list[SceneModel] = []
    if fx.path == "rise":
        base = box.bottom * 2 + 1
        columns = list(range(box.col - 1, box.right + 2, 4))
        for step, height in enumerate((3, 7, 11, 14, 9, 4)):
            pixels: _Pixels = {}
            rubble = []
            for index, col in enumerate(columns):
                pillar = max(1, height - 3 * (index % 3))
                for level in range(pillar):
                    color = fx.palette[2] if level >= pillar - 2 else fx.palette[1 if level else 0]
                    for dx in (0, 1):
                        pixels[(col + dx, base - level)] = color
                rubble.append(
                    _particle(
                        box.bottom, col - 1, fx.head if index % 2 else fx.trail, fx.palette[0]
                    )
                )
            frames.append(_with(scene, _to_particles(pixels) + rubble, shake=1 if step % 2 else -1))
        return frames
    targets = [box.col + box.width // 4, box.col + box.width // 2, box.col + 3 * box.width // 4]
    impact = (box.row + box.height // 2) * 2
    for step in range(7):
        pixels = {}
        dust = []
        for index, col in enumerate(targets):
            y = (box.row - 6) * 2 + 4 * (step - index)
            if (box.row - 7) * 2 < y <= impact:
                _disc(pixels, col + 0.5, y, 2.4, fx.palette)
                _disc(
                    pixels, col + 2.0, y - 1.5, 1.0, (fx.palette[0], fx.palette[0], fx.palette[1])
                )
                dust.append(_particle(*_to_cell(col + 0.5, y - 5), fx.trail, fx.palette[0]))
        frames.append(
            _with(scene, _to_particles(pixels) + dust, shake=1 if step >= 4 and step % 2 else 0)
        )
    return frames + _burst_frames(scene, defender, replace(fx, burst=fx.head), rings=2)


def _hit_frames(
    scene: SceneModel, side: SideId, *, critical: bool, super_effective: bool, weak: bool
) -> list[SceneModel]:
    """Parpadeo y sacudida al recibir el golpe; el crítico sacude la escena."""
    pattern = [
        (1, True, True),
        (0, False, False),
        (-1, True, False),
        (0, False, False),
        (0, True, False),
    ]
    if weak:
        pattern = [(1, True, True), (0, False, False), (0, True, False)]
    shakes = [2, -2, 1, -1, 0] if critical else [0] * len(pattern)
    center = _center(sprite_box(scene, side))
    frames = []
    for index, (col, visible, flash) in enumerate(pattern):
        particles: list[Particle] = []
        if super_effective and index < 3:
            particles = _ring(center, 1 + index, "✦", _SPARK, points=6 + 3 * index, phase=index)
        moved = replace_side(scene, side, offset=(0, col), visible=visible, flash=flash)
        frames.append(_with(moved, particles, shake=shakes[index]))
    return frames


def _explosion_frames(scene: SceneModel, side: SideId) -> list[SceneModel]:
    """Explosión: bola de fuego que crece y se abre en anillos."""
    center = _center(sprite_box(scene, side))
    x, y = _to_pixel(center)
    fire = _TYPE_FX["fire"].palette
    frames = []
    for step in range(5):
        pixels: _Pixels = {}
        radius = 3 + 3 * step
        _disc(pixels, x, y, radius, fire, hollow=0.0 if step < 2 else 1 - 2.2 / radius)
        sparks = _ring(center, radius / 2 + 1, "✺" if step % 2 else "✹", fire, points=6 + 2 * step)
        flashed = replace_side(scene, side, flash=step < 2)
        frames.append(_with(flashed, _to_particles(pixels) + sparks, shake=2 if step % 2 else -2))
    return frames


# --- Barras ---------------------------------------------------------------------


def _bar_frames(
    scene: SceneModel, side: SideId, start: int, end: int, max_hp: int
) -> list[SceneModel]:
    """La barra de PS baja (o sube) paso a paso, siempre en el mismo sentido."""
    hud = side_view(scene, side).hud
    if hud is None:
        return []
    start, end = max(0, start), max(0, end)
    if start == end:
        return []
    max_hp = max(1, max_hp)
    delta = abs(end - start)
    steps = max(2, min(16, math.ceil(delta * 40 / max_hp)))
    sign = 1 if end > start else -1
    return [
        replace_side(
            scene, side, hud=replace(hud, hp=start + sign * (delta * k // steps), max_hp=max_hp)
        )
        for k in range(1, steps + 1)
    ]


def _exp_frames(scene: SceneModel, before: float, after: float) -> list[SceneModel]:
    hud = scene.player_hud
    if hud is None or hud.exp_fraction is None or after <= before:
        return []
    steps = max(2, min(14, math.ceil((after - before) * 24)))
    return [
        replace(scene, player_hud=replace(hud, exp_fraction=before + (after - before) * k / steps))
        for k in range(1, steps + 1)
    ]


# --- Auras y estados --------------------------------------------------------------


def _aura_frames(
    scene: SceneModel, side: SideId, palette: Palette, glyph: str, *, glow: bool = False
) -> list[SceneModel]:
    """Aura que gira alrededor del Pokémon (movimientos de estado, curación)."""
    box = sprite_box(scene, side)
    frames = []
    for step in range(6):
        particles = _around(box, glyph if step % 2 == 0 else "·", palette, phase=step * 0.35)
        if glyph == "✚":
            pixels: _Pixels = {}
            for index in range(4):
                x = box.col + (index + 0.5) * box.width / 4
                y = (box.bottom - ((step * 2 + index * 3) % max(3, box.height))) * 2
                for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
                    pixels[(math.floor(x) + dx, y + dy)] = palette[1 if dx or dy else 2]
            particles = _to_particles(pixels) + particles
        lit = replace_side(scene, side, flash=glow and step in (2, 4))
        frames.append(_with(lit, particles))
    return frames


def _status_frames(
    scene: SceneModel, side: SideId, status: Status, frames: int = 6
) -> list[SceneModel]:
    """El problema de estado se ve: llamas, burbujas, chispas, «z» o hielo."""
    glyph, palette = _STATUS_FX.get(status, ("✦", _SPARK))
    box = sprite_box(scene, side)
    result = []
    for step in range(frames):
        pixels: _Pixels = {}
        particles: list[Particle] = []
        if status is Status.SLEEP:
            # «z» que suben en diagonal desde la cabeza, cada vez más grandes.
            head = (box.row + 1, box.col + box.width // 2)
            for index in range(3):
                rise = step - index * 2
                if rise >= 0:
                    particles.append(
                        _particle(
                            head[0] - rise,
                            head[1] + 2 * rise + 2,
                            "Z" if index % 2 else "z",
                            palette[index % 3],
                        )
                    )
        elif status is Status.FREEZE:
            particles = _around(box, glyph, palette, phase=step * 0.5, points=12)
            for index in range(8):
                x = box.col + _hash01(step * 13 + index) * box.width
                y = box.row * 2 + _hash01(step * 7 + index * 5) * box.height * 2
                pixels[(math.floor(x), math.floor(y))] = palette[2]
        elif status is Status.PARALYSIS:
            for index in range(5):
                x = box.col + _hash01(step * 31 + index) * box.width
                y = box.row * 2 + _hash01(step * 17 + index * 7) * box.height * 2
                points = [(x, y), (x + 1.5, y + 1.5), (x, y + 3), (x + 1.5, y + 4.5)]
                for a, b in zip(points, points[1:]):
                    _segment(pixels, a, b, palette[1 + index % 2])
                particles.append(_particle(*_to_cell(x, y), glyph, palette[index % 3]))
        else:
            # Llamas o burbujas que suben desde los pies.
            bubbles = status in (Status.POISON, Status.TOXIC)
            for index, col in enumerate(range(box.col, box.right + 1, max(2, box.width // 6))):
                rise = (step * 2 + index * 3) % max(2, box.height)
                x, y = col + 0.5, (box.bottom - rise) * 2 + 1.0
                _disc(
                    pixels, x, y, 1.6 if bubbles else 1.3, palette, hollow=0.5 if bubbles else 0.0
                )
                if index % 2:
                    particles.append(
                        _particle(box.bottom - rise - 1, col, glyph, palette[index % 3])
                    )
        moved = scene
        if status is Status.PARALYSIS:
            moved = replace_side(scene, side, offset=(0, 1 if step % 2 else -1))
        result.append(_with(moved, _to_particles(pixels) + particles))
    return result


def _immobilized_frames(
    scene: SceneModel, side: SideId, reason: ImmobileReason
) -> list[SceneModel]:
    if reason is ImmobileReason.ASLEEP:
        return _status_frames(scene, side, Status.SLEEP)
    if reason is ImmobileReason.FROZEN:
        return _status_frames(scene, side, Status.FREEZE, frames=4)
    if reason is ImmobileReason.PARALYZED:
        return _status_frames(scene, side, Status.PARALYSIS, frames=4)
    if reason is ImmobileReason.FLINCHED:
        return [replace_side(scene, side, offset=(0, col)) for col in (1, -1, 1, 0)]
    return []


def _sparkle_frames(scene: SceneModel, side: SideId) -> list[SceneModel]:
    """Destellos (shiny, curación de estado)."""
    box = sprite_box(scene, side)
    frames = []
    for step in range(5):
        particles = []
        for index in range(7):
            angle = index * 2.4 + step * 0.6
            radius = 0.35 + 0.15 * ((index + step) % 3)
            particles.append(
                _particle(
                    box.row + box.height / 2 + radius * box.height * math.sin(angle),
                    box.col + box.width / 2 + radius * box.width * math.cos(angle),
                    "✦" if (index + step) % 2 else "✧",
                    _SPARK[(index + step) % 3],
                )
            )
        frames.append(_with(scene, particles))
    return frames


def _stars_frames(scene: SceneModel, side: SideId) -> list[SceneModel]:
    """Estrellitas de la confusión dando vueltas sobre la cabeza."""
    box = sprite_box(scene, side)
    head = (max(0, box.row) - 0.5, box.col + box.width / 2)
    frames = []
    for step in range(6):
        particles = [
            _particle(
                head[0] + 0.8 * math.sin(step * 0.9 + k * 2 * math.pi / 3),
                head[1] + 4 * math.cos(step * 0.9 + k * 2 * math.pi / 3),
                "✦",
                _STARS[k],
            )
            for k in range(3)
        ]
        frames.append(_with(scene, particles))
    return frames


def _stat_frames(scene: SceneModel, side: SideId, delta: int) -> list[SceneModel]:
    """Estelas y flechas que suben (cálidas) o bajan (frías) sobre el sprite."""
    box = sprite_box(scene, side)
    rising = delta > 0
    palette = _RISE if rising else _FALL
    spacing = 3 if abs(delta) >= 2 else 5
    frames = []
    for step in range(7):
        pixels: _Pixels = {}
        particles = []
        for index, col in enumerate(range(box.col + 1, box.right, spacing)):
            travel = (step * 2 + index * 3) % max(3, box.height)
            row = box.bottom - travel if rising else box.row + travel
            tail = 1 if rising else -1
            for length in range(4):
                pixels[(col, row * 2 + tail * length)] = palette[min(2, length)]
            particles.append(_particle(row, col, "↑" if rising else "↓", palette[index % 3]))
        frames.append(_with(scene, _to_particles(pixels) + particles))
    return frames


def _shield_frames(scene: SceneModel, side: SideId, frames: int) -> list[SceneModel]:
    box = sprite_box(scene, side)
    return [
        _with(scene, _around(box, "◇" if step % 2 else "◆", _SHIELD, phase=step * 0.2, points=16))
        for step in range(frames)
    ]


def _wall_frames(scene: SceneModel, side: SideId, palette: Palette) -> list[SceneModel]:
    """Pantalla translúcida delante del Pokémon, entre él y el rival."""
    box = sprite_box(scene, side)
    col = box.right + 2 if side == "player" else box.col - 3
    frames = []
    for step in range(5):
        particles = [
            _particle(
                row,
                col + (1 if (row + step) % 4 == 0 else 0),
                "▒" if (row + step) % 2 else "░",
                palette[(row + step) % 3],
            )
            for row in range(box.row, box.bottom + 1)
        ]
        frames.append(_with(scene, particles))
    return frames


def _mist_frames(scene: SceneModel) -> list[SceneModel]:
    """Niebla que barre todo el campo (anula los cambios de características)."""
    frames = []
    for step in range(6):
        particles = []
        for row in range(0, scene.height, 2):
            for col in range((row * 3 + step * 4) % 9, scene.width, 9):
                particles.append(_particle(row, col, "░", _MIST[(row + col) % 3]))
        frames.append(_with(scene, particles))
    return frames


def _seed_frames(scene: SceneModel, side: SideId) -> list[SceneModel]:
    box = sprite_box(scene, side)
    columns = [box.col + box.width // 4, box.col + box.width // 2, box.col + 3 * box.width // 4]
    grass = _TYPE_FX["grass"].palette
    frames = []
    for step in range(5):
        particles = []
        for index, col in enumerate(columns):
            row = box.row - 3 + step + index % 2
            glyph = "•" if step < 3 else "❦"
            particles.append(_particle(min(row, box.row + 1), col, glyph, grass[index % 3]))
        frames.append(_with(scene, particles))
    return frames


def _wrap_frames(scene: SceneModel, side: SideId, frames: int = 5) -> list[SceneModel]:
    box = sprite_box(scene, side)
    rows = [box.row + box.height // 4, box.row + box.height // 2, box.row + 3 * box.height // 4]
    result = []
    for step in range(frames):
        particles = [
            _particle(row, col, "≈", _WRAP[(col + step) % 3])
            for index, row in enumerate(rows)
            for col in range(box.col - 1 + (step + index) % 2, box.right + 2, 2)
        ]
        result.append(_with(scene, particles))
    return result


def _drain_frames(scene: SceneModel, source: SideId, target: SideId) -> list[SceneModel]:
    """Orbes de energía que viajan del drenado al que se cura."""
    start = _center(sprite_box(scene, source))
    end = _center(sprite_box(scene, target))
    frames = []
    for step in range(7):
        pixels: _Pixels = {}
        for index in range(4):
            t = (step - index * 1.2) / 5
            if 0 <= t <= 1:
                row = start[0] + (end[0] - start[0]) * t - 2 * math.sin(t * math.pi)
                col = start[1] + (end[1] - start[1]) * t
                _disc(pixels, *_to_pixel((row, col)), 1.5, _HEAL)
        frames.append(_with(scene, _to_particles(pixels)))
    return frames


# --- Entradas, salidas y movimientos de carga ---------------------------------------


def _shrunk(
    scene: SceneModel, side: SideId, grid: Grid, fraction: float, rgb: RGB | None
) -> SceneModel:
    """El sprite reducido a ``fraction`` y centrado donde estaría el completo."""
    small = scale_grid(grid, fraction)
    if rgb is not None:
        small = tint_grid(small, rgb)
    full_rows, _ = grid_size(grid)
    rows, _ = grid_size(small)
    return replace_side(
        scene, side, sprite=small, visible=True, offset=(-((full_rows - rows) // 2), 0)
    )


def _ball_pixels(pixels: _Pixels, x: float, y: float, style: BallAnimationStyle) -> None:
    """Poké Ball de 5×4 píxeles (dos celdas de alto) centrada en ``(x, y)``."""
    pattern = (".TTT.", "TATAT" if style.tier else "TTTTT", "KKWKK", ".BBB.")
    colors = {
        "T": style.top,
        "A": style.accent,
        "K": (40, 40, 40),
        "W": (250, 250, 250),
        "B": style.bottom,
    }
    left = math.floor(x) - 2
    top = (math.floor(y) // 2) * 2 - 2
    for dy, line in enumerate(pattern):
        for dx, code in enumerate(line):
            if code != ".":
                pixels[(left + dx, top + dy)] = colors[code]


def _ball(point: tuple[float, float], style: BallAnimationStyle) -> list[Particle]:
    pixels: _Pixels = {}
    _ball_pixels(pixels, *_to_pixel(point), style)
    return _to_particles(pixels)


def _out_of_ball(scene: SceneModel, side: SideId, height: int, width: int) -> list[SceneModel]:
    """La Poké Ball llega en arco, se abre con un destello y el Pokémon crece."""
    grid = side_view(scene, side).sprite
    box = sprite_box(replace_side(scene, side, visible=True), side)
    center = _center(box)
    start = (height - 2, 0) if side == "player" else (0, width - 1)
    style = _ball_style("pokeball")
    frames = []
    trail: list[tuple[float, float]] = []
    for step in range(1, 7):
        t = step / 6
        point = (
            start[0] + (center[0] - start[0]) * t - 3 * 4 * t * (1 - t),
            start[1] + (center[1] - start[1]) * t,
        )
        dots = [_particle(r, c, "·", style.top) for r, c in trail[-3:]]
        trail.append(point)
        frames.append(_with(scene, dots + _ball(point, style)))
    x, y = _to_pixel(center)
    pixels: _Pixels = {}
    _disc(pixels, x, y, 3.5, (_SPARK[1], _SPARK[2], FLASH_RGB))
    frames.append(_with(scene, _to_particles(pixels) + _ring(center, 1.6, "✦", _SPARK, points=8)))
    pixels = {}
    _disc(pixels, x, y, 7.0, _SPARK, hollow=0.75)
    frames.append(
        _with(scene, _to_particles(pixels) + _ring(center, 3, "✧", _SPARK, points=12, phase=0.3))
    )
    if grid and grid[0]:
        for fraction in (0.3, 0.55, 0.8):
            grown = replace_side(_shrunk(scene, side, grid, fraction, None), side, flash=True)
            frames.append(_with(grown, []))
    frames.append(replace_side(scene, side, visible=True, flash=True))
    return frames


def _into_ball(scene: SceneModel, side: SideId) -> list[SceneModel]:
    """El Pokémon vuelve a su Poké Ball: destello, energía roja y encoge."""
    grid = side_view(scene, side).sprite
    frames = [replace_side(scene, side, flash=True)]
    if grid and grid[0]:
        for fraction in (0.75, 0.5, 0.25):
            frames.append(_shrunk(scene, side, grid, fraction, (232, 64, 72)))
    frames.append(replace_side(scene, side, visible=False))
    return frames


def _slide_in_frames(scene: SceneModel, side: SideId, width: int) -> list[SceneModel]:
    """El salvaje entra deslizándose desde el borde, como al abrirse el combate."""
    shown = replace_side(scene, side, visible=True)
    box = sprite_box(shown, side)
    distance = max(6, width - box.col) * (1 if side == "opponent" else -1)
    steps = 8
    return [
        replace_side(shown, side, offset=(0, round(distance * (1 - k / steps) ** 2)))
        for k in range(1, steps)
    ]


def _slide_out_frames(scene: SceneModel, side: SideId, width: int) -> list[SceneModel]:
    """Sale corriendo del campo por su lado (huida, expulsión)."""
    box = sprite_box(scene, side)
    direction = 1 if side == "opponent" else -1
    distance = (width - box.col) if direction > 0 else (box.right + 1)
    steps = 6
    frames = []
    for k in range(1, steps + 1):
        offset = direction * round(distance * (k / steps) ** 1.5)
        dust = [
            _particle(
                box.bottom,
                box.col + box.width // 2 + direction * (k * 3 - j * 2),
                "·",
                _MIST[j % 3],
            )
            for j in range(3)
        ]
        frames.append(_with(replace_side(scene, side, offset=(0, offset)), dust))
    return frames


def _faint_frames(scene: SceneModel, side: SideId) -> list[SceneModel]:
    """Se hunde tras su plataforma fila a fila hasta desaparecer."""
    height = sprite_box(scene, side).height
    step = max(1, math.ceil(height / 9))
    sinks = list(range(step, height, step)) + [height]
    shaken = [replace_side(scene, side, offset=(0, col)) for col in (1, -1)]
    return shaken + [replace_side(scene, side, sink=sink) for sink in sinks]


def _teleport_frames(scene: SceneModel, side: SideId) -> list[SceneModel]:
    box = sprite_box(scene, side)
    frames = []
    for step in range(6):
        particles = _around(box, "✧", _SPARK, phase=step * 0.7, points=10)
        blinking = replace_side(scene, side, visible=step % 2 == 0, flash=step >= 3)
        frames.append(_with(blinking, particles))
    frames.append(replace_side(scene, side, visible=False))
    return frames


def _ascend_frames(scene: SceneModel, side: SideId) -> list[SceneModel]:
    box = sprite_box(scene, side)
    frames: list[SceneModel] = []
    offset = 0
    while box.bottom + offset >= 0 and len(frames) < 12:
        offset -= max(2, box.height // 4)
        dust = [
            _particle(box.bottom + 1, box.col + box.width // 2 + d, "·", _MIST[abs(d) % 3])
            for d in (-3, 0, 3)
        ]
        frames.append(
            _with(replace_side(scene, side, offset=(offset, 0)), dust if len(frames) < 2 else [])
        )
    frames.append(replace_side(scene, side, visible=False))
    return frames


def _burrow_frames(scene: SceneModel, side: SideId, *, bubbles: bool) -> list[SceneModel]:
    box = sprite_box(scene, side)
    palette = _TYPE_FX["water"].palette if bubbles else _TYPE_FX["ground"].palette
    glyph = "○" if bubbles else "▒"
    frames = []
    for sink in range(2, box.height + 2, 2):
        particles = [
            _particle(
                box.bottom - (sink + k) % 3,
                box.col + k * max(2, box.width // 5),
                glyph,
                palette[k % 3],
            )
            for k in range(6)
        ]
        frames.append(_with(replace_side(scene, side, sink=min(sink, box.height)), particles))
    frames.append(replace_side(scene, side, visible=False))
    return frames


def _blink_out_frames(scene: SceneModel, side: SideId) -> list[SceneModel]:
    frames = [replace_side(scene, side, visible=step % 2 == 0) for step in range(5)]
    frames.append(replace_side(scene, side, visible=False))
    return frames


def _return_frames(scene: SceneModel, side: SideId) -> list[SceneModel]:
    """Reaparece en picado desde arriba tras volar o excavar."""
    box = sprite_box(scene, side)
    return [
        replace_side(scene, side, offset=(-round(box.height * (1 - k / 4)), 0)) for k in range(4)
    ]


def _converge_frames(scene: SceneModel, side: SideId) -> list[SceneModel]:
    """Luz que se concentra en el Pokémon (Rayo Solar y similares)."""
    center = _center(sprite_box(scene, side))
    x, y = _to_pixel(center)
    frames = []
    for radius in (5, 4, 3, 2, 1):
        pixels: _Pixels = {}
        _disc(pixels, x, y, radius * 2.2, _SPARK, hollow=0.8)
        glyphs = _ring(center, radius, "✦" if radius % 2 else "·", _SPARK, points=10, phase=radius)
        frames.append(_with(scene, _to_particles(pixels) + glyphs))
    frames.append(replace_side(scene, side, flash=True))
    return frames


# --- Poké Ball en combate ------------------------------------------------------------


def _throw_frames(
    scene: SceneModel, target: tuple[int, int], height: int, style: BallAnimationStyle
) -> list[SceneModel]:
    """Lanzamiento en arco desde la esquina del jugador hasta el rival."""
    start = (height - 3, 2)
    frames = []
    trail: list[tuple[float, float]] = []
    for step in range(1, 9):
        t = step / 8
        point = (
            start[0] + (target[0] - start[0]) * t - 6 * 4 * t * (1 - t),
            start[1] + (target[1] - start[1]) * t,
        )
        dots = [_particle(r, c, "·", style.accent) for r, c in trail[-3:]]
        trail.append(point)
        frames.append(_with(scene, dots + _ball(point, style)))
    return frames


def _absorb_frames(
    scene: SceneModel,
    side: SideId,
    grid: Grid,
    center: tuple[int, int],
    style: BallAnimationStyle,
) -> list[SceneModel]:
    """La bola se abre sobre el Pokémon y lo absorbe convertido en energía."""
    ball = _ball(center, style)
    frames = [_with(replace_side(scene, side, flash=True), ball)]
    if grid and grid[0]:
        for fraction in (0.75, 0.5, 0.25):
            frames.append(_with(_shrunk(scene, side, grid, fraction, style.absorb), ball))
    frames.append(_with(replace_side(scene, side, visible=False), ball))
    return frames


def _drop_frames(
    scene: SceneModel, start: tuple[int, int], landing: tuple[int, int], style: BallAnimationStyle
) -> list[SceneModel]:
    rows = max(1, landing[0] - start[0])
    steps = max(2, min(5, rows // 2))
    return [
        _with(scene, _ball((start[0] + rows * k / steps, landing[1]), style))
        for k in range(1, steps + 1)
    ]


def _wobble_frames(
    scene: SceneModel, landing: tuple[int, int], style: BallAnimationStyle, wobbles: int
) -> list[SceneModel]:
    frames = []
    for index in range(wobbles):
        tilt = -1 if index % 2 == 0 else 1
        frames.append(_with(scene, _ball((landing[0], landing[1] + tilt), style)))
        frames.append(_with(scene, _ball(landing, style)))
    return frames


def _click_frames(
    scene: SceneModel, landing: tuple[int, int], style: BallAnimationStyle
) -> list[SceneModel]:
    sparks = [
        _particle(landing[0] + dr, landing[1] + dc, "✦" if (dr + dc) % 2 else "✧", style.flash)
        for dr, dc in ((-2, -4), (-3, 0), (-2, 4), (-1, -6), (-1, 6))
    ]
    ball = _ball(landing, style)
    lock = [_particle(landing[0] - 2, landing[1], style.lock_glyph, style.accent)]
    return [
        _with(scene, ball + sparks + lock),
        _with(scene, ball + sparks[::2] + lock),
        _with(scene, ball + lock),
    ]


def _breakout_frames(
    scene: SceneModel, side: SideId, grid: Grid, landing: tuple[int, int], accent: RGB
) -> list[SceneModel]:
    x, y = _to_pixel(landing)
    pixels: _Pixels = {}
    _disc(pixels, x, y, 3.0, (accent, FLASH_RGB, FLASH_RGB))
    burst = [
        _particle(landing[0] + dr, landing[1] + dc, "✦", accent)
        for dr, dc in ((-1, -3), (-2, 0), (-1, 3), (0, -5), (0, 5))
    ]
    frames = [_with(scene, _to_particles(pixels) + burst)]
    if grid and grid[0]:
        for fraction in (0.4, 0.75):
            frames.append(
                _with(
                    replace_side(_shrunk(scene, side, grid, fraction, None), side, flash=True), []
                )
            )
    frames.append(replace_side(scene, side, visible=True, flash=True))
    frames.append(replace_side(scene, side, visible=True, offset=(0, 1)))
    frames.append(replace_side(scene, side, visible=True, offset=(0, -1)))
    return frames
