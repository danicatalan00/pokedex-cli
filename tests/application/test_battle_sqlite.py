"""Combate de punta a punta con SQLite temporal, repositorios reales y API falsa.

Los números se eligen para que el resultado no dependa del azar: Shedinja
tiene siempre 1 PS, Magikarp apenas hace daño y quien debe actuar primero es
mucho más rápido. La caché de especies se siembra y PokeAPI está sin conexión;
los movimientos salen de una API falsa que cuenta cada descarga.
"""

import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pokedex_cli import composition
from pokedex_cli.application.battle import (
    BattleSetup,
    BattleStatus,
    DescribeBattle,
    ExhibitionSession,
    Fight,
    Flee,
    PersistedSession,
    PrepareBattle,
    Replace,
    StartBattle,
    TakeBattleTurn,
    ThrowBall,
)
from pokedex_cli.application.capture import CaptureEncounter
from pokedex_cli.application.collection import CollectionQueries
from pokedex_cli.application.moves import GetMoveData
from pokedex_cli.application.movesets import MovesetStatus
from pokedex_cli.application.species import GetSpeciesData
from pokedex_cli.domain.battle.model import (
    BallThrown,
    DamageClass,
    EscapeAttempted,
    ExperienceGained,
    Move,
    MoveUsed,
    OpponentFled,
    Outcome,
    Phase,
    ReplacementNeeded,
    SentOut,
    StatChange,
    TurnStarted,
)
from pokedex_cli.domain.battle.moves import FALLBACK_BY_TYPE, TACKLE, move_to_record
from pokedex_cli.domain.individuality import derive_ability, derive_gender, derive_ivs_nature
from pokedex_cli.domain.progression import experience_for_level
from pokedex_cli.infrastructure import database
from pokedex_cli.infrastructure.repositories import (
    SQLiteBattleRecordRepository,
    SQLiteBattleRepository,
    SQLiteCaptureMovesRepository,
    SQLiteCaptureRepository,
    SQLiteCollectionRepository,
    SQLiteEncounterRepository,
    SQLiteInventoryRepository,
    SQLiteMoveCacheRepository,
    SQLiteSpeciesCacheRepository,
)

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
SEEN_AT = "2026-11-11T08:00:00+00:00"

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
SPLASH = Move(
    "splash", "Salpicadura", "normal", DamageClass.STATUS, None, None, 40, category="unique"
)
EMBER = FALLBACK_BY_TYPE["fire"]
MOVES = {
    move.slug: move
    for move in (
        SCRATCH,
        GROWL,
        SPLASH,
        TACKLE,
        EMBER,
        FALLBACK_BY_TYPE["psychic"],
        FALLBACK_BY_TYPE["electric"],
    )
}


def profile(types, stats, learnset, **extra) -> dict:
    data = {
        "pokedex_id": 1,
        "capture_rate": 45,
        "types": list(types),
        **dict(zip(("hp", "atk", "def", "spa", "spd", "spe"), stats)),
        "is_legendary": False,
        "is_mythical": False,
        "growth_rate": "medium",
        "base_experience": 64,
        "encounter_level": 5,
        "level_evolutions": [],
        "gender_rate": 4,
        "abilities": ["run-away"],
        "level_moves": learnset,
        "moves_version_group": "emerald",
    }
    data.update(extra)
    return data


CHARMANDER = profile(
    ["fire"],
    (39, 52, 43, 60, 50, 65),
    [["scratch", 1], ["growl", 1], ["ember", 7]],
    base_experience=62,
    level_evolutions=[{"species": "charmeleon", "form": "regular", "min_level": 16}],
    abilities=["blaze"],
    gender_rate=1,
)
# Base de PS 1: siempre 1 PS, cualquier golpe que le afecte lo debilita.
SHEDINJA = profile(
    ["bug", "ghost"],
    (1, 90, 45, 30, 30, 1),
    [["scratch", 1]],
    base_experience=236,
    encounter_level=30,
    gender_rate=-1,
    abilities=["wonder-guard"],
)
MAGIKARP = profile(
    ["water"], (20, 10, 55, 15, 20, 80), [["splash", 1], ["tackle", 15]], abilities=["swift-swim"]
)
RATTATA = profile(["normal"], (30, 56, 35, 25, 35, 72), [["tackle", 1]], encounter_level=30)
PICHU = profile(["electric"], (20, 40, 15, 35, 35, 60), [["thunder-shock", 1]])
MEWTWO = profile(
    ["psychic"],
    (106, 110, 90, 154, 90, 130),
    [["confusion", 1]],
    is_legendary=True,
    growth_rate="slow",
)
HOUNDOUR = profile(["dark", "fire"], (45, 60, 30, 80, 50, 65), [["ember", 1]])


