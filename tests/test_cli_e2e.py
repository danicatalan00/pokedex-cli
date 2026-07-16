import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from pokedex_cli import storage
from pokedex_cli.infrastructure import database

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUBCOMMANDS = [
    "hook",
    "ver",
    "capturar",
    "bolsas",
    "list",
    "search",
    "vision",
    "equipo",
    "tipos",
    "ranking",
    "legendarios",
    "demo",
    "demo-evolucion",
    "refresh",
    "completion",
]


def isolated_environment(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    data = tmp_path / "data"
    home.mkdir(parents=True)
    data.mkdir(parents=True)
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "XDG_DATA_HOME": str(data),
            "PYTHONPATH": str(PROJECT_ROOT),
            "NO_COLOR": "1",
        }
    )
    return environment


def run_cli(environment: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pokedex_cli", *args],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        input="",
        text=True,
        timeout=15,
    )


def database_path(environment: dict[str, str]) -> Path:
    return Path(environment["XDG_DATA_HOME"]) / "pokedex-cli" / "pokedex.db"


def cache_species(connection, species: str, *, capture_rate: int = 190, mythical=False) -> None:
    storage.upsert_species_cache(
        connection,
        species,
        "regular",
        {
            "pokedex_id": 25,
            "capture_rate": capture_rate,
            "types": ["electric"],
            "hp": 35,
            "atk": 55,
            "def": 40,
            "spa": 50,
            "spd": 50,
            "spe": 90,
            "is_mythical": mythical,
            "growth_rate": "medium",
            "base_experience": 112,
            "level_evolutions": [],
            # gender_rate/abilities present so `pokedex vision` never tries a
            # real PokeAPI refresh in these network-free tests.
            "gender_rate": 4,
            "abilities": ["static", "lightning-rod"],
        },
        "2026-07-15T10:00:00+00:00",
    )


