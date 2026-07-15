import sqlite3
import threading
import time
from pathlib import Path

import pytest

from pokedex_cli.infrastructure import database


def test_new_database_enables_integrity_and_concurrency_pragmas(tmp_path: Path) -> None:
    connection = database.connect(tmp_path / "pokedex.db", busy_timeout_ms=7_500)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 7_500
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row[0] for row in versions] == [1, 2, 3, 4, 5, 6, 7]
    finally:
        connection.close()


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "pokedex.db"
    first = database.connect(path)
    first.close()
    second = database.connect(path)
    try:
        versions = second.execute(
            "SELECT version, COUNT(*) FROM schema_migrations GROUP BY version"
        ).fetchall()
        assert [tuple(row) for row in versions] == [
            (1, 1),
            (2, 1),
            (3, 1),
            (4, 1),
            (5, 1),
            (6, 1),
            (7, 1),
        ]
    finally:
        second.close()


@pytest.mark.parametrize("historical_version", range(1, 8))
def test_every_recorded_historical_schema_upgrades_without_losing_captures(
    tmp_path: Path, historical_version: int
) -> None:
    path = tmp_path / f"v{historical_version}.db"
    old = sqlite3.connect(path)
    database.migrate(old, database.MIGRATIONS[:historical_version])
    old.execute(
        "INSERT INTO captures (species, form, shiny, caught_at) "
        "VALUES ('pikachu', 'regular', 0, 'then')"
    )
    old.commit()
    old.close()

    upgraded = database.connect(path)
    try:
        assert upgraded.execute("SELECT species FROM captures").fetchone()[0] == "pikachu"
        assert [
            row[0]
            for row in upgraded.execute("SELECT version FROM schema_migrations ORDER BY version")
        ] == [1, 2, 3, 4, 5, 6, 7]
        assert database._columns(upgraded, "captures") >= {
            "ball_slug",
            "level",
            "experience",
            "pending_evolution_species",
            "iv_hp",
            "nature",
            "gender",
            "ability",
        }
    finally:
        upgraded.close()


def test_connection_waits_for_a_short_lock_and_then_recovers(tmp_path: Path) -> None:
    path = tmp_path / "locked.db"
    writer = database.connect(path)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        "INSERT INTO captures (species, form, shiny, caught_at) "
        "VALUES ('writer', 'regular', 0, 'now')"
    )

    observed: list[int] = []

    def wait_for_lock() -> None:
        recovered = database.connect(path, busy_timeout_ms=1_000)
        try:
            observed.append(recovered.execute("SELECT COUNT(*) FROM captures").fetchone()[0])
        finally:
            recovered.close()

    waiter = threading.Thread(target=wait_for_lock)
    waiter.start()
    try:
        time.sleep(0.05)
        writer.commit()
        waiter.join(timeout=2)
        assert not waiter.is_alive()
        assert observed == [1]
    finally:
        waiter.join()
        writer.close()


def test_database_rejects_a_seventh_team_member(tmp_path: Path) -> None:
    connection = database.connect(tmp_path / "pokedex.db")
    try:
        for index in range(6):
            connection.execute(
                "INSERT INTO captures (species, form, shiny, caught_at, in_team) "
                "VALUES (?, 'regular', 0, 'now', 1)",
                (f"pokemon-{index}",),
            )
        with pytest.raises(sqlite3.IntegrityError, match="team_limit"):
            connection.execute(
                "INSERT INTO captures (species, form, shiny, caught_at, in_team) "
                "VALUES ('seventh', 'regular', 0, 'now', 1)"
            )
    finally:
        connection.rollback()
        connection.close()


def test_failing_migration_rolls_back_its_schema_and_version(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "rollback.db")

    def broken_migration(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE partial_state (id INTEGER PRIMARY KEY)")
        raise RuntimeError("migration interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        database.migrate(connection, ((1, broken_migration),))

    assert (
        connection.execute("SELECT name FROM sqlite_master WHERE name = 'partial_state'").fetchone()
        is None
    )
    assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    connection.close()


def test_migration_007_backfills_deterministic_ivs_and_nature_but_not_gender_or_ability(
    tmp_path: Path,
) -> None:
    path = tmp_path / "individuality.db"
    old = sqlite3.connect(path)
    database.migrate(old, database.MIGRATIONS[:6])
    old.execute(
        "INSERT INTO captures (id, species, form, shiny, caught_at) "
        "VALUES (1, 'pikachu', 'regular', 0, '2026-07-15T10:00:00+00:00')"
    )
    old.commit()
    old.close()

    from pokedex_cli.domain.individuality import derive_ivs_nature

    expected_ivs, expected_nature = derive_ivs_nature("1:2026-07-15T10:00:00+00:00")

    upgraded = database.connect(path)
    try:
        row = upgraded.execute(
            "SELECT iv_hp, iv_atk, iv_def, iv_spa, iv_spd, iv_spe, nature, gender, ability "
            "FROM captures WHERE id = 1"
        ).fetchone()
        assert (row["iv_hp"], row["iv_atk"], row["iv_def"], row["iv_spa"], row["iv_spd"]) == (
            expected_ivs["hp"],
            expected_ivs["atk"],
            expected_ivs["def"],
            expected_ivs["spa"],
            expected_ivs["spd"],
        )
        assert row["iv_spe"] == expected_ivs["spe"]
        assert row["nature"] == expected_nature.name
        assert row["gender"] is None
        assert row["ability"] is None
        assert database._columns(upgraded, "species_cache") >= {"gender_rate", "abilities"}
    finally:
        upgraded.close()

    # Applying it again (a fresh connection re-running every registered
    # migration) must be a no-op: same values, no crash.
    again = database.connect(path)
    try:
        row = again.execute("SELECT iv_hp, nature FROM captures WHERE id = 1").fetchone()
        assert row["iv_hp"] == expected_ivs["hp"]
        assert row["nature"] == expected_nature.name
    finally:
        again.close()