class OfflineSpecies:
    def fetch_species_data(self, species, form):
        return None


class MoveApi:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch_move_data(self, slug):
        self.calls.append(slug)
        move = MOVES.get(slug)
        return None if move is None else move_to_record(move)


class CaptureLuck:
    """Azar de la captura: ``random()`` fijo; la paciencia sale de ``randint``."""

    def __init__(self, roll: float) -> None:
        self.roll = roll

    def random(self) -> float:
        return self.roll

    def randint(self, low: int, high: int) -> int:
        return min(high, max(low, 3))


def initial_inventory(raw: object | None) -> dict:
    if isinstance(raw, dict):
        return raw
    stamp = NOW.isoformat()
    return {
        "version": 1,
        "balls": {"superball": 3, "ultraball": 1, "masterball": 0},
        "activity": {
            "started_at": stamp,
            "last_passive_at": stamp,
            "last_synced_at": stamp,
            "last_repo_scan_at": None,
            "repositories": [],
            "processed_commits": [],
            "work_commits": 0,
        },
    }


class Field:
    """Casos de uso del combate cableados con SQLite real en ``tmp_path``."""

    def __init__(self, tmp_path: Path, *, capture_roll: float = 0.0, seed: int = 7) -> None:
        self.db = tmp_path / "pokedex.db"
        self.cache = SQLiteSpeciesCacheRepository(self.db)
        self.inventory = SQLiteInventoryRepository(self.db, tmp_path / "inventory.json")
        self.encounters = SQLiteEncounterRepository(self.db, tmp_path / "last_seen.json")
        self.battles = SQLiteBattleRepository(self.db)
        self.records = SQLiteBattleRecordRepository(self.db)
        self.move_api = MoveApi()
        self.rng = random.Random(seed)

        def connect():
            return database.connect(self.db)

        self.prepare = PrepareBattle(
            collection=CollectionQueries(SQLiteCollectionRepository(self.db)),
            species=GetSpeciesData(cache=self.cache, api=OfflineSpecies(), clock=lambda: NOW),
            moves=GetMoveData(
                cache=SQLiteMoveCacheRepository(self.db), api=self.move_api, clock=lambda: NOW
            ),
            movesets=SQLiteCaptureMovesRepository(self.db),
            pool=self.cache,
            rng=self.rng,
        )
        self.start = StartBattle(
            connection_factory=connect, battles=self.battles, encounters=self.encounters
        )
        self.turn = TakeBattleTurn(
            connection_factory=connect,
            battles=self.battles,
            encounters=self.encounters,
            capture=CaptureEncounter(
                connection_factory=connect,
                inventory_repository=self.inventory,
                encounter_repository=self.encounters,
                capture_repository=SQLiteCaptureRepository(),
                inventory_normaliser=initial_inventory,
                random_source=CaptureLuck(capture_roll),
            ),
            # El adaptador real de la composición: lee y escribe en la transacción.
            progress=composition._BattleProgressRepository(random.Random(0)),
            records=self.records,
            rng=self.rng,
        )
        self.describe = DescribeBattle(
            connection_factory=connect, battles=self.battles, encounters=self.encounters
        )

    # --- Siembra ----------------------------------------------------------------

    def species(self, **profiles: dict) -> None:
        for name, data in profiles.items():
            self.cache.put(name, "regular", data, NOW.isoformat())

    def capture(self, species: str, *, level: int, experience: int | None = None) -> int:
        growth = "slow" if species == "mewtwo" else "medium"
        values = {
            "species": species,
            "form": "regular",
            "shiny": 0,
            "caught_at": "2026-11-01T10:00:00+00:00",
            "level": level,
            "experience": experience_for_level(growth, level) if experience is None else experience,
            "in_team": 1,
            **{f"iv_{key}": 0 for key in ("hp", "atk", "def", "spa", "spd", "spe")},
            "nature": "hardy",
        }
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        return self.execute(f"INSERT INTO captures ({columns}) VALUES ({marks})", *values.values())

    def appear(self, species: str, **overrides: object) -> None:
        encounter = {
            "species": species,
            "form": "regular",
            "shiny": False,
            "seen_at": SEEN_AT,
            "captured": False,
            "failed_capture_attempts": 0,
            "escape_after_attempts": None,
            "catch_stage": 0,
            "item_turns": 0,
        }
        encounter.update(overrides)
        self.encounters.write(encounter)

    def balls(self, **stock: int) -> None:
        self.inventory.update(initial_inventory, lambda state: state["balls"].update(stock))

    def stock(self, slug: str) -> int:
        return int(self.inventory.update(initial_inventory, lambda state: None)["balls"][slug])

    # --- Juego -----------------------------------------------------------------

    def wild_battle(self):
        setup = self.prepare.wild(self.encounters.read())
        assert isinstance(setup, BattleSetup)
        return self.begin(setup)

    def trainer_battle(self):
        setup = self.prepare.trainer()
        assert isinstance(setup, BattleSetup)
        return self.begin(setup)

    def begin(self, setup: BattleSetup):
        outcome = self.start.execute(setup, now=NOW)
        assert outcome.status is BattleStatus.STARTED
        return outcome

    def play(self, command):
        return self.turn.execute(command, now=NOW)

    # --- SQL directo para preparar y comprobar -----------------------------------

    def execute(self, sql: str, *params: object) -> int:
        connection = database.connect(self.db)
        try:
            cursor = connection.execute(sql, params)
            connection.commit()
            return int(cursor.lastrowid or 0)
        finally:
            connection.close()

    def query(self, sql: str, *params: object) -> tuple:
        connection = database.connect(self.db)
        try:
            row = connection.execute(sql, params).fetchone()
            return tuple(row) if row is not None else ()
        finally:
            connection.close()

    def snapshot(self) -> list:
        connection = database.connect(self.db)
        try:
            return [
                connection.execute(f"SELECT * FROM {table}").fetchall()
                for table in (
                    "captures",
                    "battle_state",
                    "battle_record",
                    "encounter_state",
                    "inventory_balls",
                )
            ]
        finally:
            connection.close()


