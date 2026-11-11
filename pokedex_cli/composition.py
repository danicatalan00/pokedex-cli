"""Application composition root: bind use cases to local adapters."""

from __future__ import annotations

import random
import sqlite3
import threading
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, TypeVar

from pokedex_cli.application import battle as battle_application
from pokedex_cli.application import capture as capture_application
from pokedex_cli.application import collection as collection_application
from pokedex_cli.application import encounter as encounter_application
from pokedex_cli.application import evolutions as evolution_application
from pokedex_cli.application import hook as hook_application
from pokedex_cli.application import individuality as individuality_application
from pokedex_cli.application import moves as moves_application
from pokedex_cli.application import movesets as movesets_application
from pokedex_cli.application import pokedex_catalog as pokedex_catalog_application
from pokedex_cli.application import safari as safari_application
from pokedex_cli.application import species as species_application
from pokedex_cli.application import team as team_application
from pokedex_cli.application import training as training_application
from pokedex_cli.domain.models import Ball
from pokedex_cli.infrastructure import database, paths, wild_encounters
from pokedex_cli.infrastructure import legacy_inventory as inventory
from pokedex_cli.infrastructure.diagnostics import log_failure
from pokedex_cli.infrastructure.krabby import KrabbyClient
from pokedex_cli.infrastructure.pokeapi import TolerantPokeApiClient
from pokedex_cli.infrastructure.repositories import (
    SQLiteBattleProgressRepository,
    SQLiteBattleRecordRepository,
    SQLiteBattleRepository,
    SQLiteCaptureMovesRepository,
    SQLiteCaptureRepository,
    SQLiteCollectionRepository,
    SQLiteEncounterRepository,
    SQLiteEvolutionRepository,
    SQLiteIndividualityRepository,
    SQLiteInventoryRepository,
    SQLiteMoveCacheRepository,
    SQLiteSightingsRepository,
    SQLiteSpeciesCacheRepository,
    SQLiteTeamRepository,
    SQLiteTrainingRepository,
)

# Descargas de movimientos simultáneas al preparar un combate.
MOVE_FETCH_WORKERS = 8

_Item = TypeVar("_Item")
_Result = TypeVar("_Result")


def sync_training(*, force_repo_scan: bool = False):
    return training_application.SyncTraining(
        sync_activity=inventory.sync_activity,
        repository=SQLiteTrainingRepository(paths.DB_PATH),
        species=species_data(),
    ).execute(force_repo_scan=force_repo_scan)


def ball_catalog() -> Mapping[str, Ball]:
    return inventory.BALLS


def resolve_ball(value: str) -> Ball | None:
    return inventory.resolve_ball(value)


def stock_count(current_inventory: dict, slug: str) -> int | None:
    return inventory.stock_count(current_inventory, slug)


def commits_until_next(current_inventory: dict, slug: str) -> int | None:
    return inventory.commits_until_next(current_inventory, slug)


def capture_encounter() -> capture_application.CaptureEncounter:
    return capture_application.CaptureEncounter(
        connection_factory=lambda: database.connect(paths.DB_PATH),
        inventory_repository=SQLiteInventoryRepository(paths.DB_PATH, paths.INVENTORY_PATH),
        encounter_repository=SQLiteEncounterRepository(paths.DB_PATH, paths.LAST_SEEN_PATH),
        capture_repository=SQLiteCaptureRepository(),
        inventory_normaliser=lambda raw: inventory._normalise_inventory(
            raw, datetime.now(timezone.utc)
        ),
        random_source=random,
    )


def throw_safari_item() -> safari_application.ThrowSafariItem:
    return safari_application.ThrowSafariItem(
        connection_factory=lambda: database.connect(paths.DB_PATH),
        encounter_repository=SQLiteEncounterRepository(paths.DB_PATH, paths.LAST_SEEN_PATH),
        random_source=random,
    )


def manage_team() -> team_application.ManageTeam:
    return team_application.ManageTeam(
        connection_factory=lambda: database.connect(paths.DB_PATH),
        repository=SQLiteTeamRepository(),
    )


def collection_queries() -> collection_application.CollectionQueries:
    return collection_application.CollectionQueries(SQLiteCollectionRepository(paths.DB_PATH))


def backfill_individuality() -> individuality_application.BackfillIndividuality:
    return individuality_application.BackfillIndividuality(
        connection_factory=lambda: database.connect(paths.DB_PATH),
        repository=SQLiteIndividualityRepository(),
    )


