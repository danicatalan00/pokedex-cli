"""Repositorios SQLite del modo combate: caché de movimientos, movesets
propios, combate en curso, récord y progreso de las capturas."""

import json
import sqlite3
from pathlib import Path

import pytest

from pokedex_cli.domain.progression import experience_for_level
from pokedex_cli.infrastructure import database
from pokedex_cli.infrastructure.repositories import (
    SQLiteBattleProgressRepository,
    SQLiteBattleRecordRepository,
    SQLiteBattleRepository,
    SQLiteCaptureMovesRepository,
    SQLiteMoveCacheRepository,
)

TACKLE = {
    "slug": "tackle",
    "name": "Placaje",
    "type": "normal",
    "damage_class": "physical",
    "power": 40,
    "accuracy": 100,
    "pp": 35,
    "priority": 0,
    "target": "selected-pokemon",
    "category": "damage",
    "ailment": "none",
    "ailment_chance": 0,
    "min_hits": None,
    "max_hits": None,
    "min_turns": None,
    "max_turns": None,
    "drain": 0,
    "healing": 0,
    "crit_rate": 0,
    "flinch_chance": 0,
    "stat_changes": [],
    "stat_chance": 0,
    "description": "Embiste con todo el cuerpo.",
}
GROWL = {
    **TACKLE,
    "slug": "growl",
    "name": "Gruñido",
    "damage_class": "status",
    "power": None,
    "pp": 40,
    "target": "all-opponents",
    "category": "net-good-stats",
    "stat_changes": [{"stat": "atk", "change": -1}],
    "description": "Dulce gruñido que reduce el Ataque del contrincante.",
}


def insert_capture(path: Path, species: str = "pikachu", **columns: object) -> int:
    values = {"form": "regular", "shiny": 0, "caught_at": "2026-11-01T10:00:00+00:00"}
    values.update(columns)
    names = ", ".join(["species", *values])
    marks = ", ".join("?" for _ in range(len(values) + 1))
    connection = database.connect(path)
    try:
        cursor = connection.execute(
            f"INSERT INTO captures ({names}) VALUES ({marks})", (species, *values.values())
        )
        connection.commit()
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)
    finally:
        connection.close()


def in_transaction(path: Path, operation) -> object:
    connection = database.connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        result = operation(connection)
        connection.commit()
        return result
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


# --- Caché de movimientos ----------------------------------------------------