def slot_of(state, slug: str) -> int:
    return next(i for i, slot in enumerate(state.player.current.moves) if slot.move.slug == slug)


def record(field: Field, *keys: str) -> tuple:
    current = field.records.read()
    return tuple(current[key] for key in keys)


# --- Salvaje ------------------------------------------------------------------------


def test_a_wild_battle_until_ko_adds_experience_queues_evolution_and_records_it(
    tmp_path: Path,
) -> None:
    field = Field(tmp_path)
    field.species(charmander=CHARMANDER, shedinja=SHEDINJA)
    chispa = field.capture("charmander", level=15)
    field.appear("shedinja")
    started = field.wild_battle()
    assert field.describe.active_wild_name() == "Shedinja"
    # Con el combate abierto, los commits entrenan a la captura.
    field.execute("UPDATE captures SET experience = experience + 100 WHERE id = ?", chispa)

    outcome = field.play(Fight(slot_of(started.state, "ember")))

    assert outcome.status is BattleStatus.FINISHED
    assert outcome.state.outcome is Outcome.WON
    gained = 236 * 30 // 7
    assert ExperienceGained("p0", "Charmander", gained) in outcome.events
    # Solo se suma lo ganado en el turno: la EXP de los commits no se pierde.
    trained = experience_for_level("medium", 15) + 100
    assert field.query(
        "SELECT experience, level, pending_evolution_species FROM captures WHERE id = ?", chispa
    ) == (trained + gained, 16, "charmeleon")
    assert outcome.leveled == (chispa,)
    assert field.battles.read() is None
    assert field.encounters.read() is None
    assert record(field, "wild_battles", "wild_wins", "wild_captures") == (1, 1, 0)
    assert field.describe.active_wild_name() is None


