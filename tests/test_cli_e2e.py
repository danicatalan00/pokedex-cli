import os
import re
import socket
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from pokedex_cli import storage
from pokedex_cli.domain.battle.model import DamageClass, Move, StatChange
from pokedex_cli.domain.battle.moves import FALLBACK_BY_TYPE, TACKLE, move_to_record
from pokedex_cli.domain.progression import experience_for_level
from pokedex_cli.infrastructure import database
from pokedex_cli.infrastructure.repositories import (
    SQLiteMoveCacheRepository,
    SQLiteSpeciesCacheRepository,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUBCOMMANDS = [
    "hook",
    "ver",
    "capturar",
    "roca",
    "cebo",
    "bolsas",
    "list",
    "search",
    "vision",
    "equipo",
    "combate",
    "batalla",
    "demo-combate",
    "movimientos",
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


def run_cli(
    environment: dict[str, str], *args: str, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pokedex_cli", *args],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        input=stdin,
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
    # Learnset ya consultado (vacío): la ficha de movimientos de `vision` y el
    # combate tampoco refrescan el perfil.
    connection.execute(
        "UPDATE species_cache SET level_moves = '[]' WHERE species = ? AND form = 'regular'",
        (species,),
    )
    connection.commit()


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


def test_safari_items_change_the_encounter_and_eventually_end_it(tmp_path: Path) -> None:
    """Un encuentro completo de Zona Safari sin red: la roca sube la captura y
    gasta paciencia, el cebo la devuelve, y las rocas acaban espantándolo."""
    environment = isolated_environment(tmp_path)
    connection = database.connect(database_path(environment))
    try:
        cache_species(connection, "pikachu")
        set_encounter(connection, "pikachu", escape_after=4)
    finally:
        connection.close()

    rock = run_cli(environment, "roca")
    assert rock.returncode == 0
    assert "enfadado" in rock.stdout
    assert "Traceback" not in rock.stderr

    check = database.connect(database_path(environment))
    try:
        state = check.execute("SELECT * FROM encounter_state").fetchone()
        assert (state["catch_stage"], state["item_turns"], state["escape_after_attempts"]) == (
            1,
            1,
            3,
        )
        # Un ítem no gasta lanzamientos: la Pokeball sigue intacta.
        assert state["failed_capture_attempts"] == 0
    finally:
        check.close()

    # El jugador ve palabras; las cifras solo aparecen si las pide.
    seen = run_cli(environment, "ver")
    assert seen.returncode == 0
    assert "está enfadado" in seen.stdout
    assert "paciencia" not in seen.stdout

    debug = run_cli(environment, "ver", "--debug")
    assert debug.returncode == 0
    assert "captura ×2 · paciencia 2/3" in " ".join(debug.stdout.split())

    bait = run_cli(environment, "cebo")
    assert bait.returncode == 0
    assert "comiendo" in bait.stdout
    check = database.connect(database_path(environment))
    try:
        state = check.execute("SELECT * FROM encounter_state").fetchone()
        assert (state["catch_stage"], state["item_turns"], state["escape_after_attempts"]) == (
            0,
            2,
            5,
        )
    finally:
        check.close()

    # Cada roca cuesta dos turnos de paciencia (el que gasta y el que le quita),
    # así que desde 5 con 2 gastados la segunda lo espanta.
    first, second = run_cli(environment, "roca"), run_cli(environment, "roca")
    assert (first.returncode, second.returncode) == (0, 0)
    assert "ha huido" not in first.stdout
    assert "se ha enfadado y ha huido" in second.stdout
    check = database.connect(database_path(environment))
    try:
        assert check.execute("SELECT COUNT(*) FROM encounter_state").fetchone()[0] == 0
    finally:
        check.close()

    gone = run_cli(environment, "roca")
    assert gone.returncode == 1
    assert "No hay ningún Pokémon" in gone.stdout


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


def test_evolution_chain_remains_queued_across_consecutive_terminal_opens(tmp_path: Path) -> None:
    environment = isolated_environment(tmp_path)
    assert run_cli(environment, "bolsas").returncode == 0
    connection = database.connect(database_path(environment))
    try:
        capture_id = storage.insert_capture(connection, "charmander", "regular", False, "now")
        connection.execute(
            "UPDATE captures SET in_team = 1, level = 36, "
            "pending_evolution_species = 'charmeleon', pending_evolution_form = 'regular' "
            "WHERE id = ?",
            (capture_id,),
        )
        for species in ("charmander", "charmeleon", "charizard"):
            cache_species(connection, species)
        connection.execute(
            "UPDATE species_cache SET level_evolutions = ? WHERE species = 'charmeleon'",
            ('[{"species":"charizard","form":"regular","min_level":36}]',),
        )
        connection.commit()
    finally:
        connection.close()

    first = run_cli(environment, "hook")
    assert first.returncode == 0
    check = database.connect(database_path(environment))
    try:
        row = check.execute(
            "SELECT species, pending_evolution_species FROM captures WHERE id = ?",
            (capture_id,),
        ).fetchone()
        assert (row["species"], row["pending_evolution_species"]) == (
            "charmeleon",
            "charizard",
        )
    finally:
        check.close()

    second = run_cli(environment, "hook")
    assert second.returncode == 0
    check = database.connect(database_path(environment))
    try:
        row = check.execute(
            "SELECT species, pending_evolution_species FROM captures WHERE id = ?",
            (capture_id,),
        ).fetchone()
        assert (row["species"], row["pending_evolution_species"]) == ("charizard", None)
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


def test_a_broken_battle_table_never_stops_the_hook_from_drawing_an_encounter(
    tmp_path: Path,
) -> None:
    environment = isolated_environment(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "krabby"
    executable.write_text(
        "#!/bin/sh\nif [ \"$1\" = list ]; then printf 'pikachu\\n'; else printf 'PIKACHU\\n'; fi\n"
    )
    executable.chmod(0o755)
    environment["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    connection = database.connect(database_path(environment))
    try:
        set_encounter(connection, "pidgey")
        # La consulta del combate falla de verdad: la tabla ya no existe.
        connection.execute("DROP TABLE battle_state")
        connection.commit()
    finally:
        connection.close()

    hook = run_cli(environment, "hook", "1")

    assert hook.returncode == 0
    assert "Traceback" not in hook.stderr
    assert "PIKACHU" in hook.stdout
    assert "Combate en pausa" not in hook.stdout
    connection = database.connect(database_path(environment))
    try:
        assert connection.execute("SELECT species FROM encounter_state").fetchone()[0] == "pikachu"
    finally:
        connection.close()


# --- Modo combate: de punta a punta, sin red ni TTY ---------------------------------

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
BATTLE_MOVES = (SCRATCH, GROWL, TACKLE, FALLBACK_BY_TYPE["fire"])


def battle_profile(pokedex_id: int, types, stats, learnset, **extra) -> dict:
    data = {
        "pokedex_id": pokedex_id,
        "capture_rate": 45,
        "types": list(types),
        **dict(zip(("hp", "atk", "def", "spa", "spd", "spe"), stats)),
        "is_legendary": False,
        "is_mythical": False,
        "generation": "generation-i",
        "flavor_text": "Datos de prueba.",
        "form_data_exact": True,
        "growth_rate": "medium",
        "base_experience": 64,
        "encounter_level": 5,
        "level_evolutions": [],
        "gender_rate": 4,
        "abilities": ["keen-eye"],
        # Learnset ya en caché: preparar el combate no necesita PokeAPI.
        "level_moves": learnset,
        "moves_version_group": "emerald",
    }
    data.update(extra)
    return data


BATTLE_SPECIES = {
    "charmander": battle_profile(
        4, ["fire"], (39, 52, 43, 60, 50, 65), [["scratch", 1], ["growl", 1], ["ember", 7]]
    ),
    "pidgey": battle_profile(
        16, ["normal", "flying"], (40, 45, 40, 35, 35, 56), [["tackle", 1], ["growl", 1]]
    ),
}


def seed_battle_world(
    environment: dict[str, str],
    *,
    encounter: bool = True,
    learnset: bool = True,
    moves: bool = True,
) -> int:
    """BD lista para combatir sin red: un Charmander de nivel 50 en el equipo,
    especies con learnset y todos sus movimientos en caché. ``learnset`` y
    ``moves`` a False dejan la caché como antes del combate: perfiles sin
    learnset (NULL) o sin ningún movimiento descargado."""
    path = database_path(environment)
    connection = database.connect(path)
    try:
        capture_id = storage.insert_capture(
            connection, "charmander", "regular", False, "2026-07-15T10:00:00+00:00"
        )
        connection.execute(
            "UPDATE captures SET in_team = 1, level = 50, experience = ? WHERE id = ?",
            (experience_for_level("medium", 50), capture_id),
        )
        connection.commit()
        if encounter:
            set_encounter(connection, "pidgey")
    finally:
        connection.close()
    species = SQLiteSpeciesCacheRepository(path)
    for name, data in BATTLE_SPECIES.items():
        if not learnset:
            data = {
                key: value
                for key, value in data.items()
                if key not in ("level_moves", "moves_version_group")
            }
        species.put(name, "regular", data, "2026-11-11T08:00:00+00:00")
    if moves:
        cache = SQLiteMoveCacheRepository(path)
        for move in BATTLE_MOVES:
            cache.put(move.slug, move_to_record(move), "2026-11-11T08:00:00+00:00")
    return capture_id


def battle_rows(environment: dict[str, str]) -> dict:
    check = database.connect(database_path(environment))
    try:
        return {
            "battles": check.execute("SELECT COUNT(*) FROM battle_state").fetchone()[0],
            "encounters": check.execute("SELECT COUNT(*) FROM encounter_state").fetchone()[0],
            "record": dict(check.execute("SELECT * FROM battle_record").fetchone() or {}),
            "experience": check.execute("SELECT experience FROM captures").fetchone()[0],
        }
    finally:
        check.close()


def test_an_auto_battle_against_the_waiting_pokemon_runs_to_the_end_offline(tmp_path: Path):
    environment = isolated_environment(tmp_path)
    seed_battle_world(environment)
    before = battle_rows(environment)["experience"]

    result = run_cli(environment, "combate", "--auto")

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert "¡Un Pidgey salvaje apareció!" in result.stdout
    assert "¡Adelante, Charmander!" in result.stdout
    assert "Combate contra Pidgey salvaje" in result.stdout
    rows = battle_rows(environment)
    assert rows["battles"] == 0
    # Debilitado (o huido): el Pokémon salvaje ya no está.
    assert rows["encounters"] == 0
    assert rows["record"]["wild_battles"] == 1
    if rows["record"]["wild_wins"]:
        assert rows["experience"] > before


def test_an_exhibition_auto_battle_plays_without_saving_anything(tmp_path: Path):
    environment = isolated_environment(tmp_path)
    capture_id = seed_battle_world(environment, encounter=False)
    before = battle_rows(environment)

    result = run_cli(environment, "demo-combate", str(capture_id), "pidgey", "-n", "20", "--auto")

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert "¡Comienza el combate de exhibición!" in result.stdout
    assert "no se ha guardado nada" in result.stdout
    after = battle_rows(environment)
    assert after["experience"] == before["experience"]
    assert after["battles"] == 0
    assert not after["record"]


def test_a_paused_battle_keeps_its_rival_and_blocks_loose_capture_orders(tmp_path: Path):
    environment = isolated_environment(tmp_path)
    environment["PATH"] = "/usr/bin:/bin"  # sin krabby: el hook no pintaría nada nuevo
    seed_battle_world(environment)

    paused = run_cli(environment, "combate", stdin="q\n")
    assert paused.returncode == 0, paused.stderr
    assert "Combate en pausa: vuelve con `pokedex combate`." in paused.stdout
    assert battle_rows(environment)["battles"] == 1

    hook = run_cli(environment, "hook", "1")
    assert hook.returncode == 0
    assert "Combate en pausa contra Pidgey salvaje" in hook.stdout
    seen = run_cli(environment, "ver")
    assert "· en combate" in seen.stdout
    for command in ("capturar", "roca", "cebo"):
        blocked = run_cli(environment, command)
        assert blocked.returncode == 1, command
        assert "Estás combatiendo contra Pidgey" in blocked.stdout
    rows = battle_rows(environment)
    assert (rows["battles"], rows["encounters"]) == (1, 1)

    resumed = run_cli(environment, "batalla", "--auto")
    assert resumed.returncode == 0, resumed.stderr
    assert "¡Sigue el combate contra Pidgey salvaje!" in resumed.stdout
    assert battle_rows(environment)["battles"] == 0


def test_the_moves_command_shows_and_changes_a_moveset_offline(tmp_path: Path):
    environment = isolated_environment(tmp_path)
    capture_id = seed_battle_world(environment, encounter=False)

    shown = run_cli(environment, "movimientos", str(capture_id))
    assert shown.returncode == 0, shown.stderr
    assert "Moveset automático" in shown.stdout
    assert "Ascuas" in shown.stdout

    # Con la opción delante, como en la línea de uso: --set no se come el ID.
    chosen = run_cli(environment, "movimientos", "--set", "arañazo, ascuas", str(capture_id))
    assert chosen.returncode == 0, chosen.stderr
    assert "guardado: Arañazo, Ascuas" in " ".join(chosen.stdout.split())
    vision = run_cli(environment, "vision", str(capture_id))
    assert "Movimientos · elegidos por ti" in vision.stdout

    rejected = run_cli(environment, "movimientos", str(capture_id), "--set", "hiperrayo")
    assert rejected.returncode == 1
    assert "no puede aprender" in rejected.stdout

    reset = run_cli(environment, "movimientos", str(capture_id), "--auto")
    assert reset.returncode == 0
    assert "vuelve al moveset automático" in reset.stdout

    nonsense = run_cli(environment, "movimientos", "?")
    assert nonsense.returncode == 1
    assert "Traceback" not in nonsense.stderr
    assert "No tienes ninguna captura que encaje con" in nonsense.stdout


@pytest.fixture
def unreachable_proxy() -> Iterator[str]:
    """Proxy en un puerto local ligado y sin escuchar: toda petición HTTP del
    proceso hijo se rechaza al instante, sin salir nunca a Internet."""
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        yield f"http://127.0.0.1:{reserved.getsockname()[1]}"


def offline(environment: dict[str, str], proxy: str) -> dict[str, str]:
    for key in ("NO_PROXY", "no_proxy"):
        environment.pop(key, None)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        environment[key] = proxy
    return environment


@pytest.mark.parametrize(
    "learnset", [True, False], ids=["learnset-sin-movimientos", "sin-learnset"]
)
def test_an_auto_battle_with_an_empty_move_cache_falls_back_to_reserve_moves(
    tmp_path: Path, unreachable_proxy: str, learnset: bool
):
    environment = offline(isolated_environment(tmp_path), unreachable_proxy)
    seed_battle_world(environment, learnset=learnset, moves=False)

    result = run_cli(environment, "combate", "--auto")

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    # Sin datos de movimientos, Charmander lucha con la reserva de su tipo.
    assert re.search(r"¡Charmander usó (Ascuas|Placaje)!", result.stdout), result.stdout
    assert "Combate contra Pidgey salvaje" in result.stdout
    rows = battle_rows(environment)
    assert rows["battles"] == 0
    assert rows["record"]["wild_battles"] == 1
    check = database.connect(database_path(environment))
    try:
        # Nada que guardar: las descargas fallaron y no se cachea un fallo.
        assert check.execute("SELECT COUNT(*) FROM move_cache").fetchone()[0] == 0
    finally:
        check.close()


def test_a_battle_whose_encounter_changed_no_longer_blocks_anything(
    tmp_path: Path, unreachable_proxy: str
):
    environment = offline(isolated_environment(tmp_path), unreachable_proxy)
    environment["PATH"] = "/usr/bin:/bin"
    seed_battle_world(environment)
    paused = run_cli(environment, "combate", stdin="q\n")
    assert paused.returncode == 0, paused.stderr
    connection = database.connect(database_path(environment))
    try:
        # Otro encuentro ocupa su sitio (p. ej., un hook que no pudo consultar
        # el combate): el combate guardado queda obsoleto.
        cache_species(connection, "rattata")
        connection.execute(
            "UPDATE encounter_state SET species = 'rattata', seen_at = ?",
            ("2026-11-11T09:00:00+00:00",),
        )
        connection.commit()
    finally:
        connection.close()

    seen = run_cli(environment, "ver")
    assert seen.returncode == 0, seen.stderr
    assert "en combate" not in seen.stdout
    capture = run_cli(environment, "capturar", "--bola", "poke")
    assert "Traceback" not in capture.stderr
    assert "Estás combatiendo" not in capture.stdout
    assert capture.returncode == 0, capture.stdout + capture.stderr
