import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pokedex_cli.infrastructure import database
from pokedex_cli.infrastructure.repositories import SQLiteEncounterRepository


def encounter() -> dict:
    return {
        "species": "pikachu",
        "form": "regular",
        "shiny": False,
        "seen_at": "2026-07-15T10:00:00+00:00",
        "captured": False,
        "failed_capture_attempts": 0,
        "escape_after_attempts": None,
    }


def test_legacy_encounter_imports_once_and_clear_does_not_resurrect_it(
    tmp_path: Path,
) -> None:
    legacy_path = tmp_path / "last_seen.json"
    legacy_path.write_text(json.dumps(encounter()))
    repository = SQLiteEncounterRepository(tmp_path / "pokedex.db", legacy_path)

    assert repository.read() == encounter()
    repository.clear()
    assert repository.read() is None
    assert legacy_path.exists()


def test_invalid_or_incomplete_legacy_encounter_is_ignored(tmp_path: Path) -> None:
    legacy_path = tmp_path / "last_seen.json"
    legacy_path.write_text('{"species": "pikachu"}')
    repository = SQLiteEncounterRepository(tmp_path / "pokedex.db", legacy_path)

    assert repository.read() is None


def test_twenty_concurrent_updates_preserve_every_failed_attempt(tmp_path: Path) -> None:
    repository = SQLiteEncounterRepository(tmp_path / "pokedex.db", tmp_path / "last_seen.json")
    repository.write(encounter())

    def record_failure(_: int) -> None:
        def operation(state: dict | None) -> None:
            assert state is not None
            state["failed_capture_attempts"] += 1
            state["escape_after_attempts"] = 25

        repository.update(operation)

    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(record_failure, range(20)))

    final = repository.read()
    assert final is not None
    assert final["failed_capture_attempts"] == 20
    assert final["escape_after_attempts"] == 25


def test_write_records_a_sighting_in_the_same_transaction(tmp_path: Path) -> None:
    db_path = tmp_path / "pokedex.db"
    repository = SQLiteEncounterRepository(db_path, tmp_path / "last_seen.json")

    repository.write(encounter())

    connection = database.connect(db_path)
    try:
        row = connection.execute(
            "SELECT form, first_seen_at, last_seen_at, times_seen FROM sightings "
            "WHERE species = 'pikachu'"
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert row["form"] == "regular"
    assert row["first_seen_at"] == "2026-07-15T10:00:00+00:00"
    assert row["last_seen_at"] == "2026-07-15T10:00:00+00:00"
    assert row["times_seen"] == 1


def test_two_writes_of_the_same_species_increment_times_seen_and_last_seen(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "pokedex.db"
    repository = SQLiteEncounterRepository(db_path, tmp_path / "last_seen.json")

    repository.write(encounter())
    second = encounter()
    second["seen_at"] = "2026-07-15T11:30:00+00:00"
    repository.write(second)

    connection = database.connect(db_path)
    try:
        row = connection.execute(
            "SELECT first_seen_at, last_seen_at, times_seen FROM sightings "
            "WHERE species = 'pikachu'"
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert row["first_seen_at"] == "2026-07-15T10:00:00+00:00"
    assert row["last_seen_at"] == "2026-07-15T11:30:00+00:00"
    assert row["times_seen"] == 2


def test_a_failure_after_the_sighting_upsert_rolls_back_the_whole_write(
    tmp_path: Path, monkeypatch
) -> None:
    """The encounter row and its sighting are one atomic unit: a failure
    later in the same transaction must roll back both."""
    db_path = tmp_path / "pokedex.db"
    repository = SQLiteEncounterRepository(db_path, tmp_path / "last_seen.json")

    def boom(connection: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(repository, "_mark_imported", boom)

    try:
        repository.write(encounter())
    except RuntimeError:
        pass

    connection = database.connect(db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM encounter_state").fetchone()[0] == 0
    finally:
        connection.close()