def test_catching_in_battle_keeps_the_fought_individual(tmp_path: Path) -> None:
    field = Field(tmp_path, capture_roll=0.5)
    field.species(charmander=CHARMANDER, magikarp=MAGIKARP)
    field.capture("charmander", level=15)
    field.appear("magikarp")
    field.balls(masterball=1)
    field.wild_battle()

    outcome = field.play(ThrowBall("masterball", "Masterball", 255.0))

    assert outcome.status is BattleStatus.FINISHED
    assert outcome.state.outcome is Outcome.CAPTURED
    assert outcome.events[0] == BallThrown("masterball", "Masterball", 3, caught=True)
    seed = f"wild:magikarp:regular:{SEEN_AT}"
    ivs, nature = derive_ivs_nature(seed)
    assert field.query(
        "SELECT species, level, experience, iv_hp, iv_atk, iv_def, iv_spa, iv_spd, iv_spe, "
        "nature, gender, ability, ball_slug FROM captures WHERE id = ?",
        outcome.capture_id,
    ) == (
        "magikarp",
        5,
        experience_for_level("medium", 5),
        *ivs.values(),
        nature.name,
        derive_gender(seed, 4),
        derive_ability(seed, ["swift-swim"]),
        "masterball",
    )
    assert field.encounters.read()["captured"] is True
    assert field.battles.read() is None
    assert field.stock("masterball") == 0
    assert record(field, "wild_battles", "wild_wins", "wild_captures") == (1, 0, 1)


def test_a_failed_ball_spends_the_turn_and_the_wild_moves(tmp_path: Path) -> None:
    field = Field(tmp_path, capture_roll=0.99)
    field.species(charmander=CHARMANDER, magikarp=MAGIKARP)
    field.capture("charmander", level=15)
    field.appear("magikarp")
    field.wild_battle()

    outcome = field.play(ThrowBall("superball", "Superball", 1.5))

    assert outcome.status is BattleStatus.APPLIED
    assert outcome.events[0] == TurnStarted(1)
    thrown = outcome.events[1]
    assert isinstance(thrown, BallThrown) and not thrown.caught and 0 <= thrown.shakes <= 3
    assert any(isinstance(e, MoveUsed) and e.side == "opponent" for e in outcome.events)
    assert field.stock("superball") == 2
    assert field.encounters.read()["failed_capture_attempts"] == 1
    view = field.describe.execute()
    assert view is not None and view.state == outcome.state and view.state.turn == 1


def test_running_out_of_patience_the_wild_flees_and_takes_the_encounter(
    tmp_path: Path,
) -> None:
    field = Field(tmp_path, capture_roll=0.99)
    field.species(charmander=CHARMANDER, magikarp=MAGIKARP)
    field.capture("charmander", level=15)
    field.appear("magikarp", escape_after_attempts=1)
    field.wild_battle()

    outcome = field.play(ThrowBall("pokeball", "Pokeball", 1.0))

    assert outcome.status is BattleStatus.FINISHED
    assert outcome.state.outcome is Outcome.OPPONENT_FLED
    assert outcome.events[-2] == OpponentFled("Magikarp")
    assert field.encounters.read() is None
    assert field.battles.read() is None
    assert record(field, "wild_battles", "wild_wins") == (1, 0)


def test_fleeing_ends_the_battle_and_the_wild_is_gone(tmp_path: Path) -> None:
    field = Field(tmp_path)
    field.species(charmander=CHARMANDER, magikarp=MAGIKARP)
    field.capture("charmander", level=15)
    field.appear("magikarp")
    field.wild_battle()

    outcome = field.play(Flee())

    assert outcome.status is BattleStatus.FINISHED
    assert EscapeAttempted(success=True) in outcome.events
    assert outcome.state.outcome is Outcome.ESCAPED
    assert field.encounters.read() is None
    assert field.battles.read() is None
    assert record(field, "wild_battles", "wild_wins") == (1, 0)


def test_a_battle_whose_encounter_changed_is_stale_and_never_blocks_the_hook(
    tmp_path: Path,
) -> None:
    field = Field(tmp_path)
    field.species(charmander=CHARMANDER, magikarp=MAGIKARP)
    field.capture("charmander", level=15)
    field.appear("magikarp")
    field.wild_battle()
    assert field.describe.active_wild_name() == "Magikarp"

    field.appear("magikarp", seen_at="2026-11-11T09:30:00+00:00")  # otro encuentro

    assert field.describe.active_wild_name() is None
    outcome = field.play(Fight(0))
    assert (outcome.status, outcome.state) == (BattleStatus.STALE, None)
    assert field.battles.read() is None
    assert field.describe.execute() is None


