"""SQLite connection policy and numbered, transactional migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path

from pokedex_cli.domain.individuality import STAT_KEYS, derive_ivs_nature

Migration = tuple[int, Callable[[sqlite3.Connection], None]]
DEFAULT_BUSY_TIMEOUT_MS = 5_000


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _migration_001_base_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS captures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            species TEXT NOT NULL,
            form TEXT NOT NULL DEFAULT 'regular',
            shiny INTEGER NOT NULL DEFAULT 0 CHECK (shiny IN (0, 1)),
            caught_at TEXT NOT NULL,
            nickname TEXT,
            in_team INTEGER NOT NULL DEFAULT 0 CHECK (in_team IN (0, 1))
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_captures_species ON captures(species)")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS species_cache (
            species TEXT NOT NULL,
            form TEXT NOT NULL DEFAULT 'regular',
            pokedex_id INTEGER,
            types TEXT,
            hp INTEGER,
            atk INTEGER,
            def INTEGER,
            spa INTEGER,
            spd INTEGER,
            spe INTEGER,
            is_legendary INTEGER,
            is_mythical INTEGER,
            generation TEXT,
            flavor_text TEXT,
            form_data_exact INTEGER NOT NULL DEFAULT 1,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (species, form)
        )
        """
    )


def _migration_002_capture_rules(connection: sqlite3.Connection) -> None:
    cache_columns = _columns(connection, "species_cache")
    if "capture_rate" not in cache_columns:
        connection.execute("ALTER TABLE species_cache ADD COLUMN capture_rate INTEGER")
    capture_columns = _columns(connection, "captures")
    if "ball_slug" not in capture_columns:
        connection.execute(
            "ALTER TABLE captures ADD COLUMN ball_slug TEXT NOT NULL DEFAULT 'pokeball'"
        )
    legacy_slugs = {
        "pokebola": "pokeball",
        "superbola": "superball",
        "ultrabola": "ultraball",
        "masterbola": "masterball",
    }
    for old_slug, new_slug in legacy_slugs.items():
        connection.execute(
            "UPDATE captures SET ball_slug = ? WHERE ball_slug = ?",
            (new_slug, old_slug),
        )


def _migration_003_progression(connection: sqlite3.Connection) -> None:
    capture_columns = _columns(connection, "captures")
    capture_additions = {
        "level": "INTEGER NOT NULL DEFAULT 5",
        "experience": "INTEGER NOT NULL DEFAULT 0",
        "pending_evolution_species": "TEXT",
        "pending_evolution_form": "TEXT",
    }
    for column, declaration in capture_additions.items():
        if column not in capture_columns:
            connection.execute(f"ALTER TABLE captures ADD COLUMN {column} {declaration}")

    cache_columns = _columns(connection, "species_cache")
    cache_additions = {
        "growth_rate": "TEXT",
        "base_experience": "INTEGER",
        "level_evolutions": "TEXT NOT NULL DEFAULT '[]'",
    }
    for column, declaration in cache_additions.items():
        if column not in cache_columns:
            connection.execute(f"ALTER TABLE species_cache ADD COLUMN {column} {declaration}")


