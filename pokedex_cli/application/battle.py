"""Casos de uso del modo combate: preparar, empezar, jugar y describir.

- ``PrepareBattle`` monta los combatientes fuera de transacción; puede usar la
  red a través de sus puertos (perfiles de especie y movimientos).
- ``StartBattle`` y ``TakeBattleTurn`` son, cada uno, una sola transacción
  ``BEGIN IMMEDIATE``: comprueban que el combate y su encuentro siguen
  vigentes, aplican el motor puro del dominio y guardan estado, EXP, récord y
  encuentro de forma atómica.
- ``DescribeBattle`` reanuda el combate guardado y descarta el obsoleto.
- ``ExhibitionSession`` y ``PersistedSession`` dan a la CLI y a la TUI la misma
  forma de jugar un combate en memoria o guardado.

El estado se guarda con el codec del dominio dentro de un envoltorio propio,
``{"v": 1, "state": …, "context": …}``: el contexto lleva los datos de la
especie salvaje que necesita una Pokéball lanzada en pleno combate.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, Union

from pokedex_cli.application.capture import (
    CaptureCommand,
    CapturedIndividual,
    CaptureResult,
    CaptureStatus,
)
from pokedex_cli.application.movesets import (
    DEFAULT_TYPES,
    CustomMovesets,
    LevelMoves,
    MoveSource,
    SpeciesSource,
    capture_moves,
    decoded_list,
    level_moves,
    species_name,
    species_types,
    species_with_learnset,
)
from pokedex_cli.domain.battle import ai, codec, engine
from pokedex_cli.domain.battle.combatants import build_combatant
from pokedex_cli.domain.battle.mechanics import battle_catch_multiplier
from pokedex_cli.domain.battle.model import (
    MAX_TEAM,
    STAT_KEYS,
    Action,
    BallThrown,
    BattleEvent,
    BattleKind,
    BattleState,
    Combatant,
    LearnsetEntry,
    Move,
    OpponentFled,
    Outcome,
    Phase,
    RandomSource,
    Run,
    Side,
    SpendTurn,
    Stats,
    SwitchTo,
    TurnResult,
    TurnStarted,
    UseMove,
)
from pokedex_cli.domain.battle.movesets import fallback_moveset, learnable, wild_moveset
from pokedex_cli.domain.battle.trainers import SpeciesCandidate, plan_trainer
from pokedex_cli.domain.identity import normalize_species
from pokedex_cli.domain.individuality import (
    Nature,
    derive_ability,
    derive_gender,
    derive_ivs_nature,
    gender_from_roll,
    roll_ability,
    roll_ivs,
    roll_nature,
)
from pokedex_cli.domain.progression import (
    DEFAULT_BASE_EXPERIENCE,
    MAX_LEVEL,
    STARTING_LEVEL,
    experience_for_level,
    level_for_experience,
)

CaptureView = dict[str, Any]
# Un salvaje legendario aparece al menos a este nivel, el mismo que usa la captura.
LEGENDARY_LEVEL = 50
# Nivel de una especie en exhibición si no se pide otro.
EXHIBITION_LEVEL = 50
# Sin stats de la especie (sin datos ni conexión), una captura lucha con 50 en
# todo y como tipo Normal: puede combatir igual, aunque no sea fiel.
DEFAULT_BASE_STAT = 50
ENVELOPE_VERSION = 1
MAX_SHAKES = 3
# Formas que solo existen en combate o en eventos: no las saca un entrenador
# cualquiera (un Charizard Mega a nivel 10 rompería la gracia).
BATTLE_ONLY_FORMS: tuple[str, ...] = ("mega", "gmax", "primal", "eternamax", "totem")
STALE_REASON = "El Pokémon salvaje de ese combate ya no está."
# Una sesión abierta en otra ventana sobre un combate que ya terminó no debe
# jugar turnos en el combate que haya empezado después.
REPLACED_REASON = "Ese combate ya terminó en otra ventana."


# --- Órdenes, estados y resultados --------------------------------------------


@dataclass(frozen=True)
class Fight:
    slot: int  # índice del movimiento del activo


@dataclass(frozen=True)
class Switch:
    index: int  # índice en el equipo


@dataclass(frozen=True)
class ThrowBall:
    ball_slug: str
    ball_name: str
    multiplier: float


@dataclass(frozen=True)
class Flee:
    pass


@dataclass(frozen=True)
class Replace:
    index: int  # a quién sacar tras un desmayo


@dataclass(frozen=True)
class Auto:
    """La IA elige por el jugador (también el relevo tras un desmayo)."""


BattleCommand = Union[Fight, Switch, ThrowBall, Flee, Replace, Auto]


class BattleStatus(str, Enum):
    NO_TEAM = "no_team"
    NO_ENCOUNTER = "no_encounter"
    ALREADY_CAPTURED = "already_captured"
    NO_SPECIES_DATA = "no_species_data"
    NO_TRAINER_POOL = "no_trainer_pool"
    # Exhibición con un id de captura que no existe.
    NO_CAPTURE = "no_capture"
    BATTLE_IN_PROGRESS = "battle_in_progress"
    STARTED = "started"
    NO_BATTLE = "no_battle"
    STALE = "stale"
    INVALID_ACTION = "invalid_action"
    NO_STOCK = "no_stock"
    APPLIED = "applied"
    FINISHED = "finished"


@dataclass(frozen=True)
class CaptureContext:
    """Datos de la especie salvaje que necesita una Pokéball lanzada en combate."""

    capture_rate: int | None
    speed: int | None
    is_legendary: bool
    is_mythical: bool
    growth_rate: str | None
    gender_rate: int | None
    abilities: tuple[str, ...]
    encounter_level: int


@dataclass(frozen=True)
class BattleSetup:
    """Combate preparado y aún sin empezar (``StartBattle`` lo abre y lo guarda)."""

    state: BattleState
    encounter_seen_at: str | None = None
    capture: CaptureContext | None = None

    @property
    def kind(self) -> BattleKind:
        return self.state.kind

    @property
    def trainer(self) -> str | None:
        return self.state.opponent.trainer


@dataclass(frozen=True)
class BattleView:
    kind: BattleKind
    state: BattleState
    trainer: str | None
    # Identidad del combate guardado (su ``started_at``): la sesión la usa
    # para no jugar turnos de otro combate. None en vistas en memoria.
    started_at: str | None = None


@dataclass(frozen=True)
class TurnOutcome:
    status: BattleStatus
    events: tuple[BattleEvent, ...]
    state: BattleState | None
    reason: str | None = None
    capture_id: int | None = None
    # Capturas que han subido de nivel en la BD este turno (para animar
    # evoluciones al acabar).
    leveled: tuple[int, ...] = ()


# --- Puertos ------------------------------------------------------------------


class TeamQueries(Protocol):
    def team(self) -> list[CaptureView]: ...

    def resolve(self, identifier: str, form: str | None) -> CaptureView | None: ...


class SpeciesPool(Protocol):
    def battle_pool(self) -> list[dict[str, Any]]: ...


class BattleRepository(Protocol):
    def load_in_transaction(self, connection: sqlite3.Connection) -> dict[str, Any] | None: ...

    def save_in_transaction(
        self, connection: sqlite3.Connection, row: Mapping[str, Any]
    ) -> None: ...

    def clear_in_transaction(self, connection: sqlite3.Connection) -> None: ...


class EncounterRepository(Protocol):
    def load_in_transaction(self, connection: sqlite3.Connection) -> dict[str, Any] | None: ...

    def clear_in_transaction(self, connection: sqlite3.Connection) -> None: ...


class CaptureAttempt(Protocol):
    def attempt_in_transaction(
        self, connection: sqlite3.Connection, command: CaptureCommand
    ) -> CaptureResult: ...


class CaptureProgress(Protocol):
    def progress_in_transaction(
        self, connection: sqlite3.Connection, capture_id: int
    ) -> tuple[int, int] | None:
        """EXP y nivel guardados ahora; ``None`` si la captura no existe."""
        ...

    def apply_experience_in_transaction(
        self, connection: sqlite3.Connection, capture_id: int, experience: int, level: int
    ) -> bool: ...


class BattleRecords(Protocol):
    def record_in_transaction(
        self, connection: sqlite3.Connection, *, kind: str, outcome: str, at: str
    ) -> None: ...


class TurnTaker(Protocol):
    def execute(
        self, command: BattleCommand, *, now: datetime | str, battle_id: str | None = None
    ) -> TurnOutcome: ...


class BattleSession(Protocol):
    """Lo que necesita una interfaz para jugar un combate, guardado o no."""

    @property
    def state(self) -> BattleState: ...

    @property
    def kind(self) -> BattleKind: ...

    @property
    def trainer(self) -> str | None: ...

    @property
    def opening(self) -> tuple[BattleEvent, ...]: ...

    def take(self, command: BattleCommand) -> TurnOutcome: ...


# --- Preparación --------------------------------------------------------------


@dataclass(frozen=True)
class _Blueprint:
    """Todo lo de un combatiente salvo los movimientos, que se piden de una
    vez para todo el combate (así las descargas van en paralelo)."""

    key: str
    species: str
    form: str
    name: str
    level: int
    types: tuple[str, ...]
    base_stats: Stats
    ivs: Stats
    nature: str
    entries: LevelMoves
    shiny: bool = False
    gender: str | None = None
    ability: str | None = None
    capture_id: int | None = None
    base_experience: int = DEFAULT_BASE_EXPERIENCE
    growth_rate: str = "medium"
    experience: int = 0
    custom: tuple[str, ...] = ()
    # Capturas del jugador: moveset propio o automático con learnset. El resto
    # (salvajes, entrenadores, especies sueltas): los cuatro últimos
    # aprendidos, como en Gen 3.
    owned: bool = False

    def slugs(self) -> list[str]:
        if not self.owned:
            return learnable(self.entries, self.level)
        if self.custom:
            return list(self.custom)
        return [slug for slug, _ in self.entries]

    def build(self, catalog: Mapping[str, Move]) -> Combatant:
        learnset: tuple[LearnsetEntry, ...] = ()
        custom = False
        if self.owned:
            plan = capture_moves(
                custom=self.custom,
                entries=self.entries,
                level=self.level,
                types=self.types,
                catalog=catalog,
            )
            moves, learnset, custom = plan.moves, plan.learnset, plan.custom
        else:
            # Un learnset sin nada soportado (Ditto) también acaba en la reserva.
            moves = wild_moveset(self.entries, self.level, catalog) or fallback_moveset(self.types)
        return build_combatant(
            key=self.key,
            species=self.species,
            form=self.form,
            name=self.name,
            level=self.level,
            types=self.types,
            base_stats=self.base_stats,
            ivs=self.ivs,
            nature=self.nature,
            moves=moves,
            shiny=self.shiny,
            gender=self.gender,
            ability=self.ability,
            capture_id=self.capture_id,
            base_experience=self.base_experience,
            growth_rate=self.growth_rate,
            experience=self.experience,
            learnset=learnset,
            custom_moves=custom,
        )


class PrepareBattle:
    """Monta combates a partir del equipo, el encuentro y la caché de especies.

    Va fuera de transacción y puede tardar (perfiles sin learnset que hay que
    refrescar, movimientos sin caché): ``on_progress`` recibe frases como
    «Preparando a Noctowl…» para que la interfaz cuente qué hace.
    """

    def __init__(
        self,
        *,
        collection: TeamQueries,
        species: SpeciesSource,
        moves: MoveSource,
        movesets: CustomMovesets,
        pool: SpeciesPool,
        rng: RandomSource,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self._collection = collection
        self._species = species
        self._moves = moves
        self._movesets = movesets
        self._pool = pool
        self._rng = rng
        self._on_progress = on_progress

    def player_side(self) -> tuple[Combatant, ...]:
        """Un combatiente por miembro del equipo (``p0``…, en el orden de ``team()``)."""
        blueprints = self._team_blueprints(self._team_rows())
        catalog = self._catalog(blueprints)
        return tuple(blueprint.build(catalog) for blueprint in blueprints)

    def wild(self, encounter: Mapping[str, Any] | None) -> BattleSetup | BattleStatus:
        """Combate contra el encuentro actual.

        El salvaje sale al mismo nivel que usaría la captura y con la misma
        individualidad cada vez que se prepare (semilla del encuentro).
        """
        if encounter is None or not encounter.get("species") or not encounter.get("seen_at"):
            return BattleStatus.NO_ENCOUNTER
        if bool(encounter.get("captured")):
            return BattleStatus.ALREADY_CAPTURED
        rows = self._team_rows()
        if not rows:
            return BattleStatus.NO_TEAM
        species = str(encounter["species"])
        form = str(encounter.get("form") or "regular")
        seen_at = str(encounter["seen_at"])
        self._report(f"Preparando a {species_name(species)} salvaje…")
        data = species_with_learnset(self._species, species, form)
        stats = _base_stats(data)
        if data is None or stats is None:
            return BattleStatus.NO_SPECIES_DATA
        seed = f"wild:{species}:{form}:{seen_at}"
        ivs, nature = derive_ivs_nature(seed)
        wild = self._unowned_blueprint(
            "o0",
            species,
            form,
            _wild_level(data),
            data,
            stats,
            ivs=Stats.from_mapping(ivs),
            nature=nature,
            gender=derive_gender(seed, _gender_rate(data)),
            ability=derive_ability(seed, _abilities(data)),
            shiny=bool(encounter.get("shiny")),
        )
        team = self._team_blueprints(rows)
        catalog = self._catalog([wild, *team])
        state = BattleState(
            kind=BattleKind.WILD,
            player=Side(tuple(blueprint.build(catalog) for blueprint in team)),
            opponent=Side((wild.build(catalog),)),
        )
        return BattleSetup(state=state, encounter_seen_at=seen_at, capture=_capture_context(data))

    def trainer(self) -> BattleSetup | BattleStatus:
        """Entrenador generado con especies de la caché, a la altura del equipo.

        Sin legendarios, singulares ni formas de combate (Mega, Gigamax…). La
        individualidad de sus Pokémon se tira con el azar inyectado.
        """
        rows = self._team_rows()
        if not rows:
            return BattleStatus.NO_TEAM
        pool = _trainer_candidates(self._pool.battle_pool())
        plan = plan_trainer(pool, [int(row["level"]) for row in rows], self._rng)
        if plan is None:
            return BattleStatus.NO_TRAINER_POOL
        self._report(f"Preparando a {plan.display_name}…")
        members: list[_Blueprint] = []
        for species, form, level in plan.members:
            data = species_with_learnset(self._species, species, form)
            stats = _base_stats(data)
            if data is None or stats is None:
                continue
            members.append(
                self._unowned_blueprint(
                    f"o{len(members)}",
                    species,
                    form,
                    level,
                    data,
                    stats,
                    ivs=Stats.from_mapping(roll_ivs(self._rng)),
                    nature=roll_nature(self._rng),
                    gender=gender_from_roll(_gender_rate(data), self._rng.random()),
                    ability=roll_ability(_abilities(data), self._rng),
                )
            )
        if not members:
            return BattleStatus.NO_TRAINER_POOL
        team = self._team_blueprints(rows)
        catalog = self._catalog([*members, *team])
        state = BattleState(
            kind=BattleKind.TRAINER,
            player=Side(tuple(blueprint.build(catalog) for blueprint in team)),
            opponent=Side(
                tuple(blueprint.build(catalog) for blueprint in members),
                trainer=plan.display_name,
            ),
        )
        return BattleSetup(state=state)

    def exhibition(
        self, left: str | None = None, right: str | None = None, level: int | None = None
    ) -> BattleState | BattleStatus:
        """Exhibición de un Pokémon por lado, sin EXP ni nada que guardar.

        Cada lado es un id de captura (sus stats, nivel y moveset reales;
        ``level`` lo sobrescribe) o un nombre de especie (individuo fijo por
        semilla, a ``level`` o 50). Sin izquierda lucha el líder del equipo y
        sin derecha, una especie al azar de la caché. La izquierda es el jugador.
        """
        fight_level = None if level is None else _clamp_level(level)
        player = self._exhibition_side(left, "p0", fight_level)
        if isinstance(player, BattleStatus):
            return player
        opponent = self._exhibition_side(right, "o0", fight_level)
        if isinstance(opponent, BattleStatus):
            return opponent
        catalog = self._catalog([player, opponent])
        return BattleState(
            kind=BattleKind.EXHIBITION,
            player=Side((player.build(catalog),)),
            opponent=Side((opponent.build(catalog),)),
            award_experience=False,
        )

    # --- Piezas -------------------------------------------------------------

    def _report(self, message: str) -> None:
        if self._on_progress is not None:
            self._on_progress(message)

    def _team_rows(self) -> list[CaptureView]:
        return list(self._collection.team())[:MAX_TEAM]

    def _team_blueprints(self, rows: Sequence[Mapping[str, Any]]) -> list[_Blueprint]:
        return [self._owned_blueprint(row, f"p{index}") for index, row in enumerate(rows)]

    def _catalog(self, blueprints: Sequence[_Blueprint]) -> Mapping[str, Move]:
        slugs = [slug for blueprint in blueprints for slug in blueprint.slugs()]
        if not slugs:
            return {}
        self._report("Preparando los movimientos…")
        return self._moves.execute_many(slugs)

    def _owned_blueprint(
        self, row: Mapping[str, Any], key: str, *, level: int | None = None
    ) -> _Blueprint:
        """Una captura del jugador con sus stats reales; ``level`` sobrescribe
        su nivel (exhibición) y entonces la EXP es la justa de ese nivel."""
        capture_id = int(row["id"])
        species, form = str(row["species"]), str(row["form"])
        nickname = row.get("nickname")
        name = str(nickname) if nickname else species_name(species)
        self._report(f"Preparando a {name}…")
        data = species_with_learnset(self._species, species, form)
        growth = str(_field(data, "growth_rate") or row.get("growth_rate") or "medium")
        own_level = _clamp_level(int(row.get("level") or STARTING_LEVEL))
        if level is None:
            fight_level = own_level
            # Capturas antiguas guardan menos EXP que el suelo de su nivel.
            experience = max(
                int(row.get("experience") or 0), experience_for_level(growth, own_level)
            )
        else:
            fight_level = level
            experience = experience_for_level(growth, level)
        return _Blueprint(
            key=key,
            species=species,
            form=form,
            name=name,
            level=fight_level,
            types=species_types(data) or _row_types(row) or DEFAULT_TYPES,
            base_stats=_base_stats(data) or _base_stats(row) or _default_base_stats(),
            ivs=Stats.from_mapping(row["ivs"]),
            nature=_nature_name(row.get("nature")),
            entries=level_moves(data),
            shiny=bool(row.get("shiny")),
            gender=_optional_text(row.get("gender")),
            ability=_optional_text(row.get("ability")),
            capture_id=capture_id,
            base_experience=_base_experience(data, row),
            growth_rate=growth,
            experience=experience,
            custom=tuple(self._movesets.get(capture_id)),
            owned=True,
        )

    def _unowned_blueprint(
        self,
        key: str,
        species: str,
        form: str,
        level: int,
        data: Mapping[str, Any],
        stats: Stats,
        *,
        ivs: Stats,
        nature: Nature,
        gender: str | None,
        ability: str | None,
        shiny: bool = False,
    ) -> _Blueprint:
        growth = str(data.get("growth_rate") or "medium")
        return _Blueprint(
            key=key,
            species=species,
            form=form,
            name=species_name(species),
            level=level,
            types=species_types(data) or DEFAULT_TYPES,
            base_stats=stats,
            ivs=ivs,
            nature=nature.name,
            entries=level_moves(data),
            shiny=shiny,
            gender=gender,
            ability=ability,
            base_experience=_base_experience(data, {}),
            growth_rate=growth,
            experience=experience_for_level(growth, level),
        )

    def _exhibition_side(
        self, identifier: str | None, key: str, level: int | None
    ) -> _Blueprint | BattleStatus:
        text = (identifier or "").strip().lstrip("#")
        if not text:
            if key.startswith("p"):
                rows = self._team_rows()
                if not rows:
                    return BattleStatus.NO_TEAM
                return self._owned_blueprint(rows[0], key, level=level)
            pool = self._pool.battle_pool()
            if not pool:
                return BattleStatus.NO_TRAINER_POOL
            entry = pool[self._rng.randint(0, len(pool) - 1)]
            species, form = str(entry["species"]), str(entry.get("form") or "regular")
            return self._species_blueprint(species, form, key, level or EXHIBITION_LEVEL)
        if text.isdecimal():
            row = self._collection.resolve(text, None)
            if row is None:
                return BattleStatus.NO_CAPTURE
            return self._owned_blueprint(row, key, level=level)
        try:
            species = normalize_species(text)
        except ValueError:
            return BattleStatus.NO_SPECIES_DATA
        return self._species_blueprint(species, "regular", key, level or EXHIBITION_LEVEL)

    def _species_blueprint(
        self, species: str, form: str, key: str, level: int
    ) -> _Blueprint | BattleStatus:
        self._report(f"Preparando a {species_name(species)}…")
        data = species_with_learnset(self._species, species, form)
        stats = _base_stats(data)
        if data is None or stats is None:
            return BattleStatus.NO_SPECIES_DATA
        seed = f"exhibition:{species}:{form}:{level}"
        ivs, nature = derive_ivs_nature(seed)
        return self._unowned_blueprint(
            key,
            species,
            form,
            level,
            data,
            stats,
            ivs=Stats.from_mapping(ivs),
            nature=nature,
            gender=derive_gender(seed, _gender_rate(data)),
            ability=derive_ability(seed, _abilities(data)),
        )


# --- Combate guardado ---------------------------------------------------------


@dataclass(frozen=True)
class _Stored:
    state: BattleState
    context: CaptureContext | None
    encounter_seen_at: str | None
    started_at: str


class _Missing(Enum):
    NONE = "none"  # no hay combate guardado
    CORRUPT = "corrupt"  # había uno ilegible y se ha borrado
    STALE = "stale"  # era salvaje y su encuentro ya no está; se ha borrado


def _load(
    connection: sqlite3.Connection, battles: BattleRepository, encounters: EncounterRepository
) -> _Stored | _Missing:
    """El combate vigente; uno ilegible u obsoleto se borra en esta transacción."""
    row = battles.load_in_transaction(connection)
    if row is None:
        return _Missing.NONE
    stored = _decode_row(row)
    if stored is None:
        battles.clear_in_transaction(connection)
        return _Missing.CORRUPT
    if stored.state.kind is BattleKind.WILD and not _is_current(
        encounters.load_in_transaction(connection), stored.encounter_seen_at
    ):
        battles.clear_in_transaction(connection)
        return _Missing.STALE
    return stored


def _is_current(encounter: Mapping[str, Any] | None, seen_at: str | None) -> bool:
    """¿Sigue ahí el mismo encuentro (``seen_at``) y sin capturar?"""
    return (
        encounter is not None
        and seen_at is not None
        and encounter.get("seen_at") == seen_at
        and not bool(encounter.get("captured"))
    )


def _battle_row(
    state: BattleState,
    context: CaptureContext | None,
    encounter_seen_at: str | None,
    started_at: str,
    updated_at: str,
) -> dict[str, Any]:
    envelope = {
        "v": ENVELOPE_VERSION,
        "state": codec.state_to_dict(state),
        "context": None if context is None else _context_to_dict(context),
    }
    return {
        "kind": state.kind.value,
        "state_json": json.dumps(envelope, ensure_ascii=False),
        "encounter_seen_at": encounter_seen_at,
        "started_at": started_at,
        "updated_at": updated_at,
    }


def _decode_row(row: Mapping[str, Any]) -> _Stored | None:
    """Fila guardada → estado validado; ``None`` ante cualquier incoherencia."""
    try:
        envelope = json.loads(str(row["state_json"]))
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(envelope, dict) or not _is_int(envelope.get("v"), ENVELOPE_VERSION):
        return None
    state = codec.state_from_dict(envelope.get("state"))
    if state is None or state.finished or state.kind.value != row.get("kind"):
        return None
    started_at = row.get("started_at")
    if not isinstance(started_at, str) or not started_at:
        return None
    if state.kind is BattleKind.TRAINER:
        return _Stored(state, None, None, started_at)
    if state.kind is not BattleKind.WILD:
        return None
    context = _context_from_dict(envelope.get("context"))
    seen_at = row.get("encounter_seen_at")
    if context is None or not isinstance(seen_at, str) or not seen_at:
        return None
    return _Stored(state, context, seen_at, started_at)


def _context_to_dict(context: CaptureContext) -> dict[str, Any]:
    raw = asdict(context)
    raw["abilities"] = list(context.abilities)
    return raw


def _context_from_dict(raw: object) -> CaptureContext | None:
    if not isinstance(raw, dict):
        return None
    abilities = raw.get("abilities")
    growth_rate = raw.get("growth_rate")
    flags = (raw.get("is_legendary"), raw.get("is_mythical"))
    if (
        not isinstance(abilities, list)
        or not all(isinstance(item, str) for item in abilities)
        or not (growth_rate is None or isinstance(growth_rate, str))
        or not all(isinstance(flag, bool) for flag in flags)
        or not all(
            _is_nullable_int(raw.get(key)) and key in raw
            for key in ("capture_rate", "speed", "gender_rate")
        )
        or not _is_int(raw.get("encounter_level"))
    ):
        return None
    return CaptureContext(
        capture_rate=raw["capture_rate"],
        speed=raw["speed"],
        is_legendary=bool(raw["is_legendary"]),
        is_mythical=bool(raw["is_mythical"]),
        growth_rate=growth_rate,
        gender_rate=raw["gender_rate"],
        abilities=tuple(abilities),
        encounter_level=int(raw["encounter_level"]),
    )


class StartBattle:
    """Abre y guarda un combate preparado, en una transacción."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        battles: BattleRepository,
        encounters: EncounterRepository,
    ) -> None:
        self._connection_factory = connection_factory
        self._battles = battles
        self._encounters = encounters

    def execute(self, setup: BattleSetup, *, now: datetime | str) -> TurnOutcome:
        """``STARTED`` con los eventos de apertura, o por qué no se puede.

        Si ya hay un combate vigente, ``BATTLE_IN_PROGRESS`` (con su estado);
        uno ilegible u obsoleto se descarta. Un salvaje se revalida aquí
        dentro: su encuentro debe seguir siendo el mismo y sin capturar.
        """
        if setup.kind is BattleKind.EXHIBITION:
            raise ValueError("Las exhibiciones no se guardan: usa ExhibitionSession.")
        if setup.kind is BattleKind.WILD and (setup.capture is None or not setup.encounter_seen_at):
            raise ValueError("Un combate salvaje necesita su encuentro y su contexto de captura.")
        timestamp = _timestamp(now)
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            outcome = self._start(connection, setup, timestamp)
            connection.commit()
            return outcome
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _start(
        self, connection: sqlite3.Connection, setup: BattleSetup, timestamp: str
    ) -> TurnOutcome:
        current = _load(connection, self._battles, self._encounters)
        if isinstance(current, _Stored):
            return TurnOutcome(BattleStatus.BATTLE_IN_PROGRESS, (), current.state)
        if setup.kind is BattleKind.WILD:
            encounter = self._encounters.load_in_transaction(connection)
            if encounter is None:
                return TurnOutcome(BattleStatus.NO_ENCOUNTER, (), None)
            if encounter.get("seen_at") != setup.encounter_seen_at:
                return TurnOutcome(BattleStatus.STALE, (), None, reason=STALE_REASON)
            if bool(encounter.get("captured")):
                return TurnOutcome(BattleStatus.ALREADY_CAPTURED, (), None)
        opening = engine.begin(setup.state)
        self._battles.save_in_transaction(
            connection,
            _battle_row(
                opening.state, setup.capture, setup.encounter_seen_at, timestamp, timestamp
            ),
        )
        return TurnOutcome(BattleStatus.STARTED, opening.events, opening.state)


