import json
import sqlite3
from typing import Any, cast

from pokedex_cli.infrastructure import database
from pokedex_cli.infrastructure.paths import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    species TEXT NOT NULL,
    form TEXT NOT NULL DEFAULT 'regular',
    shiny INTEGER NOT NULL DEFAULT 0,
    caught_at TEXT NOT NULL,
    ball_slug TEXT NOT NULL DEFAULT 'pokeball',
    nickname TEXT,
    in_team INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 5,
    experience INTEGER NOT NULL DEFAULT 0,
    pending_evolution_species TEXT,
    pending_evolution_form TEXT
);
CREATE INDEX IF NOT EXISTS idx_captures_species ON captures(species);

CREATE TABLE IF NOT EXISTS species_cache (
    species TEXT NOT NULL,
    form TEXT NOT NULL DEFAULT 'regular',
    pokedex_id INTEGER,
    capture_rate INTEGER,
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
    growth_rate TEXT,
    base_experience INTEGER,
    level_evolutions TEXT NOT NULL DEFAULT '[]',
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (species, form)
);
"""


def get_connection() -> sqlite3.Connection:
    return database.connect(DB_PATH)


def _migrate(conn: sqlite3.Connection) -> None:
    """Compatibility entry point for callers that already own a connection."""
    database.migrate(conn)


def insert_capture(
    conn: sqlite3.Connection,
    species: str,
    form: str,
    shiny: bool,
    caught_at: str,
    ball_slug: str = "pokeball",
    experience: int = 0,
) -> int:
    cur = conn.execute(
        "INSERT INTO captures "
        "(species, form, shiny, caught_at, ball_slug, level, experience) "
        "VALUES (?, ?, ?, ?, ?, 5, ?)",
        (species, form, int(shiny), caught_at, ball_slug, experience),
    )
    conn.commit()
    if cur.lastrowid is None:
        raise RuntimeError("capture insert did not return an id")
    return int(cur.lastrowid)


def get_capture(conn: sqlite3.Connection, capture_id: int) -> sqlite3.Row | None:
    return cast(
        sqlite3.Row | None,
        conn.execute("SELECT * FROM captures WHERE id = ?", (capture_id,)).fetchone(),
    )


def list_captures(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return cast(
        list[sqlite3.Row],
        conn.execute("SELECT * FROM captures ORDER BY caught_at DESC").fetchall(),
    )


def set_team(conn: sqlite3.Connection, capture_id: int, in_team: bool) -> None:
    conn.execute("UPDATE captures SET in_team = ? WHERE id = ?", (int(in_team), capture_id))
    conn.commit()


def count_team(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM captures WHERE in_team = 1").fetchone()
    return int(row["n"])


def get_pending_evolution(conn: sqlite3.Connection) -> sqlite3.Row | None:
    rows = list_pending_evolutions(conn)
    return rows[0] if rows else None


def list_pending_evolutions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return cast(
        list[sqlite3.Row],
        conn.execute(
            "SELECT * FROM captures WHERE pending_evolution_species IS NOT NULL ORDER BY id"
        ).fetchall(),
    )


def complete_evolution(conn: sqlite3.Connection, capture_id: int) -> None:
    conn.execute(
        "UPDATE captures SET species = pending_evolution_species, "
        "form = COALESCE(pending_evolution_form, 'regular'), "
        "pending_evolution_species = NULL, pending_evolution_form = NULL "
        "WHERE id = ? AND pending_evolution_species IS NOT NULL",
        (capture_id,),
    )
    conn.commit()


def get_species_cache(conn: sqlite3.Connection, species: str, form: str) -> sqlite3.Row | None:
    return cast(
        sqlite3.Row | None,
        conn.execute(
            "SELECT * FROM species_cache WHERE species = ? AND form = ?", (species, form)
        ).fetchone(),
    )


def upsert_species_cache(
    conn: sqlite3.Connection,
    species: str,
    form: str,
    data: dict[str, Any],
    fetched_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO species_cache
            (species, form, pokedex_id, capture_rate, types, hp, atk, def, spa, spd, spe,
             is_legendary, is_mythical, generation, flavor_text, form_data_exact,
             growth_rate, base_experience, level_evolutions, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(species, form) DO UPDATE SET
            pokedex_id=excluded.pokedex_id, capture_rate=excluded.capture_rate,
            types=excluded.types,
            hp=excluded.hp, atk=excluded.atk, def=excluded.def,
            spa=excluded.spa, spd=excluded.spd, spe=excluded.spe,
            is_legendary=excluded.is_legendary, is_mythical=excluded.is_mythical,
            generation=excluded.generation, flavor_text=excluded.flavor_text,
            form_data_exact=excluded.form_data_exact, fetched_at=excluded.fetched_at
            , growth_rate=excluded.growth_rate,
            base_experience=excluded.base_experience,
            level_evolutions=excluded.level_evolutions
        """,
        (
            species,
            form,
            data.get("pokedex_id"),
            data.get("capture_rate"),
            json.dumps(data.get("types", [])),
            data.get("hp"),
            data.get("atk"),
            data.get("def"),
            data.get("spa"),
            data.get("spd"),
            data.get("spe"),
            int(data.get("is_legendary", False)),
            int(data.get("is_mythical", False)),
            data.get("generation"),
            data.get("flavor_text"),
            int(data.get("form_data_exact", True)),
            data.get("growth_rate"),
            data.get("base_experience"),
            json.dumps(data.get("level_evolutions", [])),
            fetched_at,
        ),
    )
    conn.commit()
