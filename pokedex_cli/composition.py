"""Application composition root: bind use cases to local adapters."""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from pokedex_cli.application import capture as capture_application
from pokedex_cli.application import collection as collection_application
from pokedex_cli.application import evolutions as evolution_application
from pokedex_cli.application import hook as hook_application
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
    SQLiteCaptureRepository,
    SQLiteCollectionRepository,
    SQLiteEncounterRepository,
    SQLiteEvolutionRepository,
    SQLiteInventoryRepository,
    SQLiteSpeciesCacheRepository,
    SQLiteTeamRepository,
    SQLiteTrainingRepository,
)


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


def manage_team() -> team_application.ManageTeam:
    return team_application.ManageTeam(
        connection_factory=lambda: database.connect(paths.DB_PATH),
        repository=SQLiteTeamRepository(),
    )


def collection_queries() -> collection_application.CollectionQueries:
    return collection_application.CollectionQueries(SQLiteCollectionRepository(paths.DB_PATH))


def species_data() -> species_application.GetSpeciesData:
    return species_application.GetSpeciesData(
        cache=SQLiteSpeciesCacheRepository(paths.DB_PATH),
        api=TolerantPokeApiClient(),
        clock=lambda: datetime.now(timezone.utc),
    )


def sprite_renderer() -> KrabbyClient:
    return KrabbyClient(pokemon_json_path=paths.KRABBY_POKEMON_JSON)


def process_evolutions() -> evolution_application.ProcessEvolutions:
    return evolution_application.ProcessEvolutions(
        connection_factory=lambda: database.connect(paths.DB_PATH),
        repository=SQLiteEvolutionRepository(),
        random_source=random,
    )


def open_terminal(
    write_last_seen: Callable[[str, str, bool], None],
) -> hook_application.OpenTerminal:
    return hook_application.OpenTerminal(
        evolutions=process_evolutions(),
        sync_activity=sync_training,
        start_wild_encounter=lambda generations: wild_encounters.run_hook(
            generations, write_last_seen
        ),
    )


def read_encounter() -> dict | None:
    return paths.read_last_seen()


def write_encounter(species: str, form: str, shiny: bool, seen_at: str) -> None:
    paths.write_last_seen(species, form, shiny, seen_at)


def clear_encounter() -> None:
    paths.clear_last_seen()


def pick_species_form_shiny(generations: str) -> tuple[str, str, bool]:
    return wild_encounters.pick_species_form_shiny(generations)


def run_wild_encounter(generations: str, write_last_seen: Callable[[str, str, bool], None]) -> None:
    wild_encounters.run_hook(generations, write_last_seen)


def completion_file(shell: str) -> Path:
    return paths.PROJECT_DIR / "completions" / f"_pokedex.{shell}"


def record_failure(context: str, error: BaseException) -> None:
    log_failure(context, error)