def set_encounter(connection, species: str, *, escape_after: int | None = None) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO encounter_state
            (singleton, species, form, shiny, seen_at, captured,
             failed_capture_attempts, escape_after_attempts)
        VALUES (1, ?, 'regular', 0, '2026-07-15T10:00:00+00:00', 0, 0, ?)
        """,
        (species, escape_after),
    )
    connection.commit()


def test_top_level_and_every_subcommand_help(tmp_path: Path) -> None:
    environment = isolated_environment(tmp_path)
    top = run_cli(environment, "--help")
    assert top.returncode == 0
    assert "usage:" in top.stdout
    for subcommand in SUBCOMMANDS:
        result = run_cli(environment, subcommand, "--help")
        assert result.returncode == 0, (subcommand, result.stderr)
        assert "usage:" in result.stdout


def test_first_execution_and_empty_commands_are_stable(tmp_path: Path) -> None:
    environment = isolated_environment(tmp_path)
    unseen = run_cli(environment, "ver")
    assert unseen.returncode == 1
    assert "No hay ningún Pokémon" in unseen.stdout
    assert "Traceback" not in unseen.stderr

    bags = run_cli(environment, "bolsas")
    listing = run_cli(environment, "list")
    assert bags.returncode == 0
    assert "Bolsa" in bags.stdout
    assert listing.returncode == 0
    assert "Traceback" not in bags.stderr + listing.stderr


@pytest.mark.parametrize("contents", ["{", "null", "[]"])
def test_corrupt_legacy_inventory_is_recoverable(tmp_path: Path, contents: str) -> None:
    environment = isolated_environment(tmp_path)
    legacy = Path(environment["XDG_DATA_HOME"]) / "pokedex-cli" / "inventory.json"
    legacy.parent.mkdir()
    legacy.write_text(contents)
    result = run_cli(environment, "bolsas")
    assert result.returncode == 0
    assert "Traceback" not in result.stderr


def test_unknown_command_has_stable_argparse_failure(tmp_path: Path) -> None:
    result = run_cli(isolated_environment(tmp_path), "missing-command")
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert "Traceback" not in result.stderr


def test_corrupt_database_fails_cleanly(tmp_path: Path) -> None:
    environment = isolated_environment(tmp_path)
    database_path = Path(environment["XDG_DATA_HOME"]) / "pokedex-cli" / "pokedex.db"
    database_path.parent.mkdir()
    database_path.write_bytes(b"not a sqlite database")

    result = run_cli(environment, "list")

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "database" in result.stderr.lower()


def test_version_one_database_is_upgraded_by_the_real_cli(tmp_path: Path) -> None:
    environment = isolated_environment(tmp_path)
    path = database_path(environment)
    path.parent.mkdir(parents=True)
    old = sqlite3.connect(path)
    database.migrate(old, database.MIGRATIONS[:1])
    old.execute(
        "INSERT INTO captures (species, form, shiny, caught_at) "
        "VALUES ('pikachu', 'regular', 0, '2026-07-15')"
    )
    old.commit()
    old.close()

    result = run_cli(environment, "list")

    assert result.returncode == 0
    assert "Pikachu" in result.stdout
    upgraded = database.connect(path)
    try:
        assert (
            upgraded.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            == database.MIGRATIONS[-1][0]
        )
    finally:
        upgraded.close()


def test_non_tty_team_selector_cancels_cleanly_on_eof(tmp_path: Path) -> None:
    environment = isolated_environment(tmp_path)
    connection = database.connect(database_path(environment))
    try:
        capture_id = storage.insert_capture(connection, "pikachu", "regular", False, "now")
    finally:
        connection.close()

    result = run_cli(environment, "equipo", "add")

    assert result.returncode == 0
    assert "Traceback" not in result.stderr
    check = database.connect(database_path(environment))
    try:
        assert (
            check.execute("SELECT in_team FROM captures WHERE id = ?", (capture_id,)).fetchone()[0]
            == 0
        )
    finally:
        check.close()


def test_offline_capture_success_and_failure_are_persisted_atomically(tmp_path: Path) -> None:
    success_environment = isolated_environment(tmp_path / "success")
    assert run_cli(success_environment, "bolsas").returncode == 0
    success_db = database.connect(database_path(success_environment))
    try:
        cache_species(success_db, "pikachu")
        set_encounter(success_db, "pikachu")
        success_db.execute(
            "INSERT INTO inventory_balls (slug, stock) VALUES ('masterball', 1) "
            "ON CONFLICT(slug) DO UPDATE SET stock = 1"
        )
        success_db.commit()
    finally:
        success_db.close()

    caught = run_cli(success_environment, "capturar", "--bola", "master")
    assert caught.returncode == 0
    assert "Capturaste" in caught.stdout
    check = database.connect(database_path(success_environment))
    try:
        assert check.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 1
        assert (
            check.execute("SELECT stock FROM inventory_balls WHERE slug = 'masterball'").fetchone()[
                0
            ]
            == 0
        )
    finally:
        check.close()

    failure_environment = isolated_environment(tmp_path / "failure")
    assert run_cli(failure_environment, "bolsas").returncode == 0
    failure_db = database.connect(database_path(failure_environment))
    try:
        cache_species(failure_db, "magikarp", capture_rate=0)
        set_encounter(failure_db, "magikarp", escape_after=5)
    finally:
        failure_db.close()

    failed = run_cli(failure_environment, "capturar", "--bola", "poke")
    assert failed.returncode == 0
    assert failed.stdout
    assert "Capturaste" not in failed.stdout
    check = database.connect(database_path(failure_environment))
    try:
        state = check.execute("SELECT * FROM encounter_state").fetchone()
        assert state["failed_capture_attempts"] == 1
        assert check.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 0
    finally:
        check.close()


def test_populated_read_commands_and_team_work_without_network(tmp_path: Path) -> None:
    environment = isolated_environment(tmp_path)
    connection = database.connect(database_path(environment))
    try:
        capture_id = storage.insert_capture(
            connection, "pikachu", "regular", False, "2026-07-15T10:00:00+00:00"
        )
        cache_species(connection, "pikachu", mythical=True)
        storage.set_team(connection, capture_id, True)
    finally:
        connection.close()

    expectations = {
        ("list",): "Pikachu",
        ("search", "pikachu"): "Pikachu",
        ("vision", str(capture_id)): "Pikachu",
        ("equipo",): "Tu equipo",
        ("tipos",): "electric",
        ("ranking",): "Pikachu",
        ("legendarios",): "Pikachu",
    }
    for arguments, expected in expectations.items():
        result = run_cli(environment, *arguments)
        assert result.returncode == 0, (arguments, result.stderr)
        assert expected in result.stdout
        assert "Traceback" not in result.stderr


def test_pending_evolution_completes_before_trying_a_wild_encounter(tmp_path: Path) -> None:
    environment = isolated_environment(tmp_path)
    assert run_cli(environment, "bolsas").returncode == 0
    connection = database.connect(database_path(environment))
    try:
        capture_id = storage.insert_capture(connection, "bulbasaur", "regular", False, "now")
        connection.execute(
            "UPDATE captures SET in_team = 1, level = 16, "
            "pending_evolution_species = 'ivysaur', pending_evolution_form = 'regular' "
            "WHERE id = ?",
            (capture_id,),
        )
        cache_species(connection, "ivysaur")
        connection.commit()
    finally:
        connection.close()

    result = run_cli(environment, "hook")
    assert result.returncode == 0
    assert "Traceback" not in result.stderr
    check = database.connect(database_path(environment))
    try:
        evolved = check.execute(
            "SELECT species FROM captures WHERE id = ?", (capture_id,)
        ).fetchone()
        assert evolved["species"] == "ivysaur"
        assert check.execute("SELECT COUNT(*) FROM encounter_state").fetchone()[0] == 0
    finally:
        check.close()


def test_hook_is_clean_with_missing_and_available_krabby(tmp_path: Path) -> None:
    missing_environment = isolated_environment(tmp_path / "missing")
    missing_environment["PATH"] = "/usr/bin:/bin"
    missing = run_cli(missing_environment, "hook", "1")
    assert missing.returncode == 0
    assert "Traceback" not in missing.stderr

    available_environment = isolated_environment(tmp_path / "available")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "krabby"
    executable.write_text(
        "#!/bin/sh\nif [ \"$1\" = list ]; then printf 'pikachu\\n'; else printf 'PIKACHU\\n'; fi\n"
    )
    executable.chmod(0o755)
    available_environment["PATH"] = f"{bin_dir}:/usr/bin:/bin"

    available = run_cli(available_environment, "hook", "1")
    assert available.returncode == 0
    assert "PIKACHU" in available.stdout
    assert "Traceback" not in available.stderr
    connection = database.connect(database_path(available_environment))
    try:
        assert connection.execute("SELECT species FROM encounter_state").fetchone()[0] == "pikachu"
    finally:
        connection.close()