class DescribeBattle:
    """Lee el combate guardado para reanudarlo."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        battles: BattleRepository,
        encounters: EncounterRepository,
    ) -> None:
        self._connection_factory = connection_factory
        self._battles = battles
        self._encounters = encounters

    def execute(self) -> BattleView | None:
        """El combate vigente; si está corrupto o su encuentro ya no está
        (cambió, desapareció o se capturó), lo borra y devuelve ``None``."""
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = _load(connection, self._battles, self._encounters)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        if not isinstance(current, _Stored):
            return None
        return BattleView(
            current.state.kind,
            current.state,
            current.state.opponent.trainer,
            started_at=current.started_at,
        )

    def active_wild_name(self) -> str | None:
        """Nombre del salvaje de un combate vigente, para el hook y ``ver``.

        Consulta ligera que no escribe: solo hay nombre si el combate es
        salvaje, se puede leer y su encuentro sigue siendo el actual sin
        capturar. Así un combate obsoleto nunca bloquea los encuentros nuevos.
        Cualquier error cuenta como «no hay combate».
        """
        try:
            return self._active_wild_name()
        except Exception:
            return None

    def _active_wild_name(self) -> str | None:
        connection = self._connection_factory()
        try:
            row = self._battles.load_in_transaction(connection)
            if row is None or row.get("kind") != BattleKind.WILD.value:
                return None
            stored = _decode_row(row)
            if stored is None:
                return None
            encounter = self._encounters.load_in_transaction(connection)
            if not _is_current(encounter, stored.encounter_seen_at):
                return None
            return stored.state.opponent.current.name
        finally:
            # Sin commit: si leer el encuentro marcó la importación heredada,
            # se descarta; esto solo consulta.
            connection.close()


class TakeBattleTurn:
    """Un turno completo en UNA transacción: valida, juega y guarda."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        battles: BattleRepository,
        encounters: EncounterRepository,
        capture: CaptureAttempt,
        progress: CaptureProgress,
        records: BattleRecords,
        rng: RandomSource,
    ) -> None:
        self._connection_factory = connection_factory
        self._battles = battles
        self._encounters = encounters
        self._capture = capture
        self._progress = progress
        self._records = records
        self._rng = rng

    def execute(
        self, command: BattleCommand, *, now: datetime | str, battle_id: str | None = None
    ) -> TurnOutcome:
        """Resuelve ``command`` contra el combate guardado.

        - ``battle_id`` (el ``started_at`` de la sesión) que no coincide con el
          combate guardado: ``NO_BATTLE`` sin tocar nada; ese combate ya acabó.

        - Sin combate (o ilegible, que se borra): ``NO_BATTLE``. Salvaje cuyo
          encuentro cambió: se borra y ``STALE``.
        - Orden inválida: ``INVALID_ACTION`` con el motivo, sin gastar turno ni
          escribir. Sin existencias de esa bola: ``NO_STOCK``, igual.
        - Si no, ``APPLIED`` o ``FINISHED`` con los eventos del turno. La EXP
          ganada se suma a la que la captura tenga ahora en la BD, y al acabar
          se borra el combate, se anota el récord y (salvaje no capturado) se
          borra el encuentro.
        """
        timestamp = _timestamp(now)
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            outcome = self._take(connection, command, timestamp, battle_id)
            connection.commit()
            return outcome
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _take(
        self,
        connection: sqlite3.Connection,
        command: BattleCommand,
        timestamp: str,
        battle_id: str | None,
    ) -> TurnOutcome:
        current = _load(connection, self._battles, self._encounters)
        if current is _Missing.STALE:
            return TurnOutcome(BattleStatus.STALE, (), None, reason=STALE_REASON)
        if not isinstance(current, _Stored):
            return TurnOutcome(BattleStatus.NO_BATTLE, (), None)
        if battle_id is not None and current.started_at != battle_id:
            return TurnOutcome(BattleStatus.NO_BATTLE, (), None, reason=REPLACED_REASON)
        state = current.state
        capture_id: int | None = None
        if isinstance(command, ThrowBall):
            thrown = self._throw(connection, current, command, timestamp)
            if isinstance(thrown, TurnOutcome):
                return thrown
            result, capture_id = thrown
        else:
            try:
                result = _resolve(state, command, self._rng)
            except _Rejected as rejected:
                return TurnOutcome(BattleStatus.INVALID_ACTION, (), state, reason=rejected.reason)
        final = result.state
        leveled = self._save_progress(connection, state, final)
        if final.finished:
            self._close(connection, final, timestamp)
            status = BattleStatus.FINISHED
        else:
            self._battles.save_in_transaction(
                connection,
                _battle_row(
                    final, current.context, current.encounter_seen_at, current.started_at, timestamp
                ),
            )
            status = BattleStatus.APPLIED
        return TurnOutcome(status, result.events, final, capture_id=capture_id, leveled=leveled)

    def _throw(
        self,
        connection: sqlite3.Connection,
        current: _Stored,
        command: ThrowBall,
        timestamp: str,
    ) -> TurnOutcome | tuple[TurnResult, int | None]:
        state, context = current.state, current.context
        error = _ball_error(state)
        if error is not None or context is None:
            # Un salvaje guardado siempre lleva su contexto (se valida al leerlo).
            reason = error or "Este combate no permite capturar."
            return TurnOutcome(BattleStatus.INVALID_ACTION, (), state, reason=reason)
        wild = state.opponent.current
        result = self._capture.attempt_in_transaction(
            connection, _capture_command(command, context, wild, timestamp)
        )
        if result.status is CaptureStatus.NO_STOCK:
            reason = f"No te queda ninguna {command.ball_name}."
            return TurnOutcome(BattleStatus.NO_STOCK, (), state, reason=reason)
        if result.status in (CaptureStatus.NO_ENCOUNTER, CaptureStatus.ALREADY_CAPTURED):
            # El encuentro se validó al cargar; si aun así no está, el combate sobra.
            self._battles.clear_in_transaction(connection)
            return TurnOutcome(BattleStatus.STALE, (), None, reason=STALE_REASON)
        if result.status is CaptureStatus.CAUGHT:
            caught = BallThrown(command.ball_slug, command.ball_name, MAX_SHAKES, caught=True)
            return engine.finish(state, Outcome.CAPTURED, (caught,)), result.capture_id
        thrown = BallThrown(
            command.ball_slug, command.ball_name, _shakes(result.chance, self._rng), caught=False
        )
        if result.status is CaptureStatus.FLED:
            # Huida por paciencia: la captura ya borró el encuentro.
            fled = (thrown, OpponentFled(name=wild.name))
            return engine.finish(state, Outcome.OPPONENT_FLED, fled), None
        turn = engine.resolve_turn(state, SpendTurn(), self._rng)
        return TurnResult(turn.state, _after_turn_start(turn.events, thrown)), None

    def _save_progress(
        self, connection: sqlite3.Connection, before: BattleState, after: BattleState
    ) -> tuple[int, ...]:
        """Suma a la BD solo la EXP ganada en este turno.

        Se relee la captura dentro de la transacción: si los commits la
        entrenaron con el combate abierto, esa EXP se conserva. El total se
        acota al máximo de su curva y el nivel sale de la EXP (nunca baja).
        """
        if not after.award_experience:
            return ()
        previous = {member.key: member for member in before.player.team}
        leveled: list[int] = []
        for member in after.player.team:
            old = previous.get(member.key)
            if member.capture_id is None or old is None:
                continue
            gained = member.experience - old.experience
            if gained <= 0:
                continue
            stored = self._progress.progress_in_transaction(connection, member.capture_id)
            if stored is None:
                continue
            growth = member.growth_rate
            stored_experience, stored_level = stored
            level = _clamp_level(stored_level)
            base = max(stored_experience, experience_for_level(growth, level))
            total = min(experience_for_level(growth, MAX_LEVEL), base + gained)
            new_level = max(level, min(MAX_LEVEL, level_for_experience(growth, total)))
            if total <= stored_experience and new_level <= level:
                continue
            self._progress.apply_experience_in_transaction(
                connection, member.capture_id, total, new_level
            )
            if new_level > level:
                leveled.append(member.capture_id)
        return tuple(leveled)

    def _close(self, connection: sqlite3.Connection, final: BattleState, timestamp: str) -> None:
        outcome = final.outcome
        if outcome is None:
            raise ValueError("Un combate terminado debe tener resultado.")
        self._battles.clear_in_transaction(connection)
        self._records.record_in_transaction(
            connection, kind=final.kind.value, outcome=outcome.value, at=timestamp
        )
        if final.kind is BattleKind.WILD and outcome is not Outcome.CAPTURED:
            # Debilitado, huido o escapaste: ese Pokémon ya no está.
            self._encounters.clear_in_transaction(connection)