def test_a_battle_cannot_start_on_an_encounter_that_changed_after_preparing(
    tmp_path: Path,
) -> None:
    field = Field(tmp_path)
    field.species(charmander=CHARMANDER, magikarp=MAGIKARP)
    field.capture("charmander", level=15)
    field.appear("magikarp")
    setup = field.prepare.wild(field.encounters.read())
    assert isinstance(setup, BattleSetup)
    field.appear("magikarp", seen_at="2026-11-11T09:30:00+00:00")

    assert field.start.execute(setup, now=NOW).status is BattleStatus.STALE
    assert field.battles.read() is None


def test_only_one_battle_at_a_time(tmp_path: Path) -> None:
    field = Field(tmp_path)
    field.species(charmander=CHARMANDER, magikarp=MAGIKARP)
    field.capture("charmander", level=15)
    field.appear("magikarp")
    started = field.wild_battle()
    setup = field.prepare.trainer()
    assert isinstance(setup, BattleSetup)

    outcome = field.start.execute(setup, now=NOW)

    assert outcome.status is BattleStatus.BATTLE_IN_PROGRESS
    assert outcome.state == started.state


def test_a_session_of_a_finished_battle_never_plays_in_the_next_one(tmp_path: Path) -> None:
    """Dos ventanas: la TUI acaba un combate y empieza otro; la CLI, que seguía
    con la sesión vieja abierta, no puede jugar un turno en el combate nuevo."""
    field = Field(tmp_path)
    field.species(charmander=CHARMANDER, magikarp=MAGIKARP)
    field.capture("charmander", level=15)
    field.appear("magikarp")
    field.wild_battle()
    old_view = field.describe.execute()
    assert old_view is not None and old_view.started_at
    old_session = PersistedSession(turns=field.turn, view=old_view, clock=lambda: NOW)

    assert field.play(Flee()).status is BattleStatus.FINISHED
    field.execute("DELETE FROM encounter_state")
    setup = field.prepare.trainer()
    assert isinstance(setup, BattleSetup)
    later = datetime(2026, 11, 11, 10, tzinfo=timezone.utc)
    assert field.start.execute(setup, now=later).status is BattleStatus.STARTED
    before = field.battles.read()

    outcome = old_session.take(Fight(0))

    assert outcome.status is BattleStatus.NO_BATTLE
    assert outcome.reason == "Ese combate ya terminó en otra ventana."
    assert field.battles.read() == before
    current = field.describe.execute()
    assert current is not None and current.started_at == later.isoformat()
    fresh = PersistedSession(turns=field.turn, view=current, clock=lambda: NOW)
    assert fresh.take(Fight(0)).status in (BattleStatus.APPLIED, BattleStatus.FINISHED)


def test_unreadable_saved_state_is_dropped(tmp_path: Path) -> None:
    field = Field(tmp_path)
    field.species(charmander=CHARMANDER, magikarp=MAGIKARP)
    field.capture("charmander", level=15)
    field.appear("magikarp")
    field.wild_battle()
    field.execute("""UPDATE battle_state SET state_json = '{"v": 1, "state": {"v": 1}}'""")

    assert field.describe.active_wild_name() is None
    assert field.play(Fight(0)).status is BattleStatus.NO_BATTLE
    assert field.battles.read() is None


def test_without_stock_the_turn_is_not_spent(tmp_path: Path) -> None:
    field = Field(tmp_path)
    field.species(charmander=CHARMANDER, magikarp=MAGIKARP)
    field.capture("charmander", level=15)
    field.appear("magikarp")
    field.balls(ultraball=0)
    started = field.wild_battle()
    before = field.snapshot()

    outcome = field.play(ThrowBall("ultraball", "Ultraball", 2.0))

    assert (outcome.status, outcome.reason) == (
        BattleStatus.NO_STOCK,
        "No te queda ninguna Ultraball.",
    )
    assert outcome.state == started.state
    assert field.snapshot() == before


def test_switching_in_after_a_faint(tmp_path: Path) -> None:
    field = Field(tmp_path)
    field.species(charmander=CHARMANDER, pichu=PICHU, rattata=RATTATA)
    field.capture("pichu", level=10)  # lidera: más nivel
    field.capture("charmander", level=5)
    field.appear("rattata")  # nivel 30: su Placaje debilita a Pichu antes de que actúe
    started = field.wild_battle()
    assert started.state.player.current.name == "Pichu"

    fainted = field.play(Fight(0))
    blocked = field.play(Fight(0))
    sent = field.play(Replace(1))

    assert fainted.status is BattleStatus.APPLIED
    assert fainted.state.phase is Phase.CHOOSE_REPLACEMENT
    assert ReplacementNeeded("player") in fainted.events
    assert (blocked.status, blocked.reason) == (
        BattleStatus.INVALID_ACTION,
        "Antes tienes que sacar a otro Pokémon.",
    )
    assert sent.status is BattleStatus.APPLIED
    assert [(e.side, e.name) for e in sent.events if isinstance(e, SentOut)] == [
        ("player", "Charmander")
    ]
    view = field.describe.execute()
    assert view is not None and view.state.player.active == 1


