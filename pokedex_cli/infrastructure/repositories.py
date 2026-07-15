"""SQLite repositories for mutable game state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from pokedex_cli.application.evolutions import PendingEvolution
from pokedex_cli.application.training import TeamMember
from pokedex_cli.domain.evolutions import EvolutionOption
from pokedex_cli.domain.models import Encounter as EncounterModel
from pokedex_cli.infrastructure import database

Inventory = dict[str, Any]
InventoryNormaliser = Callable[[object | None], Inventory]
InventoryOperation = Callable[[Inventory], None]
Encounter = dict[str, Any]
EncounterOperation = Callable[[Encounter | None], None]


class SQLiteInventoryRepository:
    """Persist inventory and activity in one explicit SQLite transaction."""

    def __init__(self, database_path: Path, legacy_json_path: Path) -> None:
        self.database_path = database_path
        self.legacy_json_path = legacy_json_path

    def update(
        self,
        normalise: InventoryNormaliser,
        operation: InventoryOperation,
    ) -> Inventory:
        with self.transaction(normalise) as inventory:
            operation(inventory)
        return inventory

    @contextmanager
    def transaction(self, normalise: InventoryNormaliser) -> Iterator[Inventory]:
        connection = database.connect(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            inventory = self.load_in_transaction(connection, normalise)
            yield inventory
            self.save_in_transaction(connection, inventory)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load_in_transaction(
        self, connection: sqlite3.Connection, normalise: InventoryNormaliser
    ) -> Inventory:
        raw: object | None = self._load_sqlite(connection)
        if raw is None:
            raw = self._read_legacy_json()
            connection.execute(
                "INSERT OR IGNORE INTO legacy_imports (kind, source_path) VALUES ('inventory', ?)",
                (str(self.legacy_json_path),),
            )
        return normalise(raw)

    def save_in_transaction(self, connection: sqlite3.Connection, inventory: Inventory) -> None:
        self._save_sqlite(connection, inventory)

    def _read_legacy_json(self) -> object | None:
        try:
            return cast(object, json.loads(self.legacy_json_path.read_text()))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def _load_sqlite(connection: sqlite3.Connection) -> Inventory | None:
        activity = connection.execute("SELECT * FROM activity_state WHERE singleton = 1").fetchone()
        if activity is None:
            return None
        balls = {
            str(row["slug"]): int(row["stock"])
            for row in connection.execute("SELECT slug, stock FROM inventory_balls")
        }
        commits = [
            str(row["oid"])
            for row in connection.execute("SELECT oid FROM processed_commits ORDER BY sequence")
        ]
        try:
            repositories = json.loads(activity["repositories_json"])
        except (TypeError, json.JSONDecodeError):
            repositories = []
        return {
            "version": 1,
            "balls": balls,
            "activity": {
                "started_at": activity["started_at"],
                "last_passive_at": activity["last_passive_at"],
                "last_synced_at": activity["last_synced_at"],
                "last_repo_scan_at": activity["last_repo_scan_at"],
                "repositories": repositories,
                "processed_commits": commits,
                "work_commits": int(activity["work_commits"]),
            },
        }

    @staticmethod
    def _save_sqlite(connection: sqlite3.Connection, inventory: Inventory) -> None:
        activity = inventory["activity"]
        connection.execute("DELETE FROM inventory_balls")
        for slug, stock in inventory["balls"].items():
            connection.execute(
                "INSERT INTO inventory_balls (slug, stock) VALUES (?, ?)",
                (slug, int(stock)),
            )
        connection.execute(
            """
            INSERT INTO activity_state (
                singleton, started_at, last_passive_at, last_synced_at,
                last_repo_scan_at, repositories_json, work_commits
            ) VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(singleton) DO UPDATE SET
                started_at = excluded.started_at,
                last_passive_at = excluded.last_passive_at,
                last_synced_at = excluded.last_synced_at,
                last_repo_scan_at = excluded.last_repo_scan_at,
                repositories_json = excluded.repositories_json,
                work_commits = excluded.work_commits
            """,
            (
                activity["started_at"],
                activity["last_passive_at"],
                activity["last_synced_at"],
                activity.get("last_repo_scan_at"),
                json.dumps(activity.get("repositories", [])),
                int(activity.get("work_commits", 0)),
            ),
        )
        connection.execute("DELETE FROM processed_commits")
        for sequence, oid in enumerate(activity.get("processed_commits", [])):
            connection.execute(
                "INSERT OR IGNORE INTO processed_commits (oid, sequence) VALUES (?, ?)",
                (str(oid), sequence),
            )


class SQLiteEncounterRepository:
    """Persist the current encounter and import ``last_seen.json`` once."""

    def __init__(self, database_path: Path, legacy_json_path: Path) -> None:
        self.database_path = database_path
        self.legacy_json_path = legacy_json_path

    def read(self) -> Encounter | None:
        return self.update(lambda state: None)

    def write(self, encounter: Encounter) -> None:
        state = self._normalise(encounter)
        if state is None:
            raise ValueError("invalid encounter")
        connection = database.connect(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._save(connection, state)
            self._mark_imported(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update(self, operation: EncounterOperation) -> Encounter | None:
        connection = database.connect(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            state = self.load_in_transaction(connection)
            operation(state)
            if state is not None:
                self.save_in_transaction(connection, state)
            connection.commit()
            return state
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load_in_transaction(self, connection: sqlite3.Connection) -> Encounter | None:
        state = self._load(connection)
        if state is None and not self._was_imported(connection):
            state = self._read_legacy()
            self._mark_imported(connection)
        return state

    def save_in_transaction(self, connection: sqlite3.Connection, state: Encounter) -> None:
        self._save(connection, state)

    @staticmethod
    def clear_in_transaction(connection: sqlite3.Connection) -> None:
        connection.execute("DELETE FROM encounter_state")

    def clear(self) -> None:
        connection = database.connect(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM encounter_state")
            self._mark_imported(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _read_legacy(self) -> Encounter | None:
        try:
            raw = json.loads(self.legacy_json_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return self._normalise(raw)

    @staticmethod
    def _normalise(raw: object) -> Encounter | None:
        model = EncounterModel.from_dict(raw)
        return model.to_dict() if model is not None else None

    @staticmethod
    def _load(connection: sqlite3.Connection) -> Encounter | None:
        row = connection.execute("SELECT * FROM encounter_state WHERE singleton = 1").fetchone()
        if row is None:
            return None
        return {
            "species": row["species"],
            "form": row["form"],
            "shiny": bool(row["shiny"]),
            "seen_at": row["seen_at"],
            "captured": bool(row["captured"]),
            "failed_capture_attempts": int(row["failed_capture_attempts"]),
            "escape_after_attempts": row["escape_after_attempts"],
        }

    @staticmethod
    def _save(connection: sqlite3.Connection, state: Encounter) -> None:
        connection.execute(
            """
            INSERT INTO encounter_state (
                singleton, species, form, shiny, seen_at, captured,
                failed_capture_attempts, escape_after_attempts
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(singleton) DO UPDATE SET
                species = excluded.species,
                form = excluded.form,
                shiny = excluded.shiny,
                seen_at = excluded.seen_at,
                captured = excluded.captured,
                failed_capture_attempts = excluded.failed_capture_attempts,
                escape_after_attempts = excluded.escape_after_attempts
            """,
            (
                state["species"],
                state["form"],
                int(bool(state["shiny"])),
                state["seen_at"],
                int(bool(state["captured"])),
                int(state["failed_capture_attempts"]),
                state["escape_after_attempts"],
            ),
        )

    @staticmethod
    def _was_imported(connection: sqlite3.Connection) -> bool:
        return (
            connection.execute("SELECT 1 FROM legacy_imports WHERE kind = 'encounter'").fetchone()
            is not None
        )

    def _mark_imported(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO legacy_imports (kind, source_path) VALUES ('encounter', ?)",
            (str(self.legacy_json_path),),
        )


class SQLiteCaptureRepository:
    """Store captured Pokémon without owning the surrounding transaction."""

    def insert(
        self,
        connection: sqlite3.Connection,
        *,
        species: str,
        form: str,
        shiny: bool,
        caught_at: str,
        ball_slug: str,
        experience: int,
    ) -> int:
        cursor = connection.execute(
            "INSERT INTO captures "
            "(species, form, shiny, caught_at, ball_slug, level, experience) "
            "VALUES (?, ?, ?, ?, ?, 5, ?)",
            (species, form, int(shiny), caught_at, ball_slug, experience),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("capture insert did not return an id")
        return int(cursor.lastrowid)


class SQLiteSpeciesCacheRepository:
    """Own short-lived connections for cache-aside species enrichment."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def get(self, species: str, form: str) -> dict[str, Any] | None:
        connection = database.connect(self.database_path)
        try:
            row = connection.execute(
                "SELECT * FROM species_cache WHERE species = ? AND form = ?",
                (species, form),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            connection.close()

    def put(
        self,
        species: str,
        form: str,
        data: dict[str, Any],
        fetched_at: str,
    ) -> None:
        connection = database.connect(self.database_path)
        try:
            connection.execute(
                """
                INSERT INTO species_cache
                    (species, form, pokedex_id, capture_rate, types,
                     hp, atk, def, spa, spd, spe, is_legendary, is_mythical,
                     generation, flavor_text, form_data_exact, growth_rate,
                     base_experience, level_evolutions, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(species, form) DO UPDATE SET
                    pokedex_id=excluded.pokedex_id,
                    capture_rate=excluded.capture_rate,
                    types=excluded.types,
                    hp=excluded.hp,
                    atk=excluded.atk,
                    def=excluded.def,
                    spa=excluded.spa,
                    spd=excluded.spd,
                    spe=excluded.spe,
                    is_legendary=excluded.is_legendary,
                    is_mythical=excluded.is_mythical,
                    generation=excluded.generation,
                    flavor_text=excluded.flavor_text,
                    form_data_exact=excluded.form_data_exact,
                    growth_rate=excluded.growth_rate,
                    base_experience=excluded.base_experience,
                    level_evolutions=excluded.level_evolutions,
                    fetched_at=excluded.fetched_at
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
                    int(bool(data.get("is_legendary"))),
                    int(bool(data.get("is_mythical"))),
                    data.get("generation"),
                    data.get("flavor_text"),
                    int(bool(data.get("form_data_exact", True))),
                    data.get("growth_rate"),
                    data.get("base_experience"),
                    json.dumps(data.get("level_evolutions", [])),
                    fetched_at,
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class SQLiteCollectionRepository:
    """Return capture rows already joined and decoded for presentation."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def list_captures(self) -> list[dict[str, Any]]:
        connection = database.connect(self.database_path)
        try:
            rows = connection.execute(
                """
                SELECT captures.*,
                       species_cache.species AS cache_species,
                       species_cache.pokedex_id,
                       species_cache.capture_rate,
                       species_cache.types,
                       species_cache.hp,
                       species_cache.atk,
                       species_cache.def,
                       species_cache.spa,
                       species_cache.spd,
                       species_cache.spe,
                       species_cache.is_legendary,
                       species_cache.is_mythical,
                       species_cache.generation,
                       species_cache.flavor_text,
                       species_cache.form_data_exact,
                       species_cache.growth_rate,
                       species_cache.base_experience
                FROM captures
                LEFT JOIN species_cache
                  ON species_cache.species = captures.species
                 AND species_cache.form = captures.form
                ORDER BY captures.caught_at DESC
                """
            ).fetchall()
            return [self._decode(row) for row in rows]
        finally:
            connection.close()

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        if result.pop("cache_species") is None:
            result.update(
                types=None,
                is_legendary=0,
                is_mythical=0,
                pokedex_id=None,
                generation=None,
                flavor_text=None,
                capture_rate=None,
                form_data_exact=1,
                growth_rate="medium",
                base_experience=64,
            )
            return result
        try:
            types = json.loads(result["types"] or "[]")
        except (TypeError, json.JSONDecodeError):
            types = []
        result["types"] = types if isinstance(types, list) else []
        result["growth_rate"] = result["growth_rate"] or "medium"
        result["base_experience"] = result["base_experience"] or 64
        return result


class SQLiteTrainingRepository:
    """Own connections used by the legacy progression persistence facade."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def members_missing_progression(self) -> tuple[TeamMember, ...]:
        connection = database.connect(self.database_path)
        try:
            rows = connection.execute(
                """
                SELECT captures.species, captures.form
                FROM captures
                LEFT JOIN species_cache
                  ON species_cache.species = captures.species
                 AND species_cache.form = captures.form
                WHERE captures.in_team = 1
                  AND (species_cache.species IS NULL OR species_cache.growth_rate IS NULL)
                ORDER BY captures.id
                """
            ).fetchall()
            return tuple(TeamMember(str(row["species"]), str(row["form"])) for row in rows)
        finally:
            connection.close()

    def apply(self, workload: int | Sequence[object]) -> tuple[Any, ...]:
        from pokedex_cli.infrastructure import training

        connection = database.connect(self.database_path)
        try:
            return training.apply_commit_experience(connection, workload)
        finally:
            connection.close()


class SQLiteTeamRepository:
    """Team membership operations scoped to a caller-owned transaction."""

    @staticmethod
    def membership(connection: sqlite3.Connection, capture_id: int) -> bool | None:
        row = connection.execute(
            "SELECT in_team FROM captures WHERE id = ?", (capture_id,)
        ).fetchone()
        return None if row is None else bool(row["in_team"])

    @staticmethod
    def count(connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT COUNT(*) FROM captures WHERE in_team = 1").fetchone()
        return int(row[0])

    @staticmethod
    def set_membership(connection: sqlite3.Connection, capture_id: int, in_team: bool) -> None:
        connection.execute(
            "UPDATE captures SET in_team = ? WHERE id = ?",
            (int(in_team), capture_id),
        )


class SQLiteEvolutionRepository:
    """Evolution persistence operations inside a caller-owned transaction."""

    @staticmethod
    def pending(
        connection: sqlite3.Connection,
        capture_ids: list[int] | tuple[int, ...] | None = None,
    ) -> list[PendingEvolution]:
        query = "SELECT * FROM captures WHERE pending_evolution_species IS NOT NULL"
        parameters: tuple[Any, ...] = ()
        if capture_ids is not None:
            if not capture_ids:
                return []
            placeholders = ",".join("?" for _ in capture_ids)
            query += f" AND id IN ({placeholders})"
            parameters = tuple(capture_ids)
        query += " ORDER BY id"
        return [
            PendingEvolution(
                capture_id=int(row["id"]),
                species=str(row["species"]),
                form=str(row["form"]),
                target_species=str(row["pending_evolution_species"]),
                target_form=str(row["pending_evolution_form"] or "regular"),
                shiny=bool(row["shiny"]),
                level=int(row["level"]),
            )
            for row in connection.execute(query, parameters)
        ]

    @staticmethod
    def complete(connection: sqlite3.Connection, evolution: PendingEvolution) -> None:
        connection.execute(
            "UPDATE captures SET species = ?, form = ?, "
            "pending_evolution_species = NULL, pending_evolution_form = NULL "
            "WHERE id = ? AND pending_evolution_species IS NOT NULL",
            (
                evolution.target_species,
                evolution.target_form,
                evolution.capture_id,
            ),
        )

    def options(
        self, connection: sqlite3.Connection, species: str, form: str
    ) -> list[EvolutionOption]:
        row = connection.execute(
            "SELECT level_evolutions FROM species_cache WHERE species = ? AND form = ?",
            (species, form),
        ).fetchone()
        return self.decode_options(row[0] if row is not None else None)

    @staticmethod
    def decode_options(payload: object) -> list[EvolutionOption]:
        try:
            raw = json.loads(str(payload or "[]"))
        except json.JSONDecodeError:
            return []
        if not isinstance(raw, list):
            return []
        options: list[EvolutionOption] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                species = str(entry["species"])
                min_level = int(entry["min_level"])
            except (KeyError, TypeError, ValueError):
                continue
            if species:
                options.append(
                    EvolutionOption(
                        species,
                        str(entry.get("form") or "regular"),
                        min_level,
                    )
                )
        return options

    @staticmethod
    def queue(
        connection: sqlite3.Connection,
        capture_id: int,
        option: EvolutionOption,
    ) -> None:
        connection.execute(
            "UPDATE captures SET pending_evolution_species = ?, "
            "pending_evolution_form = ? WHERE id = ?",
            (option.species, option.form, capture_id),
        )
