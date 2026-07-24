"""SQLite repositories for mutable game state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pokedex_cli.application.evolutions import PendingEvolution
from pokedex_cli.application.individuality import IncompleteCapture
from pokedex_cli.application.species import SpeciesIdentity
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
            self._record_sighting(connection, state)
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
    def _record_sighting(connection: sqlite3.Connection, state: Encounter) -> None:
        """Upsert the sighting inside the SAME transaction as the encounter
        write: this is the only place a newly-painted wild Pokémon becomes
        visible to the national catalog (``pokedex`` with no arguments)."""
        connection.execute(
            """
            INSERT INTO sightings (species, form, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(species, form) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                times_seen = times_seen + 1
            """,
            (state["species"], state["form"], state["seen_at"], state["seen_at"]),
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
        level: int,
        experience: int,
        ivs: Mapping[str, int],
        nature: str,
        gender: str | None,
        ability: str | None,
    ) -> int:
        cursor = connection.execute(
            "INSERT INTO captures "
            "(species, form, shiny, caught_at, ball_slug, level, experience, "
            "iv_hp, iv_atk, iv_def, iv_spa, iv_spd, iv_spe, nature, gender, ability) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                species,
                form,
                int(shiny),
                caught_at,
                ball_slug,
                level,
                experience,
                ivs["hp"],
                ivs["atk"],
                ivs["def"],
                ivs["spa"],
                ivs["spd"],
                ivs["spe"],
                nature,
                gender,
                ability,
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("capture insert did not return an id")
        connection.execute(
            "INSERT OR IGNORE INTO dex_caught (species, form, first_caught_at) VALUES (?, ?, ?)",
            (species, form, caught_at),
        )
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
                     base_experience, encounter_level, level_evolutions, gender_rate, abilities,
                     height_dm, weight_hg, genus, habitat, color, shape, egg_groups,
                     base_happiness, hatch_counter, evolution_chain,
                     fetched_at)
                VALUES (:species, :form, :pokedex_id, :capture_rate, :types,
                        :hp, :atk, :def, :spa, :spd, :spe, :is_legendary, :is_mythical,
                        :generation, :flavor_text, :form_data_exact, :growth_rate,
                        :base_experience, :encounter_level, :level_evolutions,
                        :gender_rate, :abilities, :height_dm, :weight_hg, :genus,
                        :habitat, :color, :shape, :egg_groups, :base_happiness,
                        :hatch_counter, :evolution_chain, :fetched_at)
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
                    encounter_level=excluded.encounter_level,
                    level_evolutions=excluded.level_evolutions,
                    gender_rate=excluded.gender_rate,
                    abilities=excluded.abilities,
                    height_dm=excluded.height_dm,
                    weight_hg=excluded.weight_hg,
                    genus=excluded.genus,
                    habitat=excluded.habitat,
                    color=excluded.color,
                    shape=excluded.shape,
                    egg_groups=excluded.egg_groups,
                    base_happiness=excluded.base_happiness,
                    hatch_counter=excluded.hatch_counter,
                    evolution_chain=excluded.evolution_chain,
                    fetched_at=excluded.fetched_at
                """,
                {
                    "species": species,
                    "form": form,
                    "pokedex_id": data.get("pokedex_id"),
                    "capture_rate": data.get("capture_rate"),
                    "types": json.dumps(data.get("types", [])),
                    "hp": data.get("hp"),
                    "atk": data.get("atk"),
                    "def": data.get("def"),
                    "spa": data.get("spa"),
                    "spd": data.get("spd"),
                    "spe": data.get("spe"),
                    "is_legendary": int(bool(data.get("is_legendary"))),
                    "is_mythical": int(bool(data.get("is_mythical"))),
                    "generation": data.get("generation"),
                    "flavor_text": data.get("flavor_text"),
                    "form_data_exact": int(bool(data.get("form_data_exact", True))),
                    "growth_rate": data.get("growth_rate"),
                    "base_experience": data.get("base_experience"),
                    "encounter_level": data.get("encounter_level", 5),
                    "level_evolutions": json.dumps(data.get("level_evolutions", [])),
                    "gender_rate": data.get("gender_rate"),
                    "abilities": json.dumps(data.get("abilities", [])),
                    "height_dm": data.get("height_dm"),
                    "weight_hg": data.get("weight_hg"),
                    "genus": data.get("genus"),
                    "habitat": data.get("habitat"),
                    "color": data.get("color"),
                    "shape": data.get("shape"),
                    "egg_groups": json.dumps(data.get("egg_groups", [])),
                    "base_happiness": data.get("base_happiness"),
                    "hatch_counter": data.get("hatch_counter"),
                    "evolution_chain": json.dumps(data.get("evolution_chain", [])),
                    "fetched_at": fetched_at,
                },
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def captured(self) -> tuple[SpeciesIdentity, ...]:
        connection = database.connect(self.database_path)
        try:
            rows = connection.execute(
                "SELECT species, form FROM captures "
                "UNION SELECT species, form FROM dex_caught "
                "ORDER BY species, form"
            ).fetchall()
            return tuple(SpeciesIdentity(str(row["species"]), str(row["form"])) for row in rows)
        finally:
            connection.close()

    def clear(self) -> None:
        connection = database.connect(self.database_path)
        try:
            connection.execute("DELETE FROM species_cache")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


@dataclass(frozen=True)
class SightingAggregate:
    first_seen_at: str
    times_seen: int


class SQLiteSightingsRepository:
    """Read-only species-level aggregate over ``sightings``, used by the
    national Pokédex catalog (``PokedexCatalog``). Sightings themselves are
    written by :class:`SQLiteEncounterRepository`, never here."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def aggregated(self) -> dict[str, SightingAggregate]:
        connection = database.connect(self.database_path)
        try:
            rows = connection.execute(
                "SELECT species, MIN(first_seen_at) AS first_seen_at, "
                "SUM(times_seen) AS times_seen FROM sightings GROUP BY species"
            ).fetchall()
            return {
                str(row["species"]): SightingAggregate(
                    first_seen_at=str(row["first_seen_at"]),
                    times_seen=int(row["times_seen"]),
                )
                for row in rows
            }
        finally:
            connection.close()


@dataclass(frozen=True)
class CaptureAggregate:
    captures_count: int
    max_level: int
    any_shiny: bool


class SQLiteCollectionRepository:
    """Return capture rows already joined and decoded for presentation.

    Also owns the species-level aggregates the national Pokédex catalog
    needs from ``captures``/``species_cache`` (chosen over a third
    repository class since both already read those two tables for the
    exact same "one row per species" shape).
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def captures_aggregated(self) -> dict[str, CaptureAggregate]:
        connection = database.connect(self.database_path)
        try:
            rows = connection.execute(
                "SELECT species, COUNT(*) AS captures_count, MAX(level) AS max_level, "
                "MAX(shiny) AS any_shiny FROM captures GROUP BY species"
            ).fetchall()
            return {
                str(row["species"]): CaptureAggregate(
                    captures_count=int(row["captures_count"]),
                    max_level=int(row["max_level"]),
                    any_shiny=bool(row["any_shiny"]),
                )
                for row in rows
            }
        finally:
            connection.close()

    def dex_caught_species(self) -> set[str]:
        """Especies registradas como capturadas en la Pokédex, aunque la
        captura haya evolucionado después (tabla ``dex_caught``)."""
        connection = database.connect(self.database_path)
        try:
            rows = connection.execute("SELECT DISTINCT species FROM dex_caught").fetchall()
            return {str(row["species"]) for row in rows}
        finally:
            connection.close()

    def is_species_captured(self, species: str) -> bool:
        """Species-level Pokédex answer: registered as captured under any form
        (``captures`` or the sticky ``dex_caught`` registry)."""
        connection = database.connect(self.database_path)
        try:
            row = connection.execute(
                "SELECT 1 FROM captures WHERE species = ? "
                "UNION SELECT 1 FROM dex_caught WHERE species = ? "
                "LIMIT 1",
                (species, species),
            ).fetchone()
            return row is not None
        finally:
            connection.close()

    def is_variant_captured(self, species: str, form: str, shiny: bool) -> bool:
        """Exact-variant Pokédex answer for shiny/regional/mega encounters. A
        shiny only counts against a shiny capture (``dex_caught`` does not track
        shininess); a non-shiny alt form counts against either registry."""
        connection = database.connect(self.database_path)
        try:
            if shiny:
                row = connection.execute(
                    "SELECT 1 FROM captures WHERE species = ? AND form = ? AND shiny = 1 LIMIT 1",
                    (species, form),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT 1 FROM captures WHERE species = ? AND form = ? "
                    "UNION SELECT 1 FROM dex_caught WHERE species = ? AND form = ? "
                    "LIMIT 1",
                    (species, form, species, form),
                ).fetchone()
            return row is not None
        finally:
            connection.close()

    def species_cache_by_species(self) -> dict[str, dict[str, Any]]:
        """One row per species (ignoring form, as the catalog only tracks
        species-level state), preferring the 'regular' form's cache."""
        connection = database.connect(self.database_path)
        try:
            rows = connection.execute(
                "SELECT species, form, types, is_legendary, is_mythical, flavor_text, "
                "hp, atk, def, spa, spd, spe, level_evolutions, evolution_chain, "
                "height_dm, weight_hg, genus, habitat, color, shape, egg_groups, "
                "growth_rate, base_experience, capture_rate, base_happiness, hatch_counter, "
                "abilities "
                "FROM species_cache ORDER BY species, (form != 'regular'), form"
            ).fetchall()
            result: dict[str, dict[str, Any]] = {}
            for row in rows:
                species = str(row["species"])
                if species in result:
                    continue
                result[species] = {
                    "types": _decode_json_string_list(row["types"]),
                    "is_legendary": bool(row["is_legendary"]),
                    "is_mythical": bool(row["is_mythical"]),
                    "flavor_text": row["flavor_text"],
                    "hp": row["hp"],
                    "atk": row["atk"],
                    "def": row["def"],
                    "spa": row["spa"],
                    "spd": row["spd"],
                    "spe": row["spe"],
                    "level_evolutions": row["level_evolutions"],
                    "evolution_chain": _decode_json_string_list(row["evolution_chain"]),
                    "height_dm": row["height_dm"],
                    "weight_hg": row["weight_hg"],
                    "genus": row["genus"],
                    "habitat": row["habitat"],
                    "color": row["color"],
                    "shape": row["shape"],
                    "egg_groups": _decode_json_string_list(row["egg_groups"]),
                    "growth_rate": row["growth_rate"],
                    "base_experience": row["base_experience"],
                    "capture_rate": row["capture_rate"],
                    "base_happiness": row["base_happiness"],
                    "hatch_counter": row["hatch_counter"],
                    "abilities": _decode_json_string_list(row["abilities"]),
                }
            return result
        finally:
            connection.close()

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
                       species_cache.base_experience,
                       species_cache.gender_rate,
                       species_cache.abilities
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
                gender_rate=None,
                abilities=None,
            )
            return result
        try:
            types = json.loads(result["types"] or "[]")
        except (TypeError, json.JSONDecodeError):
            types = []
        result["types"] = types if isinstance(types, list) else []
        result["growth_rate"] = result["growth_rate"] or "medium"
        result["base_experience"] = result["base_experience"] or 64
        result["abilities"] = _decode_json_string_list(result["abilities"])
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
        # The ability is re-derived for the evolved species (same personality
        # parity rule as the Gen 3 slot); gender never changes and is left
        # untouched.
        connection.execute(
            "UPDATE captures SET species = ?, form = ?, ability = NULL, "
            "pending_evolution_species = NULL, pending_evolution_form = NULL "
            "WHERE id = ? AND pending_evolution_species IS NOT NULL",
            (
                evolution.target_species,
                evolution.target_form,
                evolution.capture_id,
            ),
        )
        # Como en el juego: evolucionar registra la especie evolucionada como
        # capturada en la Pokédex. CURRENT_TIMESTAMP es suficiente aquí: la
        # fecha del registro solo es informativa y no hay reloj inyectado en
        # esta operación de transacción ajena.
        connection.execute(
            "INSERT OR IGNORE INTO dex_caught (species, form, first_caught_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP)",
            (evolution.target_species, evolution.target_form),
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


def _decode_json_string_list(payload: object) -> list[str]:
    try:
        parsed = json.loads(str(payload) if payload is not None else "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


class SQLiteIndividualityRepository:
    """Select captures missing individuality data and patch only what's missing."""

    @staticmethod
    def select_incomplete(connection: sqlite3.Connection) -> list[IncompleteCapture]:
        rows = connection.execute(
            """
            SELECT captures.id AS id,
                   captures.caught_at AS caught_at,
                   captures.iv_hp AS iv_hp,
                   captures.gender AS gender,
                   captures.ability AS ability,
                   species_cache.gender_rate AS gender_rate,
                   species_cache.abilities AS abilities
            FROM captures
            LEFT JOIN species_cache
              ON species_cache.species = captures.species
             AND species_cache.form = captures.form
            WHERE captures.iv_hp IS NULL
               OR (captures.gender IS NULL AND species_cache.gender_rate IS NOT NULL)
               OR (captures.ability IS NULL AND species_cache.abilities IS NOT NULL)
            """
        ).fetchall()
        pending: list[IncompleteCapture] = []
        for row in rows:
            abilities = _decode_json_string_list(row["abilities"])
            needs_ivs = row["iv_hp"] is None
            needs_gender = row["gender"] is None and row["gender_rate"] is not None
            needs_ability = row["ability"] is None and bool(abilities)
            if not (needs_ivs or needs_gender or needs_ability):
                continue
            pending.append(
                IncompleteCapture(
                    id=int(row["id"]),
                    caught_at=str(row["caught_at"]),
                    needs_ivs=needs_ivs,
                    needs_gender=needs_gender,
                    needs_ability=needs_ability,
                    gender_rate=row["gender_rate"],
                    abilities=abilities,
                )
            )
        return pending

    @staticmethod
    def update_ivs_and_nature(
        connection: sqlite3.Connection,
        capture_id: int,
        ivs: Mapping[str, int],
        nature: str,
    ) -> None:
        connection.execute(
            "UPDATE captures SET iv_hp = ?, iv_atk = ?, iv_def = ?, iv_spa = ?, "
            "iv_spd = ?, iv_spe = ?, nature = ? WHERE id = ?",
            (
                ivs["hp"],
                ivs["atk"],
                ivs["def"],
                ivs["spa"],
                ivs["spd"],
                ivs["spe"],
                nature,
                capture_id,
            ),
        )

    @staticmethod
    def update_gender(connection: sqlite3.Connection, capture_id: int, gender: str) -> None:
        connection.execute("UPDATE captures SET gender = ? WHERE id = ?", (gender, capture_id))

    @staticmethod
    def update_ability(connection: sqlite3.Connection, capture_id: int, ability: str) -> None:
        connection.execute("UPDATE captures SET ability = ? WHERE id = ?", (ability, capture_id))