def _migration_004_inventory_and_activity(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory_balls (
            slug TEXT PRIMARY KEY,
            stock INTEGER NOT NULL CHECK (stock >= 0),
            CHECK (
                (slug = 'superball' AND stock <= 10) OR
                (slug = 'ultraball' AND stock <= 5) OR
                (slug = 'masterball' AND stock <= 1)
            )
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_state (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            started_at TEXT NOT NULL,
            last_passive_at TEXT NOT NULL,
            last_synced_at TEXT NOT NULL,
            last_repo_scan_at TEXT,
            repositories_json TEXT NOT NULL DEFAULT '[]',
            work_commits INTEGER NOT NULL DEFAULT 0 CHECK (work_commits >= 0)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_commits (
            oid TEXT PRIMARY KEY,
            sequence INTEGER NOT NULL UNIQUE
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS legacy_imports (
            kind TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _migration_005_encounters(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS encounter_state (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            species TEXT NOT NULL,
            form TEXT NOT NULL,
            shiny INTEGER NOT NULL CHECK (shiny IN (0, 1)),
            seen_at TEXT NOT NULL,
            captured INTEGER NOT NULL DEFAULT 0 CHECK (captured IN (0, 1)),
            failed_capture_attempts INTEGER NOT NULL DEFAULT 0
                CHECK (failed_capture_attempts >= 0),
            escape_after_attempts INTEGER CHECK (escape_after_attempts > 0)
        )
        """
    )


def _migration_006_team_limit(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS captures_team_limit_insert
        BEFORE INSERT ON captures
        WHEN NEW.in_team = 1
         AND (SELECT COUNT(*) FROM captures WHERE in_team = 1) >= 6
        BEGIN
            SELECT RAISE(ABORT, 'team_limit');
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS captures_team_limit_update
        BEFORE UPDATE OF in_team ON captures
        WHEN NEW.in_team = 1 AND OLD.in_team = 0
         AND (SELECT COUNT(*) FROM captures WHERE in_team = 1) >= 6
        BEGIN
            SELECT RAISE(ABORT, 'team_limit');
        END
        """
    )


def _migration_007_individuality(connection: sqlite3.Connection) -> None:
    capture_columns = _columns(connection, "captures")
    capture_additions = {
        "iv_hp": "INTEGER",
        "iv_atk": "INTEGER",
        "iv_def": "INTEGER",
        "iv_spa": "INTEGER",
        "iv_spd": "INTEGER",
        "iv_spe": "INTEGER",
        "nature": "TEXT",
        "gender": "TEXT",
        "ability": "TEXT",
    }
    for column, declaration in capture_additions.items():
        if column not in capture_columns:
            connection.execute(f"ALTER TABLE captures ADD COLUMN {column} {declaration}")

    cache_columns = _columns(connection, "species_cache")
    cache_additions = {
        "gender_rate": "INTEGER",
        "abilities": "TEXT",
    }
    for column, declaration in cache_additions.items():
        if column not in cache_columns:
            connection.execute(f"ALTER TABLE species_cache ADD COLUMN {column} {declaration}")

    # Retroactive, deterministic individuality for captures that predate this
    # migration: same capture id + caught_at always derives the same IVs and
    # nature, so this is safe to run more than once. Gender and ability need
    # species data (gender_rate/abilities) that may not be cached yet; those
    # are filled lazily by application.individuality.BackfillIndividuality.
    pending = connection.execute(
        "SELECT id, caught_at FROM captures WHERE iv_hp IS NULL"
    ).fetchall()
    for row in pending:
        capture_id, caught_at = row[0], row[1]
        ivs, nature = derive_ivs_nature(f"{capture_id}:{caught_at}")
        connection.execute(
            "UPDATE captures SET iv_hp = ?, iv_atk = ?, iv_def = ?, iv_spa = ?, "
            "iv_spd = ?, iv_spe = ?, nature = ? WHERE id = ?",
            (*(ivs[key] for key in STAT_KEYS), nature.name, capture_id),
        )


def _migration_008_sightings(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sightings (
            species TEXT NOT NULL,
            form TEXT NOT NULL DEFAULT 'regular',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            times_seen INTEGER NOT NULL DEFAULT 1 CHECK (times_seen >= 1),
            PRIMARY KEY (species, form)
        )
        """
    )
    # Retroactive backfill, idempotent via INSERT OR IGNORE: every species the
    # player already caught or had waiting counts as "seen" from day one.
    for species, form, caught_at in connection.execute(
        "SELECT species, form, caught_at FROM captures"
    ).fetchall():
        connection.execute(
            "INSERT OR IGNORE INTO sightings (species, form, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?)",
            (species, form, caught_at, caught_at),
        )
    encounter = connection.execute(
        "SELECT species, form, seen_at FROM encounter_state WHERE singleton = 1"
    ).fetchone()
    if encounter is not None:
        species, form, seen_at = encounter[0], encounter[1], encounter[2]
        connection.execute(
            "INSERT OR IGNORE INTO sightings (species, form, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?)",
            (species, form, seen_at, seen_at),
        )


def _migration_009_dex_caught(connection: sqlite3.Connection) -> None:
    """Registro de Pokédex al estilo del juego: una especie 'capturada' lo es
    para siempre, aunque la captura evolucione después (la fila de captures
    cambia de especie al evolucionar y perdía el registro del preevolucionado).
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS dex_caught (
            species TEXT NOT NULL,
            form TEXT NOT NULL DEFAULT 'regular',
            first_caught_at TEXT NOT NULL,
            PRIMARY KEY (species, form)
        )
        """
    )


def _migration_010_encounter_levels(connection: sqlite3.Connection) -> None:
    if "encounter_level" not in _columns(connection, "species_cache"):
        connection.execute(
            "ALTER TABLE species_cache ADD COLUMN encounter_level INTEGER NOT NULL DEFAULT 5"
        )
    connection.execute(
        "INSERT OR IGNORE INTO dex_caught (species, form, first_caught_at) "
        "SELECT species, form, MIN(caught_at) FROM captures GROUP BY species, form"
    )
    # Forense retroactivo: los avistamientos que la migración 008 rellenó
    # desde captures llevan first_seen_at == caught_at exacto (microsegundos
    # incluidos). Si esa especie ya no está en captures es que la captura
    # evolucionó entre la 008 y esta migración: también estuvo capturada.
    connection.execute(
        """
        INSERT OR IGNORE INTO dex_caught (species, form, first_caught_at)
        SELECT s.species, s.form, s.first_seen_at
        FROM sightings s
        WHERE EXISTS (SELECT 1 FROM captures c WHERE c.caught_at = s.first_seen_at)
        """
    )


def _migration_011_pokedex_profiles(connection: sqlite3.Connection) -> None:
    """Conserva el perfil enciclopédico y la familia completa de PokéAPI."""
    cache_columns = _columns(connection, "species_cache")
    additions = {
        "height_dm": "INTEGER",
        "weight_hg": "INTEGER",
        "genus": "TEXT",
        "habitat": "TEXT",
        "color": "TEXT",
        "shape": "TEXT",
        "egg_groups": "TEXT NOT NULL DEFAULT '[]'",
        "base_happiness": "INTEGER",
        "hatch_counter": "INTEGER",
        "evolution_chain": "TEXT NOT NULL DEFAULT '[]'",
    }
    for column, declaration in additions.items():
        if column not in cache_columns:
            connection.execute(f"ALTER TABLE species_cache ADD COLUMN {column} {declaration}")


MIGRATIONS: tuple[Migration, ...] = (
    (1, _migration_001_base_schema),
    (2, _migration_002_capture_rules),
    (3, _migration_003_progression),
    (4, _migration_004_inventory_and_activity),
    (5, _migration_005_encounters),
    (6, _migration_006_team_limit),
    (7, _migration_007_individuality),
    (8, _migration_008_sightings),
    (9, _migration_009_dex_caught),
    (10, _migration_010_encounter_levels),
    (11, _migration_011_pokedex_profiles),
)


def migrate(
    connection: sqlite3.Connection,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for version, migration in migrations:
        try:
            connection.execute("BEGIN IMMEDIATE")
            already_applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
            ).fetchone()
            if already_applied is not None:
                connection.commit()
                continue
            migration(connection)
            connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def connect(
    path: Path,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=busy_timeout_ms / 1_000)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        migrate(connection)
        return connection
    except BaseException:
        connection.close()
        raise