def species_data() -> species_application.GetSpeciesData:
    return species_application.GetSpeciesData(
        cache=SQLiteSpeciesCacheRepository(paths.DB_PATH),
        api=TolerantPokeApiClient(),
        clock=lambda: datetime.now(timezone.utc),
    )


def refresh_species_data() -> species_application.RefreshSpeciesData:
    cache = SQLiteSpeciesCacheRepository(paths.DB_PATH)
    return species_application.RefreshSpeciesData(
        catalog=cache,
        species=species_application.GetSpeciesData(
            cache=cache,
            api=TolerantPokeApiClient(),
            clock=lambda: datetime.now(timezone.utc),
        ),
    )


def sprite_renderer() -> KrabbyClient:
    return KrabbyClient(pokemon_json_path=paths.KRABBY_POKEMON_JSON)


def pokedex_catalog() -> pokedex_catalog_application.PokedexCatalog:
    collection = SQLiteCollectionRepository(paths.DB_PATH)
    return pokedex_catalog_application.PokedexCatalog(
        krabby=sprite_renderer(),
        sightings=SQLiteSightingsRepository(paths.DB_PATH),
        captures=collection,
        species_cache=collection,
        dex_registry=collection,
    )


def process_evolutions() -> evolution_application.ProcessEvolutions:
    return evolution_application.ProcessEvolutions(
        connection_factory=lambda: database.connect(paths.DB_PATH),
        repository=SQLiteEvolutionRepository(),
        random_source=random,
    )


def open_terminal(
    write_last_seen: Callable[[str, str, bool], None],
) -> hook_application.OpenTerminal:
    species = species_data()
    return hook_application.OpenTerminal(
        evolutions=process_evolutions(),
        sync_activity=sync_training,
        start_wild_encounter=lambda generations: wild_encounters.run_hook(
            generations, write_last_seen
        ),
        prepare_evolution=lambda evolution: species.execute(
            evolution.target_species, evolution.target_form
        ),
        battle_in_progress=active_wild_battle_name,
    )


# --- Modo combate ---------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _connect() -> sqlite3.Connection:
    return database.connect(paths.DB_PATH)


class _PerThreadMoveApi:
    """Un cliente de PokeAPI por hilo: ``requests.Session`` no es seguro entre
    hilos y las descargas de movimientos van en paralelo."""

    def __init__(
        self, factory: Callable[[], TolerantPokeApiClient] = TolerantPokeApiClient
    ) -> None:
        self._factory = factory
        self._local = threading.local()

    def fetch_move_data(self, slug: str) -> dict[str, Any] | None:
        client = getattr(self._local, "client", None)
        if client is None:
            client = self._factory()
            self._local.client = client
        return client.fetch_move_data(slug)


def _parallel_map(
    function: Callable[[_Item], _Result], items: Sequence[_Item]
) -> Iterable[_Result]:
    """``map`` con hasta ``MOVE_FETCH_WORKERS`` hilos; conserva el orden."""
    if len(items) <= 1:
        return [function(item) for item in items]
    with ThreadPoolExecutor(max_workers=MOVE_FETCH_WORKERS) as executor:
        return list(executor.map(function, items))


class _BattleProgressRepository(SQLiteBattleProgressRepository):
    """Progreso de las capturas tras un turno: lectura y escritura en la
    transacción del turno.

    La lectura vive aquí porque el turno necesita la EXP actual (los commits
    pueden haber entrenado a la captura con el combate abierto) y el
    repositorio de infraestructura aún no la ofrece; su sitio natural es
    ``SQLiteBattleProgressRepository``.
    """

    @staticmethod
    def progress_in_transaction(
        connection: sqlite3.Connection, capture_id: int
    ) -> tuple[int, int] | None:
        row = connection.execute(
            "SELECT experience, level FROM captures WHERE id = ?", (capture_id,)
        ).fetchone()
        if row is None:
            return None
        return int(row[0] or 0), int(row[1] or 1)


def move_data() -> moves_application.GetMoveData:
    return moves_application.GetMoveData(
        cache=SQLiteMoveCacheRepository(paths.DB_PATH),
        api=_PerThreadMoveApi(),
        clock=_utc_now,
        mapper=_parallel_map,
    )