# --- Sesiones -----------------------------------------------------------------


class ExhibitionSession:
    """Exhibición en memoria: nada toca la BD. Misma interfaz que
    ``PersistedSession``; empieza ya abierta (``opening``)."""

    def __init__(self, state: BattleState, *, rng: RandomSource) -> None:
        opening = engine.begin(state)
        self._state = opening.state
        self._opening = opening.events
        self._rng = rng

    @property
    def state(self) -> BattleState:
        return self._state

    @property
    def kind(self) -> BattleKind:
        return self._state.kind

    @property
    def trainer(self) -> str | None:
        return self._state.opponent.trainer

    @property
    def opening(self) -> tuple[BattleEvent, ...]:
        return self._opening

    def take(self, command: BattleCommand) -> TurnOutcome:
        state = self._state
        if state.finished:
            return TurnOutcome(BattleStatus.NO_BATTLE, (), state)
        if isinstance(command, ThrowBall):
            reason = _ball_error(state) or "Aquí no se puede capturar."
            return TurnOutcome(BattleStatus.INVALID_ACTION, (), state, reason=reason)
        try:
            result = _resolve(state, command, self._rng)
        except _Rejected as rejected:
            return TurnOutcome(BattleStatus.INVALID_ACTION, (), state, reason=rejected.reason)
        self._state = result.state
        status = BattleStatus.FINISHED if result.state.finished else BattleStatus.APPLIED
        return TurnOutcome(status, result.events, result.state)