# --- Entrenadores -----------------------------------------------------------------------


def test_a_ball_against_a_trainer_is_refused_without_spending_it(tmp_path: Path) -> None:
    field = Field(tmp_path)
    field.species(charmander=CHARMANDER, rattata=RATTATA)
    field.capture("charmander", level=15)
    field.trainer_battle()

    outcome = field.play(ThrowBall("ultraball", "Ultraball", 2.0))

    assert outcome.status is BattleStatus.INVALID_ACTION
    assert outcome.reason == "¡No puedes capturar el Pokémon de otro entrenador!"
    assert field.stock("ultraball") == 1


def test_beating_a_trainer_extends_the_streak_with_boosted_experience(tmp_path: Path) -> None:
    field = Field(tmp_path)
    # Mewtwo es legendario: el entrenador solo puede sacar a Shedinja.
    field.species(mewtwo=MEWTWO, shedinja=SHEDINJA)
    mewtwo = field.capture("mewtwo", level=15)
    started = field.trainer_battle()
    rival = started.state.opponent.current
    assert rival.species == "shedinja"
    assert started.state.opponent.trainer

    outcome = field.play(Fight(0))

    assert outcome.state.outcome is Outcome.WON
    boosted = 236 * rival.level // 7 * 3 // 2
    assert ExperienceGained("p0", "Mewtwo", boosted) in outcome.events
    assert field.query("SELECT experience FROM captures WHERE id = ?", mewtwo) == (
        experience_for_level("slow", 15) + boosted,
    )
    assert record(field, "trainer_wins", "streak", "best_streak") == (1, 1, 1)
    assert field.battles.read() is None


def test_losing_to_a_trainer_resets_the_streak(tmp_path: Path) -> None:
    field = Field(tmp_path)
    # Solo para que el entrenador no la use: el jugador lucha con Shedinja (1 PS).
    field.species(shedinja={**SHEDINJA, "is_legendary": True}, houndour=HOUNDOUR)
    field.capture("shedinja", level=20)
    connection = database.connect(field.db)
    try:
        for _ in range(2):
            field.records.record_in_transaction(
                connection, kind="trainer", outcome="won", at="ayer"
            )
        connection.commit()
    finally:
        connection.close()
    field.trainer_battle()

    outcome = field.play(Fight(0))

    assert outcome.status is BattleStatus.FINISHED
    assert outcome.state.outcome is Outcome.LOST
    assert record(field, "trainer_losses", "streak", "best_streak") == (1, 0, 2)


# --- Exhibición y caché -------------------------------------------------------------------


def test_an_exhibition_plays_in_memory_without_writing_anything(tmp_path: Path) -> None:
    field = Field(tmp_path)
    field.species(charmander=CHARMANDER, magikarp=MAGIKARP)
    chispa = field.capture("charmander", level=15)
    state = field.prepare.exhibition(str(chispa), "magikarp")
    assert not isinstance(state, BattleStatus)
    before = field.snapshot()

    session = ExhibitionSession(state, rng=random.Random(5))
    for _ in range(100):
        if session.state.finished:
            break
        session.take(Fight(0))

    assert session.state.finished
    assert field.snapshot() == before


def test_moves_are_downloaded_once_and_then_read_from_the_cache(tmp_path: Path) -> None:
    field = Field(tmp_path)
    field.species(charmander=CHARMANDER, magikarp=MAGIKARP)
    field.capture("charmander", level=15)
    field.appear("magikarp")

    first = field.prepare.wild(field.encounters.read())
    downloads = list(field.move_api.calls)
    second = field.prepare.wild(field.encounters.read())

    assert sorted(downloads) == ["ember", "growl", "scratch", "splash"]
    assert field.move_api.calls == downloads
    assert first == second
    assert field.query("SELECT COUNT(*) FROM move_cache") == (4,)


# --- Composición -----------------------------------------------------------------------------