def prepare_battle(
    on_progress: Callable[[str], None] | None = None,
) -> battle_application.PrepareBattle:
    return battle_application.PrepareBattle(
        collection=collection_queries(),
        species=species_data(),
        moves=move_data(),
        movesets=SQLiteCaptureMovesRepository(paths.DB_PATH),
        pool=SQLiteSpeciesCacheRepository(paths.DB_PATH),
        rng=random,
        on_progress=on_progress,
    )


def start_battle() -> battle_application.StartBattle:
    return battle_application.StartBattle(
        connection_factory=_connect,
        battles=SQLiteBattleRepository(paths.DB_PATH),
        encounters=SQLiteEncounterRepository(paths.DB_PATH, paths.LAST_SEEN_PATH),
    )


def take_battle_turn() -> battle_application.TakeBattleTurn:
    return battle_application.TakeBattleTurn(
        connection_factory=_connect,
        battles=SQLiteBattleRepository(paths.DB_PATH),
        encounters=SQLiteEncounterRepository(paths.DB_PATH, paths.LAST_SEEN_PATH),
        capture=capture_encounter(),
        progress=_BattleProgressRepository(random),
        records=SQLiteBattleRecordRepository(paths.DB_PATH),
        rng=random,
    )


def describe_battle() -> battle_application.DescribeBattle:
    return battle_application.DescribeBattle(
        connection_factory=_connect,
        battles=SQLiteBattleRepository(paths.DB_PATH),
        encounters=SQLiteEncounterRepository(paths.DB_PATH, paths.LAST_SEEN_PATH),
    )


def active_wild_battle_name() -> str | None:
    """Nombre del salvaje de un combate vigente, o ``None``. Nunca lanza: lo
    consultan el hook y ``ver``."""
    try:
        return describe_battle().active_wild_name()
    except Exception:
        return None


def battle_record() -> dict[str, Any]:
    """Récord acumulado (ceros si aún no hay combates)."""
    return SQLiteBattleRecordRepository(paths.DB_PATH).read()


def manage_moveset() -> movesets_application.ManageMoveset:
    return movesets_application.ManageMoveset(
        captures=collection_queries(),
        species=species_data(),
        moves=move_data(),
        movesets=SQLiteCaptureMovesRepository(paths.DB_PATH),
    )


def exhibition_session(
    left: str | None = None,
    right: str | None = None,
    level: int | None = None,
    *,
    on_progress: Callable[[str], None] | None = None,
) -> battle_application.ExhibitionSession | battle_application.BattleStatus:
    """Exhibición en memoria ya abierta, o el estado que lo impide."""
    state = prepare_battle(on_progress).exhibition(left, right, level)
    if isinstance(state, battle_application.BattleStatus):
        return state
    return battle_application.ExhibitionSession(state, rng=random)


def persisted_session(
    view: battle_application.BattleView | None = None,
    *,
    opening: Sequence[Any] = (),
) -> battle_application.PersistedSession | None:
    """Sesión sobre el combate guardado (el de ``view`` o el vigente); ``None``
    si no hay ninguno. ``opening`` son los eventos de apertura de
    ``start_battle`` cuando el combate acaba de empezar."""
    current = view if view is not None else describe_battle().execute()
    if current is None:
        return None
    return battle_application.PersistedSession(
        turns=take_battle_turn(), view=current, clock=_utc_now, opening=opening
    )


def read_encounter() -> dict | None:
    return paths.read_last_seen()


def describe_encounter() -> encounter_application.DescribeEncounter:
    return encounter_application.DescribeEncounter(SQLiteCollectionRepository(paths.DB_PATH))


def write_encounter(species: str, form: str, shiny: bool, seen_at: str) -> None:
    paths.write_last_seen(species, form, shiny, seen_at)


def clear_encounter() -> None:
    paths.clear_last_seen()


def pick_species_form_shiny(generations: str) -> tuple[str, str, bool]:
    return wild_encounters.pick_species_form_shiny(generations)


def run_wild_encounter(generations: str, write_last_seen: Callable[[str, str, bool], None]) -> None:
    wild_encounters.run_hook(generations, write_last_seen)


def completion_file(shell: str) -> Path:
    return paths.completion_file(shell)


def record_failure(context: str, error: BaseException) -> None:
    log_failure(context, error)


def is_recoverable_failure(error: BaseException) -> bool:
    """Classify infrastructure failures without leaking its types into presentation."""
    return isinstance(error, (sqlite3.Error, OSError))
