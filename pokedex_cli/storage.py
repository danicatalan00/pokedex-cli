import json
import sqlite3

from pokedex_cli.paths import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    species TEXT NOT NULL,
    form TEXT NOT NULL DEFAULT 'regular',
    shiny INTEGER NOT NULL DEFAULT 0,
    caught_at TEXT NOT NULL,
    nickname TEXT,
    in_team INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_captures_species ON captures(species);

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
);
"""


def get_connection() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_capture(conn, species: str, form: str, shiny: bool, caught_at: str) -> int:
    cur = conn.execute(
        "INSERT INTO captures (species, form, shiny, caught_at) VALUES (?, ?, ?, ?)",
        (species, form, int(shiny), caught_at),
    )
    conn.commit()
    return cur.lastrowid


def get_capture(conn, capture_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM captures WHERE id = ?", (capture_id,)).fetchone()


def list_captures(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM captures ORDER BY caught_at DESC").fetchall()


def set_team(conn, capture_id: int, in_team: bool) -> None:
    conn.execute("UPDATE captures SET in_team = ? WHERE id = ?", (int(in_team), capture_id))
    conn.commit()


def count_team(conn) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM captures WHERE in_team = 1").fetchone()
    return row["n"]


def get_species_cache(conn, species: str, form: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM species_cache WHERE species = ? AND form = ?", (species, form)
    ).fetchone()


def upsert_species_cache(conn, species: str, form: str, data: dict, fetched_at: str) -> None:
    conn.execute(
        """
        INSERT INTO species_cache
            (species, form, pokedex_id, types, hp, atk, def, spa, spd, spe,
             is_legendary, is_mythical, generation, flavor_text, form_data_exact, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(species, form) DO UPDATE SET
            pokedex_id=excluded.pokedex_id, types=excluded.types,
            hp=excluded.hp, atk=excluded.atk, def=excluded.def,
            spa=excluded.spa, spd=excluded.spd, spe=excluded.spe,
            is_legendary=excluded.is_legendary, is_mythical=excluded.is_mythical,
            generation=excluded.generation, flavor_text=excluded.flavor_text,
            form_data_exact=excluded.form_data_exact, fetched_at=excluded.fetched_at
        """,
        (
            species,
            form,
            data.get("pokedex_id"),
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
            fetched_at,
        ),
    )
    conn.commit()