class PersistedSession:
    """Combate guardado: cada orden es un ``TakeBattleTurn`` con la hora del reloj."""

    def __init__(
        self,
        *,
        turns: TurnTaker,
        view: BattleView,
        clock: Callable[[], datetime],
        opening: Sequence[BattleEvent] = (),
    ) -> None:
        self._turns = turns
        self._state = view.state
        self._kind = view.kind
        self._trainer = view.trainer
        self._clock = clock
        self._opening = tuple(opening)
        self._battle_id = view.started_at

    @property
    def state(self) -> BattleState:
        return self._state

    @property
    def kind(self) -> BattleKind:
        return self._kind

    @property
    def trainer(self) -> str | None:
        return self._trainer

    @property
    def opening(self) -> tuple[BattleEvent, ...]:
        return self._opening

    def take(self, command: BattleCommand) -> TurnOutcome:
        if self._battle_id is None:
            outcome = self._turns.execute(command, now=self._clock())
        else:
            outcome = self._turns.execute(command, now=self._clock(), battle_id=self._battle_id)
        if outcome.state is not None:
            self._state = outcome.state
        return outcome


# --- Reglas de las órdenes ----------------------------------------------------


class _Rejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _resolve(state: BattleState, command: BattleCommand, rng: RandomSource) -> TurnResult:
    """Aplica una orden que no es una Pokéball; ``_Rejected`` si no vale ahora."""
    if state.finished:
        raise _Rejected("El combate ya ha terminado.")
    replacing = state.phase is Phase.CHOOSE_REPLACEMENT
    if isinstance(command, Replace) or (replacing and isinstance(command, Auto)):
        if not replacing:
            raise _Rejected("Ahora no hace falta sacar a otro Pokémon.")
        if isinstance(command, Replace):
            index = command.index
        else:
            index = ai.choose_replacement_index(state, "player", rng)
        try:
            return engine.choose_replacement(state, index, rng)
        except ValueError as invalid:
            raise _Rejected(str(invalid)) from invalid
    action = _engine_action(state, command, rng)
    reason = engine.action_error(state, action)
    if reason is not None:
        raise _Rejected(reason)
    return engine.resolve_turn(state, action, rng)