def test_move_cache_round_trips_the_record_as_json(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    repository = SQLiteMoveCacheRepository(path)
    assert repository.get("tackle") is None

    repository.put("tackle", TACKLE, "2026-11-11T08:00:00+00:00")
    repository.put("tackle", {**TACKLE, "power": 50}, "2026-11-12T08:00:00+00:00")

    assert repository.get("tackle") == {**TACKLE, "power": 50}
    connection = database.connect(path)
    try:
        row = connection.execute("SELECT record, fetched_at FROM move_cache").fetchone()
        assert json.loads(row["record"]) == {**TACKLE, "power": 50}
        assert row["fetched_at"] == "2026-11-12T08:00:00+00:00"
    finally:
        connection.close()


def test_get_many_returns_only_cached_and_readable_records(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    repository = SQLiteMoveCacheRepository(path)
    repository.put("tackle", TACKLE, "now")
    repository.put("growl", GROWL, "now")
    connection = database.connect(path)
    try:
        connection.executemany(
            "INSERT INTO move_cache (slug, record, fetched_at) VALUES (?, ?, 'now')",
            [("corrupt", "{not json"), ("listed", "[1, 2]")],
        )
        connection.commit()
    finally:
        connection.close()

    found = repository.get_many(["growl", "surf", "corrupt", "tackle", "listed", "tackle"])

    assert found == {"growl": GROWL, "tackle": TACKLE}
    assert repository.get("corrupt") is None
    assert repository.get("listed") is None
    assert repository.get_many([]) == {}


def test_get_many_reads_a_whole_learnset_in_one_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "pokedex.db"
    repository = SQLiteMoveCacheRepository(path)
    for index in range(30):
        repository.put(f"move-{index}", {**TACKLE, "slug": f"move-{index}"}, "now")
    statements: list[str] = []
    connect = database.connect

    def tracing_connect(database_path: Path, **kwargs: int) -> sqlite3.Connection:
        connection = connect(database_path, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(database, "connect", tracing_connect)

    found = repository.get_many([f"move-{index}" for index in range(60)])

    assert len(found) == 30
    assert len([statement for statement in statements if "move_cache" in statement]) == 1


# --- Movesets propios ----------------------------------------------------------


def test_capture_moves_are_replaced_whole_and_read_in_slot_order(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    pikachu = insert_capture(path)
    eevee = insert_capture(path, "eevee")
    repository = SQLiteCaptureMovesRepository(path)
    assert repository.get(pikachu) == ()

    repository.replace(pikachu, ["thunderbolt", "quick-attack", "thunder-wave", "iron-tail"])
    repository.replace(eevee, ["tackle"])
    assert repository.get(pikachu) == ("thunderbolt", "quick-attack", "thunder-wave", "iron-tail")

    repository.replace(pikachu, ["surf", "thunderbolt"])
    assert repository.get(pikachu) == ("surf", "thunderbolt")

    # Sin movimientos vuelve al moveset automático.
    repository.replace(pikachu, [])
    assert repository.get(pikachu) == ()
    assert repository.get(eevee) == ("tackle",)


@pytest.mark.parametrize(
    "slugs",
    [
        ["tackle", "growl", "ember", "surf", "fly"],
        ["tackle", "growl", "tackle"],
        ["tackle", ""],
    ],
    ids=["five-moves", "duplicate", "empty-slug"],
)
def test_invalid_movesets_are_rejected_without_touching_the_current_one(
    tmp_path: Path, slugs: list[str]
) -> None:
    path = tmp_path / "pokedex.db"
    capture_id = insert_capture(path)
    repository = SQLiteCaptureMovesRepository(path)
    repository.replace(capture_id, ["thunderbolt"])

    with pytest.raises(ValueError):
        repository.replace(capture_id, slugs)

    assert repository.get(capture_id) == ("thunderbolt",)


def test_unknown_capture_cannot_get_a_moveset(tmp_path: Path) -> None:
    repository = SQLiteCaptureMovesRepository(tmp_path / "pokedex.db")

    with pytest.raises(sqlite3.IntegrityError):
        repository.replace(404, ["tackle"])

    assert repository.get(404) == ()


def test_releasing_a_capture_drops_its_moveset(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    capture_id = insert_capture(path)
    repository = SQLiteCaptureMovesRepository(path)
    repository.replace(capture_id, ["tackle", "growl"])

    in_transaction(path, lambda connection: connection.execute("DELETE FROM captures"))

    assert repository.get(capture_id) == ()


def test_moveset_reads_and_writes_join_the_callers_transaction(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    capture_id = insert_capture(path)
    repository = SQLiteCaptureMovesRepository(path)
    repository.replace(capture_id, ["tackle"])
    connection = database.connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        assert repository.get_in_transaction(connection, capture_id) == ("tackle",)
        repository.replace_in_transaction(connection, capture_id, ["growl", "tackle"])
        assert repository.get_in_transaction(connection, capture_id) == ("growl", "tackle")
        connection.rollback()
    finally:
        connection.close()

    assert repository.get(capture_id) == ("tackle",)


# --- Combate en curso ------------------------------------------------------------


def battle_row(**overrides: object) -> dict:
    row: dict = {
        "kind": "wild",
        "state_json": '{"v": 1}',
        "encounter_seen_at": "2026-11-11T08:00:00+00:00",
        "started_at": "2026-11-11T08:01:00+00:00",
        "updated_at": "2026-11-11T08:02:00+00:00",
    }
    row.update(overrides)
    return row


def test_battle_state_is_saved_and_read_back_exactly(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    repository = SQLiteBattleRepository(path)
    assert repository.read() is None

    def save(connection: sqlite3.Connection) -> object:
        assert repository.load_in_transaction(connection) is None
        repository.save_in_transaction(connection, battle_row())
        return repository.load_in_transaction(connection)

    assert in_transaction(path, save) == battle_row()
    assert repository.read() == battle_row()


def test_saving_again_replaces_the_single_battle(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    repository = SQLiteBattleRepository(path)
    in_transaction(
        path, lambda connection: repository.save_in_transaction(connection, battle_row())
    )
    trainer = battle_row(kind="trainer", state_json='{"v": 1, "turn": 3}', updated_at="later")
    del trainer["encounter_seen_at"]

    in_transaction(path, lambda connection: repository.save_in_transaction(connection, trainer))

    assert repository.read() == {**trainer, "encounter_seen_at": None}


def test_a_rolled_back_turn_keeps_the_previous_battle(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    repository = SQLiteBattleRepository(path)
    in_transaction(
        path, lambda connection: repository.save_in_transaction(connection, battle_row())
    )

    def failing_turn(connection: sqlite3.Connection) -> None:
        repository.save_in_transaction(connection, battle_row(state_json='{"v": 1, "turn": 9}'))
        repository.clear_in_transaction(connection)
        raise RuntimeError("turn interrupted")

    with pytest.raises(RuntimeError):
        in_transaction(path, failing_turn)

    assert repository.read() == battle_row()


def test_battle_can_be_cleared_inside_or_outside_a_transaction(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    repository = SQLiteBattleRepository(path)
    in_transaction(
        path, lambda connection: repository.save_in_transaction(connection, battle_row())
    )

    in_transaction(path, repository.clear_in_transaction)
    assert repository.read() is None

    in_transaction(
        path, lambda connection: repository.save_in_transaction(connection, battle_row())
    )
    repository.clear()
    assert repository.read() is None
    repository.clear()


def test_exhibitions_are_never_persisted(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    repository = SQLiteBattleRepository(path)

    with pytest.raises(sqlite3.IntegrityError):
        in_transaction(
            path,
            lambda connection: repository.save_in_transaction(
                connection, battle_row(kind="exhibition")
            ),
        )

    assert repository.read() is None


# --- Récord de combates --------------------------------------------------------------


def record(path: Path, *results: tuple[str, str, str]) -> dict:
    repository = SQLiteBattleRecordRepository(path)

    def apply(connection: sqlite3.Connection) -> None:
        for kind, outcome, at in results:
            repository.record_in_transaction(connection, kind=kind, outcome=outcome, at=at)

    in_transaction(path, apply)
    return repository.read()


def empty_record(**overrides: object) -> dict:
    values: dict = {
        "wild_battles": 0,
        "wild_wins": 0,
        "wild_captures": 0,
        "trainer_wins": 0,
        "trainer_losses": 0,
        "streak": 0,
        "best_streak": 0,
        "last_battle_at": None,
    }
    values.update(overrides)
    return values


def test_record_starts_at_zero(tmp_path: Path) -> None:
    assert SQLiteBattleRecordRepository(tmp_path / "pokedex.db").read() == empty_record()


def test_wild_battles_count_every_battle_plus_wins_and_captures(tmp_path: Path) -> None:
    result = record(
        tmp_path / "pokedex.db",
        ("wild", "won", "t1"),
        ("wild", "captured", "t2"),
        ("wild", "escaped", "t3"),
        ("wild", "lost", "t4"),
        ("wild", "opponent_fled", "t5"),
    )

    assert result == empty_record(wild_battles=5, wild_wins=1, wild_captures=1, last_battle_at="t5")


def test_trainer_streak_grows_with_wins_and_resets_on_a_loss(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    three_wins = [("trainer", "won", f"w{index}") for index in range(3)]

    assert record(path, *three_wins) == empty_record(
        trainer_wins=3, streak=3, best_streak=3, last_battle_at="w2"
    )
    assert record(path, ("trainer", "lost", "l1"), ("trainer", "won", "w3")) == empty_record(
        trainer_wins=4, trainer_losses=1, streak=1, best_streak=3, last_battle_at="w3"
    )


def test_trainer_draw_only_updates_the_date(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    record(path, ("trainer", "won", "w1"))

    assert record(path, ("trainer", "draw", "d1")) == empty_record(
        trainer_wins=1, streak=1, best_streak=1, last_battle_at="d1"
    )


@pytest.mark.parametrize(
    ("kind", "outcome"),
    [("exhibition", "won"), ("wild", "win"), ("trainer", "")],
)
def test_unknown_kinds_or_outcomes_are_rejected_without_writing(
    tmp_path: Path, kind: str, outcome: str
) -> None:
    path = tmp_path / "pokedex.db"

    with pytest.raises(ValueError):
        record(path, ("wild", "won", "t1"), (kind, outcome, "t2"))

    assert SQLiteBattleRecordRepository(path).read() == empty_record()


# --- Progreso de las capturas ----------------------------------------------------------


class LastChoice:
    def __init__(self) -> None:
        self.offered: list[list[str]] = []

    def choice(self, sequence):
        self.offered.append([option.species for option in sequence])
        return sequence[-1]


def cache_species(path: Path, species: str, evolutions: list[dict]) -> None:
    connection = database.connect(path)
    try:
        connection.execute(
            "INSERT INTO species_cache "
            "(species, form, growth_rate, base_experience, level_evolutions, fetched_at) "
            "VALUES (?, 'regular', 'medium', 65, ?, 'now')",
            (species, json.dumps(evolutions)),
        )
        connection.commit()
    finally:
        connection.close()


def capture_progress(path: Path, capture_id: int) -> tuple:
    connection = database.connect(path)
    try:
        row = connection.execute(
            "SELECT level, experience, pending_evolution_species, pending_evolution_form "
            "FROM captures WHERE id = ?",
            (capture_id,),
        ).fetchone()
        return tuple(row)
    finally:
        connection.close()


def test_battle_experience_levels_up_and_queues_the_due_evolution(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    cache_species(path, "pikachu", [{"species": "raichu", "form": "alola", "min_level": 20}])
    capture_id = insert_capture(path, level=19, experience=experience_for_level("medium", 19))
    gained = experience_for_level("medium", 20) + 10

    updated = in_transaction(
        path,
        lambda connection: SQLiteBattleProgressRepository().apply_experience_in_transaction(
            connection, capture_id, gained, 20
        ),
    )

    assert updated is True
    assert capture_progress(path, capture_id) == (20, gained, "raichu", "alola")


def test_experience_below_the_threshold_queues_nothing(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    cache_species(path, "pikachu", [{"species": "raichu", "form": "regular", "min_level": 20}])
    capture_id = insert_capture(path, level=10, experience=experience_for_level("medium", 10))
    gained = experience_for_level("medium", 12)

    in_transaction(
        path,
        lambda connection: SQLiteBattleProgressRepository().apply_experience_in_transaction(
            connection, capture_id, gained, 12
        ),
    )

    assert capture_progress(path, capture_id) == (12, gained, None, None)


def test_branched_evolution_uses_the_injected_choice(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    cache_species(
        path,
        "tyrogue",
        [
            {"species": "hitmonlee", "form": "regular", "min_level": 20},
            {"species": "hitmonchan", "form": "regular", "min_level": 20},
        ],
    )
    capture_id = insert_capture(path, "tyrogue", level=19)
    rng = LastChoice()

    in_transaction(
        path,
        lambda connection: SQLiteBattleProgressRepository(rng=rng).apply_experience_in_transaction(
            connection, capture_id, experience_for_level("medium", 20), 20
        ),
    )

    assert rng.offered == [["hitmonlee", "hitmonchan"]]
    assert capture_progress(path, capture_id)[2] == "hitmonchan"


def test_progress_never_goes_back_nor_drops_a_pending_evolution(tmp_path: Path) -> None:
    # La captura ganó EXP por commits mientras el combate seguía abierto: el
    # combate trae una EXP menor y no debe hacerla bajar.
    path = tmp_path / "pokedex.db"
    cache_species(path, "pikachu", [{"species": "raichu", "form": "regular", "min_level": 20}])
    ahead = experience_for_level("medium", 31)
    capture_id = insert_capture(
        path,
        level=31,
        experience=ahead,
        pending_evolution_species="raichu",
        pending_evolution_form="regular",
    )

    in_transaction(
        path,
        lambda connection: SQLiteBattleProgressRepository().apply_experience_in_transaction(
            connection, capture_id, experience_for_level("medium", 30), 30
        ),
    )

    assert capture_progress(path, capture_id) == (31, ahead, "raichu", "regular")


def test_progress_belongs_to_the_callers_transaction(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    cache_species(path, "pikachu", [{"species": "raichu", "form": "regular", "min_level": 20}])
    capture_id = insert_capture(path, level=19)
    connection = database.connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        SQLiteBattleProgressRepository().apply_experience_in_transaction(
            connection, capture_id, experience_for_level("medium", 25), 25
        )
        connection.rollback()
    finally:
        connection.close()

    assert capture_progress(path, capture_id) == (19, 0, None, None)


def test_progress_for_a_missing_capture_reports_false(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"

    updated = in_transaction(
        path,
        lambda connection: SQLiteBattleProgressRepository().apply_experience_in_transaction(
            connection, 404, 1_000, 10
        ),
    )

    assert updated is False


@pytest.mark.parametrize(("experience", "level"), [(-1, 10), (100, 0), (100, 101)])
def test_progress_rejects_impossible_values(tmp_path: Path, experience: int, level: int) -> None:
    path = tmp_path / "pokedex.db"
    capture_id = insert_capture(path, level=5)

    with pytest.raises(ValueError):
        in_transaction(
            path,
            lambda connection: SQLiteBattleProgressRepository().apply_experience_in_transaction(
                connection, capture_id, experience, level
            ),
        )

    assert capture_progress(path, capture_id)[0] == 5
