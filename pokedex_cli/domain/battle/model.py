"""Contrato de datos del combate: estado inmutable, acciones y eventos.

Todo lo que el motor recibe o devuelve vive aquí y es puro: dataclasses
congeladas, enums de texto y tuplas, para que el estado se serialice a JSON
sin pérdida y cada turno sea una función ``estado → (estado, eventos)``.

Los eventos son semánticos, no texto: la presentación decide cómo contarlos
(«¡Es muy eficaz!») y cómo animarlos. Así la CLI y la TUI comparten guion.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Literal, Protocol, Union

SideId = Literal["player", "opponent"]
SIDES: tuple[SideId, SideId] = ("player", "opponent")

# Claves de stats permanentes (mismas que domain.individuality.STAT_KEYS) y de
# escalones de combate, que añaden precisión y evasión.
STAT_KEYS: tuple[str, ...] = ("hp", "atk", "def", "spa", "spd", "spe")
STAGE_KEYS: tuple[str, ...] = ("atk", "def", "spa", "spd", "spe", "accuracy", "evasion")
MIN_STAGE = -6
MAX_STAGE = 6
MAX_MOVES = 4
MAX_TEAM = 6
# Red de seguridad: dos Pokémon sin forma de dañarse no pueden eternizar un
# combate. Al llegar aquí termina en tablas.
MAX_TURNS = 300


def other_side(side: SideId) -> SideId:
    return "opponent" if side == "player" else "player"


class DamageClass(str, Enum):
    PHYSICAL = "physical"
    SPECIAL = "special"
    STATUS = "status"


class Status(str, Enum):
    """Problemas de estado persistentes (uno a la vez, como en los juegos)."""

    NONE = "none"
    BURN = "burn"
    PARALYSIS = "paralysis"
    POISON = "poison"
    TOXIC = "toxic"
    SLEEP = "sleep"
    FREEZE = "freeze"


@dataclass(frozen=True)
class StatChange:
    stat: str  # una de STAGE_KEYS
    change: int


@dataclass(frozen=True)
class Move:
    """Movimiento tal y como lo describe PokeAPI (``/move/{slug}``), ya validado.

    ``category`` es la categoría ``meta`` de PokeAPI con guiones
    (``damage``, ``ailment``, ``net-good-stats``, ``heal``, ``damage-ailment``,
    ``swagger``, ``damage-lower``, ``damage-raise``, ``damage-heal``, ``ohko``,
    ``whole-field-effect``, ``field-effect``, ``force-switch``, ``unique``).
    ``drain`` es un porcentaje del daño causado (negativo = retroceso) y
    ``healing`` un porcentaje de los PS máximos del usuario (negativo = se hiere).
    """

    slug: str
    name: str
    type: str
    damage_class: DamageClass
    power: int | None
    accuracy: int | None  # None: no falla nunca
    pp: int
    priority: int = 0
    target: str = "selected-pokemon"
    category: str = "damage"
    ailment: str = "none"
    ailment_chance: int = 0
    min_hits: int | None = None
    max_hits: int | None = None
    min_turns: int | None = None
    max_turns: int | None = None
    drain: int = 0
    healing: int = 0
    crit_rate: int = 0
    flinch_chance: int = 0
    stat_changes: tuple[StatChange, ...] = ()
    stat_chance: int = 0
    description: str | None = None

    @property
    def is_damaging(self) -> bool:
        return self.damage_class is not DamageClass.STATUS


@dataclass(frozen=True)
class MoveSlot:
    move: Move
    pp: int
    max_pp: int


@dataclass(frozen=True)
class Stats:
    hp: int
    atk: int
    defense: int
    spa: int
    spd: int
    spe: int

    def value(self, key: str) -> int:
        if key == "hp":
            return self.hp
        if key == "atk":
            return self.atk
        if key == "def":
            return self.defense
        if key == "spa":
            return self.spa
        if key == "spd":
            return self.spd
        if key == "spe":
            return self.spe
        raise KeyError(key)

    def as_mapping(self) -> dict[str, int]:
        return {key: self.value(key) for key in STAT_KEYS}

    @classmethod
    def from_mapping(cls, values: Mapping[str, int]) -> Stats:
        return cls(
            hp=int(values["hp"]),
            atk=int(values["atk"]),
            defense=int(values["def"]),
            spa=int(values["spa"]),
            spd=int(values["spd"]),
            spe=int(values["spe"]),
        )


@dataclass(frozen=True)
class Stages:
    """Escalones de combate de −6 a +6; se pierden al retirarse."""

    atk: int = 0
    defense: int = 0
    spa: int = 0
    spd: int = 0
    spe: int = 0
    accuracy: int = 0
    evasion: int = 0

    def value(self, key: str) -> int:
        if key == "atk":
            return self.atk
        if key == "def":
            return self.defense
        if key == "spa":
            return self.spa
        if key == "spd":
            return self.spd
        if key == "spe":
            return self.spe
        if key == "accuracy":
            return self.accuracy
        if key == "evasion":
            return self.evasion
        raise KeyError(key)

    def shifted(self, key: str, delta: int) -> tuple[Stages, int]:
        """Aplica ``delta`` acotado a ±6 y devuelve el cambio efectivo."""
        current = self.value(key)
        target = max(MIN_STAGE, min(MAX_STAGE, current + delta))
        applied = target - current
        field_name = "defense" if key == "def" else key
        return replace(self, **{field_name: target}), applied


@dataclass(frozen=True)
class Volatile:
    """Estado que solo vive mientras el Pokémon sigue en el campo."""

    confusion_turns: int = 0
    flinched: bool = False
    protected: bool = False
    protect_streak: int = 0
    charging: str | None = None  # slug del movimiento de dos turnos en carga
    semi_invulnerable: bool = False  # Vuelo, Excavar… durante la carga
    recharging: bool = False  # Hiperrayo y compañía
    trapped_turns: int = 0
    trapped_by: str | None = None  # nombre del movimiento que lo atrapa
    seeded: bool = False  # Drenadoras
    focus_energy: bool = False
    last_move: str | None = None


@dataclass(frozen=True)
class LearnsetEntry:
    level: int
    move: Move


@dataclass(frozen=True)
class Combatant:
    """Un Pokémon en combate. ``key`` es único en el combate (``p0``, ``o2``…)."""

    key: str
    species: str
    form: str
    name: str
    level: int
    types: tuple[str, ...]
    base_stats: Stats
    ivs: Stats
    nature: str
    stats: Stats
    hp: int
    moves: tuple[MoveSlot, ...]
    shiny: bool = False
    gender: str | None = None
    ability: str | None = None
    status: Status = Status.NONE
    # Turnos de sueño restantes, o contador de Tóxico (n/16 por turno).
    status_turns: int = 0
    stages: Stages = Stages()
    volatile: Volatile = Volatile()
    capture_id: int | None = None  # solo las capturas del jugador
    base_experience: int = 64
    growth_rate: str = "medium"
    experience: int = 0
    # Movimientos que puede aprender por nivel; solo para movesets automáticos
    # del jugador (al subir de nivel se recalculan). Vacío para el resto.
    learnset: tuple[LearnsetEntry, ...] = ()
    custom_moves: bool = False

    @property
    def max_hp(self) -> int:
        return self.stats.hp

    @property
    def fainted(self) -> bool:
        return self.hp <= 0


@dataclass(frozen=True)
class Side:
    team: tuple[Combatant, ...]
    active: int = 0
    reflect_turns: int = 0
    light_screen_turns: int = 0
    trainer: str | None = None  # nombre del entrenador rival; None para jugador o salvaje

    @property
    def current(self) -> Combatant:
        return self.team[self.active]

    @property
    def able(self) -> tuple[int, ...]:
        """Índices de los miembros que aún pueden luchar."""
        return tuple(index for index, member in enumerate(self.team) if not member.fainted)

    def replace_member(self, index: int, combatant: Combatant) -> Side:
        team = list(self.team)
        team[index] = combatant
        return replace(self, team=tuple(team))

    def with_current(self, combatant: Combatant) -> Side:
        return self.replace_member(self.active, combatant)


class BattleKind(str, Enum):
    WILD = "wild"
    TRAINER = "trainer"
    EXHIBITION = "exhibition"


class Phase(str, Enum):
    CHOOSE_ACTION = "choose_action"
    CHOOSE_REPLACEMENT = "choose_replacement"  # el jugador debe sacar a otro
    FINISHED = "finished"


class Outcome(str, Enum):
    WON = "won"
    LOST = "lost"
    ESCAPED = "escaped"
    CAPTURED = "captured"
    OPPONENT_FLED = "opponent_fled"
    DRAW = "draw"


@dataclass(frozen=True)
class BattleState:
    kind: BattleKind
    player: Side
    opponent: Side
    turn: int = 0
    phase: Phase = Phase.CHOOSE_ACTION
    outcome: Outcome | None = None
    escape_attempts: int = 0
    # Para repartir EXP: clave rival → claves del jugador que lo enfrentaron.
    participants: tuple[tuple[str, tuple[str, ...]], ...] = ()
    award_experience: bool = True

    def side(self, side_id: SideId) -> Side:
        return self.player if side_id == "player" else self.opponent

    def with_side(self, side_id: SideId, side: Side) -> BattleState:
        if side_id == "player":
            return replace(self, player=side)
        return replace(self, opponent=side)

    @property
    def finished(self) -> bool:
        return self.phase is Phase.FINISHED


# --- Acciones ---------------------------------------------------------------


@dataclass(frozen=True)
class UseMove:
    slot: int  # índice en ``Combatant.moves``; sin PP en ninguno → Forcejeo


@dataclass(frozen=True)
class SwitchTo:
    index: int  # índice en ``Side.team``


@dataclass(frozen=True)
class Run:
    pass


@dataclass(frozen=True)
class SpendTurn:
    """El jugador gastó el turno fuera del motor (p. ej. una Pokéball fallida):
    solo actúa el rival y se aplican los efectos de fin de turno."""

    reason: str = "item"


Action = Union[UseMove, SwitchTo, Run, SpendTurn]


# --- Eventos ----------------------------------------------------------------


class DamageCause(str, Enum):
    MOVE = "move"
    RECOIL = "recoil"
    CONFUSION = "confusion"
    BURN = "burn"
    POISON = "poison"
    LEECH_SEED = "leech_seed"
    TRAP = "trap"
    SACRIFICE = "sacrifice"  # Explosión / Autodestrucción


class HealCause(str, Enum):
    MOVE = "move"
    DRAIN = "drain"
    LEECH_SEED = "leech_seed"
    REST = "rest"


class FailReason(str, Enum):
    GENERIC = "generic"  # «¡Pero falló!»
    UNSUPPORTED = "unsupported"  # efecto sin implementar: «¡Pero no tuvo efecto!»
    NOTHING_HAPPENED = "nothing_happened"  # Salpicadura: «¡Pero no pasó nada!»
    TARGET_AWAKE = "target_awake"  # Comesueños contra un rival despierto
    LEVEL_GAP = "level_gap"  # golpe fulminante contra un nivel superior


class ImmobileReason(str, Enum):
    ASLEEP = "asleep"
    FROZEN = "frozen"
    PARALYZED = "paralyzed"
    FLINCHED = "flinched"
    RECHARGING = "recharging"


@dataclass(frozen=True)
class BattleStarted:
    kind: BattleKind
    trainer: str | None


@dataclass(frozen=True)
class TurnStarted:
    turn: int


@dataclass(frozen=True)
class SentOut:
    side: SideId
    key: str
    name: str
    species: str
    form: str
    shiny: bool
    level: int


@dataclass(frozen=True)
class Recalled:
    side: SideId
    key: str
    name: str


@dataclass(frozen=True)
class MoveUsed:
    side: SideId
    key: str
    name: str
    move: str  # slug
    move_name: str
    move_type: str
    damage_class: DamageClass


@dataclass(frozen=True)
class NoMovesLeft:
    side: SideId
    key: str
    name: str


@dataclass(frozen=True)
class MoveMissed:
    side: SideId  # quien atacaba
    key: str
    name: str


@dataclass(frozen=True)
class MoveFailed:
    side: SideId
    key: str
    name: str
    reason: FailReason


@dataclass(frozen=True)
class NoEffect:
    """Inmunidad de tipo: «No afecta a X…». ``side``/``key`` son del objetivo."""

    side: SideId
    key: str
    name: str


@dataclass(frozen=True)
class ProtectionRaised:
    side: SideId
    key: str
    name: str


@dataclass(frozen=True)
class Protected:
    side: SideId
    key: str
    name: str


@dataclass(frozen=True)
class Damaged:
    side: SideId
    key: str
    name: str
    amount: int
    hp_before: int
    hp_after: int
    max_hp: int
    cause: DamageCause = DamageCause.MOVE
    effectiveness: float = 1.0
    critical: bool = False


@dataclass(frozen=True)
class HitCount:
    side: SideId  # objetivo
    key: str
    name: str
    hits: int


@dataclass(frozen=True)
class OneHitKO:
    side: SideId  # objetivo
    key: str
    name: str


@dataclass(frozen=True)
class Healed:
    side: SideId
    key: str
    name: str
    amount: int
    hp_before: int
    hp_after: int
    max_hp: int
    cause: HealCause = HealCause.MOVE


@dataclass(frozen=True)
class StatusInflicted:
    side: SideId
    key: str
    name: str
    status: Status


@dataclass(frozen=True)
class StatusCured:
    """Se despierta, se descongela… ``status`` es el que pierde."""

    side: SideId
    key: str
    name: str
    status: Status


@dataclass(frozen=True)
class StatusBlocked:
    """No se pudo aplicar: ya tenía un estado (``already``) o es inmune."""

    side: SideId
    key: str
    name: str
    status: Status
    already: bool


@dataclass(frozen=True)
class Immobilized:
    side: SideId
    key: str
    name: str
    reason: ImmobileReason


@dataclass(frozen=True)
class Confused:
    side: SideId
    key: str
    name: str


@dataclass(frozen=True)
class ConfusionCheck:
    side: SideId
    key: str
    name: str


@dataclass(frozen=True)
class ConfusionHurt:
    side: SideId
    key: str
    name: str


@dataclass(frozen=True)
class ConfusionEnded:
    side: SideId
    key: str
    name: str


@dataclass(frozen=True)
class StatChanged:
    side: SideId
    key: str
    name: str
    stat: str  # una de STAGE_KEYS
    delta: int  # cambio efectivo, nunca 0


@dataclass(frozen=True)
class StatUnchanged:
    side: SideId
    key: str
    name: str
    stat: str
    rising: bool


@dataclass(frozen=True)
class StatsReset:
    pass


@dataclass(frozen=True)
class PumpedUp:
    side: SideId
    key: str
    name: str


@dataclass(frozen=True)
class Charging:
    side: SideId
    key: str
    name: str
    move: str
    move_name: str


@dataclass(frozen=True)
class ScreenRaised:
    side: SideId
    screen: str  # "reflect" | "light-screen"


@dataclass(frozen=True)
class ScreenFaded:
    side: SideId
    screen: str


@dataclass(frozen=True)
class Seeded:
    side: SideId
    key: str
    name: str


@dataclass(frozen=True)
class Trapped:
    side: SideId
    key: str
    name: str
    move_name: str


@dataclass(frozen=True)
class Released:
    side: SideId
    key: str
    name: str
    move_name: str


@dataclass(frozen=True)
class Teleported:
    side: SideId
    key: str
    name: str


@dataclass(frozen=True)
class ForcedOut:
    """Rugido/Remolino: en salvaje acaba el combate; contra entrenador, relevo."""

    side: SideId  # quien sale despedido
    key: str
    name: str


@dataclass(frozen=True)
class Fainted:
    side: SideId
    key: str
    name: str


@dataclass(frozen=True)
class ExperienceGained:
    key: str
    name: str
    amount: int


@dataclass(frozen=True)
class LeveledUp:
    key: str
    name: str
    level: int
    # Subida de cada stat respecto al nivel anterior, en orden de STAT_KEYS.
    deltas: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class MoveLearned:
    key: str
    name: str
    move_name: str
    forgot: str | None = None  # nombre del movimiento olvidado, si lo hay


@dataclass(frozen=True)
class EscapeAttempted:
    success: bool
    forbidden: bool = False  # combate contra entrenador: no se puede huir


@dataclass(frozen=True)
class BallThrown:
    ball_slug: str
    ball_name: str
    shakes: int
    caught: bool


@dataclass(frozen=True)
class OpponentFled:
    name: str


@dataclass(frozen=True)
class ReplacementNeeded:
    side: SideId


@dataclass(frozen=True)
class BattleEnded:
    outcome: Outcome


BattleEvent = Union[
    BattleStarted,
    TurnStarted,
    SentOut,
    Recalled,
    MoveUsed,
    NoMovesLeft,
    MoveMissed,
    MoveFailed,
    NoEffect,
    ProtectionRaised,
    Protected,
    Damaged,
    HitCount,
    OneHitKO,
    Healed,
    StatusInflicted,
    StatusCured,
    StatusBlocked,
    Immobilized,
    Confused,
    ConfusionCheck,
    ConfusionHurt,
    ConfusionEnded,
    StatChanged,
    StatUnchanged,
    StatsReset,
    PumpedUp,
    Charging,
    ScreenRaised,
    ScreenFaded,
    Seeded,
    Trapped,
    Released,
    Teleported,
    ForcedOut,
    Fainted,
    ExperienceGained,
    LeveledUp,
    MoveLearned,
    EscapeAttempted,
    BallThrown,
    OpponentFled,
    ReplacementNeeded,
    BattleEnded,
]


@dataclass(frozen=True)
class TurnResult:
    state: BattleState
    events: tuple[BattleEvent, ...]


class RandomSource(Protocol):
    """El mismo protocolo de azar que el resto del dominio."""

    def random(self) -> float: ...

    def randint(self, low: int, high: int) -> int: ...