def _engine_action(state: BattleState, command: BattleCommand, rng: RandomSource) -> Action:
    if isinstance(command, Fight):
        return UseMove(command.slot)
    if isinstance(command, Switch):
        return SwitchTo(command.index)
    if isinstance(command, Flee):
        return Run()
    if isinstance(command, Auto):
        return ai.choose_action(state, "player", rng)
    raise _Rejected("Esa orden no vale aquí.")


def _ball_error(state: BattleState) -> str | None:
    if state.kind is BattleKind.TRAINER:
        return "¡No puedes capturar el Pokémon de otro entrenador!"
    if state.kind is not BattleKind.WILD:
        return "En una exhibición no se puede capturar."
    # Fase de relevo, turno comprometido (carga o recarga) o combate acabado.
    return engine.action_error(state, SpendTurn())


def _capture_command(
    command: ThrowBall, context: CaptureContext, wild: Combatant, timestamp: str
) -> CaptureCommand:
    return CaptureCommand(
        ball_slug=command.ball_slug,
        ball_multiplier=command.multiplier,
        caught_at=timestamp,
        capture_rate=context.capture_rate,
        speed=context.speed,
        is_legendary=context.is_legendary,
        is_mythical=context.is_mythical,
        growth_rate=context.growth_rate,
        encounter_level=context.encounter_level,
        gender_rate=context.gender_rate,
        abilities=context.abilities,
        battle_multiplier=battle_catch_multiplier(wild),
        # Se captura el individuo combatido, no uno recién tirado.
        individual=CapturedIndividual(
            level=wild.level,
            ivs=wild.ivs.as_mapping(),
            nature=wild.nature,
            gender=wild.gender,
            ability=wild.ability,
            experience=experience_for_level(wild.growth_rate, wild.level),
        ),
    )