@pytest.fixture
def composed(isolated_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Field:
    """Las fábricas de ``composition`` sobre una BD temporal ya sembrada (sin red)."""
    data = tmp_path / "data"
    monkeypatch.setattr(composition.paths, "DB_PATH", data / "pokedex.db")
    monkeypatch.setattr(composition.paths, "LAST_SEEN_PATH", data / "last_seen.json")
    monkeypatch.setattr(composition.paths, "INVENTORY_PATH", data / "inventory.json")
    monkeypatch.setattr(composition, "random", random.Random(3))
    field = Field(data)
    field.species(charmander=CHARMANDER, magikarp={**MAGIKARP, "capture_rate": 255})
    for move in MOVES.values():
        SQLiteMoveCacheRepository(field.db).put(move.slug, move_to_record(move), NOW.isoformat())
    field.capture("charmander", level=15)
    return field


def test_composition_plays_a_saved_wild_battle_end_to_end(composed: Field) -> None:
    composition.write_encounter("magikarp", "regular", False, SEEN_AT)
    setup = composition.prepare_battle().wild(composition.read_encounter())
    assert isinstance(setup, BattleSetup)
    started = composition.start_battle().execute(setup, now=NOW)
    assert started.status is BattleStatus.STARTED
    assert composition.active_wild_battle_name() == "Magikarp"

    session = composition.persisted_session(opening=started.events)
    assert session is not None and session.opening == started.events
    # Ratio 255 con Pokeball a PS llenos: captura segura con cualquier azar.
    outcome = session.take(ThrowBall("pokeball", "Pokeball", 1.0))

    assert outcome.status is BattleStatus.FINISHED
    assert outcome.capture_id is not None
    assert composition.battle_record()["wild_captures"] == 1
    assert composition.persisted_session() is None
    assert composition.active_wild_battle_name() is None


def test_composition_exhibition_and_custom_movesets(composed: Field) -> None:
    session = composition.exhibition_session("1", "magikarp")
    assert isinstance(session, ExhibitionSession)
    assert session.take(Fight(0)).status in (BattleStatus.APPLIED, BattleStatus.FINISHED)

    manager = composition.manage_moveset()
    saved = manager.set(1, ["Ascuas", "growl"])
    view = manager.view("1")

    assert saved.status is MovesetStatus.SAVED
    assert view is not None and view.custom and [m.slug for m in view.moves] == ["ember", "growl"]
    assert composition.exhibition_session("99", "magikarp") is BattleStatus.NO_CAPTURE


def test_the_hook_query_never_raises(
    isolated_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Una carpeta no es una base de datos: SQLite falla al abrirla.
    monkeypatch.setattr(composition.paths, "DB_PATH", tmp_path)

    assert composition.active_wild_battle_name() is None


def test_open_terminal_keeps_the_encounter_of_a_live_wild_battle(
    isolated_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(composition.paths, "DB_PATH", tmp_path / "pokedex.db")
    painted: list[str] = []

    class NoEvolutions:
        def pending(self):
            return ()

        def execute(self, capture_ids):
            return ()

    monkeypatch.setattr(composition, "sync_training", lambda: ("actividad", ()))
    monkeypatch.setattr(composition, "process_evolutions", NoEvolutions)
    monkeypatch.setattr(composition, "active_wild_battle_name", lambda: "Magikarp")
    monkeypatch.setattr(
        composition.wild_encounters,
        "run_hook",
        lambda generations, write: painted.append(generations),
    )

    result = composition.open_terminal(lambda species, form, shiny: None).execute("1-3")

    assert result.battle_reminder == "Magikarp"
    assert not result.wild_encounter_started
    assert painted == []


def test_moves_download_in_parallel_with_one_client_per_thread() -> None:
    clients: list[str] = []

    class Client:
        def __init__(self) -> None:
            clients.append(threading.current_thread().name)

        def fetch_move_data(self, slug):
            time.sleep(0.01)  # que los hilos se solapen de verdad
            return {"slug": slug}

    api = composition._PerThreadMoveApi(Client)
    slugs = [f"move-{index}" for index in range(24)]

    results = composition._parallel_map(api.fetch_move_data, slugs)

    assert [result["slug"] for result in results] == slugs
    assert 1 < len(clients) <= composition.MOVE_FETCH_WORKERS
    assert len(set(clients)) == len(clients)  # ninguna sesión compartida entre hilos