def _shakes(chance: float, rng: RandomSource) -> int:
    """Sacudidas de una bola fallida: cada una sale con probabilidad ``chance ** 0,25``."""
    threshold = max(0.0, chance) ** 0.25
    shakes = 0
    while shakes < MAX_SHAKES and rng.random() < threshold:
        shakes += 1
    return shakes


def _after_turn_start(events: Sequence[BattleEvent], event: BattleEvent) -> tuple[BattleEvent, ...]:
    """Coloca ``event`` justo tras el «Turno N» con el que empieza el turno."""
    if events and isinstance(events[0], TurnStarted):
        return (events[0], event, *events[1:])
    return (event, *events)


# --- Lectura de datos de especie y capturas -----------------------------------


def _timestamp(now: datetime | str) -> str:
    if isinstance(now, str):
        return now
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).isoformat()


def _clamp_level(level: int) -> int:
    return max(1, min(MAX_LEVEL, int(level)))


def _is_int(value: object, expected: int | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return expected is None or value == expected


def _is_nullable_int(value: object) -> bool:
    return value is None or _is_int(value)


def _field(data: Mapping[str, Any] | None, key: str) -> Any:
    return None if data is None else data.get(key)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _base_stats(data: Mapping[str, Any] | None) -> Stats | None:
    """Stats base si están las seis; ``None`` si falta alguna."""
    if data is None:
        return None
    values: dict[str, int] = {}
    for key in STAT_KEYS:
        value = _optional_int(data.get(key))
        if value is None or value < 1:
            return None
        values[key] = value
    return Stats.from_mapping(values)


def _default_base_stats() -> Stats:
    return Stats.from_mapping({key: DEFAULT_BASE_STAT for key in STAT_KEYS})


def _base_experience(data: Mapping[str, Any] | None, row: Mapping[str, Any]) -> int:
    value = _optional_int(_field(data, "base_experience")) or _optional_int(
        row.get("base_experience")
    )
    return value if value is not None and value > 0 else DEFAULT_BASE_EXPERIENCE


def _row_types(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(item) for item in decoded_list(row.get("types")) if item)


def _nature_name(value: object) -> str:
    if isinstance(value, Nature):
        return value.name
    return value if isinstance(value, str) and value else "hardy"


def _gender_rate(data: Mapping[str, Any]) -> int | None:
    # 0 es válido (siempre macho): no vale «or None».
    return _optional_int(data.get("gender_rate"))


def _abilities(data: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(item for item in decoded_list(data.get("abilities")) if isinstance(item, str))


def _wild_level(data: Mapping[str, Any]) -> int:
    """El mismo nivel que usa la captura: ``max(5, encuentro, 50 si legendario)``."""
    encounter_level = _optional_int(data.get("encounter_level")) or STARTING_LEVEL
    legendary = LEGENDARY_LEVEL if bool(data.get("is_legendary")) else STARTING_LEVEL
    return _clamp_level(max(STARTING_LEVEL, encounter_level, legendary))


def _capture_context(data: Mapping[str, Any]) -> CaptureContext:
    capture_rate = _optional_int(data.get("capture_rate"))
    if capture_rate is not None and not 0 <= capture_rate <= 255:
        capture_rate = None  # ``catch_chance`` lo rechazaría a mitad de turno
    growth_rate = data.get("growth_rate")
    return CaptureContext(
        capture_rate=capture_rate,
        speed=_optional_int(data.get("spe")),
        is_legendary=bool(data.get("is_legendary")),
        is_mythical=bool(data.get("is_mythical")),
        growth_rate=growth_rate if isinstance(growth_rate, str) and growth_rate else None,
        gender_rate=_gender_rate(data),
        abilities=_abilities(data),
        encounter_level=_optional_int(data.get("encounter_level")) or STARTING_LEVEL,
    )


def _trainer_candidates(entries: Iterable[Mapping[str, Any]]) -> list[SpeciesCandidate]:
    """Especies del pool aptas para un entrenador cualquiera."""
    candidates: list[SpeciesCandidate] = []
    for entry in entries:
        species = entry.get("species")
        form = str(entry.get("form") or "regular")
        if not isinstance(species, str) or not species:
            continue
        if bool(entry.get("is_legendary")) or bool(entry.get("is_mythical")):
            continue
        if form.startswith(BATTLE_ONLY_FORMS):
            continue
        types = tuple(str(item) for item in decoded_list(entry.get("types")) if item)
        candidates.append(SpeciesCandidate(species=species, form=form, types=types))
    return candidates
