"""Inventario de Pokeballs y recompensas por actividad local.

La Pokeball normal es deliberadamente infinita: el inventario añade decisiones
interesantes a la captura, pero nunca bloquea el bucle principal del programa.
Las Pokeballs especiales se reponen con el paso del tiempo y con commits propios
hechos en horario laboral en repositorios Git bajo HOME.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from pokedex_cli.application import activity as activity_application
from pokedex_cli.domain import rewards as reward_rules
from pokedex_cli.domain.models import Ball as Ball
from pokedex_cli.domain.models import WorkCommit as WorkCommit
from pokedex_cli.infrastructure import paths
from pokedex_cli.infrastructure.git_activity import GitActivitySource
from pokedex_cli.infrastructure.repositories import SQLiteInventoryRepository

BALLS = {
    "pokeball": Ball("pokeball", "Pokeball", 1.0, None),
    "superball": Ball("superball", "Superball", 1.5, 10),
    "ultraball": Ball("ultraball", "Ultraball", 2.0, 5),
    "masterball": Ball("masterball", "Masterball", 255.0, 1),
}

# Slugs antiguos (familia "-bola") que pueden vivir en inventarios/BD guardados.
# Se remapean al leer para no perder stock ni capturas antiguas.
LEGACY_BALL_SLUGS = {
    "pokebola": "pokeball",
    "superbola": "superball",
    "ultrabola": "ultraball",
    "masterbola": "masterball",
}

BALL_ALIASES = {
    "poke": "pokeball",
    "normal": "pokeball",
    "super": "superball",
    "great": "superball",
    "ultra": "ultraball",
    "master": "masterball",
    **LEGACY_BALL_SLUGS,
}

INITIAL_STOCK = {"superball": 3, "ultraball": 1, "masterball": 0}
PASSIVE_INTERVAL = timedelta(hours=24)
REPO_SCAN_INTERVAL = timedelta(hours=6)
WORKDAY_START_HOUR = 8
WORKDAY_END_HOUR = 19
COMMIT_REWARD_EVERY = {"superball": 3, "ultraball": 10, "masterball": 50}
MAX_REMEMBERED_COMMITS = 2000

_SKIP_DIRS = {
    ".cache",
    ".cargo",
    ".local",
    ".npm",
    ".rustup",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "Library",
    "snap",
}


Reward = activity_application.Reward
SyncResult = activity_application.SyncActivityResult
Inventory = dict[str, Any]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str | None, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(value or "")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return fallback


def _new_inventory(now: datetime) -> Inventory:
    timestamp = _iso(now)
    return {
        "version": 1,
        "balls": dict(INITIAL_STOCK),
        "activity": {
            "started_at": timestamp,
            "last_passive_at": timestamp,
            "last_synced_at": timestamp,
            "last_repo_scan_at": None,
            "repositories": [],
            "processed_commits": [],
            "work_commits": 0,
        },
    }


def _normalise_inventory(data: object, now: datetime) -> Inventory:
    """Tolera ficheros incompletos y el borrador antiguo ``{slug: count}``."""
    base = _new_inventory(now)
    if not isinstance(data, dict):
        return base

    if isinstance(data.get("balls"), dict):
        source_balls = data["balls"]
    else:
        source_balls = data
    # Migra slugs antiguos ("-bola") de inventarios guardados a la familia "-ball".
    source_balls = {
        LEGACY_BALL_SLUGS.get(slug, slug): count
        for slug, count in source_balls.items()
        if isinstance(slug, str)
    }
    for slug in INITIAL_STOCK:
        try:
            base["balls"][slug] = max(0, int(source_balls.get(slug, INITIAL_STOCK[slug])))
        except (TypeError, ValueError):
            pass
        maximum = BALLS[slug].max_stock
        base["balls"][slug] = min(base["balls"][slug], maximum)

    activity = data.get("activity")
    if isinstance(activity, dict):
        for key in base["activity"]:
            if key in activity:
                base["activity"][key] = activity[key]
    try:
        base["activity"]["work_commits"] = max(0, int(base["activity"].get("work_commits") or 0))
    except (TypeError, ValueError):
        base["activity"]["work_commits"] = 0
    base["activity"]["repositories"] = [
        str(repo) for repo in base["activity"].get("repositories", []) if isinstance(repo, str)
    ]
    base["activity"]["processed_commits"] = [
        str(commit)
        for commit in base["activity"].get("processed_commits", [])
        if isinstance(commit, str)
    ][-MAX_REMEMBERED_COMMITS:]
    return base


def _read_inventory(inventory_path: Path, current: datetime) -> tuple[Inventory, bool]:
    try:
        data = json.loads(inventory_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = None
    inventory = _normalise_inventory(data, current)
    return inventory, data is None


def _sqlite_repository() -> SQLiteInventoryRepository:
    return SQLiteInventoryRepository(paths.DB_PATH, paths.INVENTORY_PATH)


def load_inventory(path: Path | None = None, now: datetime | None = None) -> Inventory:
    current = now or _now_utc()
    if path is None:
        return _sqlite_repository().update(
            lambda raw: _normalise_inventory(raw, current),
            lambda inventory: None,
        )
    inventory_path = path
    inventory, needs_initial_save = _read_inventory(inventory_path, current)
    if needs_initial_save:
        save_inventory(inventory, inventory_path)
    return inventory


def save_inventory(inventory: Inventory, path: Path | None = None) -> None:
    if path is None:
        current = _now_utc()

        def replace(state: Inventory) -> None:
            state.clear()
            state.update(inventory)

        _sqlite_repository().update(lambda raw: _normalise_inventory(raw, current), replace)
        return
    paths._atomic_write_json(path, inventory)


@contextmanager
def _inventory_transaction(
    path: Path | None = None, now: datetime | None = None
) -> Iterator[Inventory]:
    current = now or _now_utc()
    if path is None:
        with _sqlite_repository().transaction(
            lambda raw: _normalise_inventory(raw, current)
        ) as inventory:
            yield inventory
        return
    inventory_path = path
    with paths.exclusive_file_lock(inventory_path):
        inventory, _ = _read_inventory(inventory_path, current)
        yield inventory
        save_inventory(inventory, inventory_path)


def resolve_ball(value: str) -> Ball | None:
    slug = value.strip().lower().replace("é", "e").replace("-", "")
    slug = BALL_ALIASES.get(slug, slug)
    return BALLS.get(slug)


def stock_count(inventory: Inventory, slug: str) -> int | None:
    ball = BALLS[slug]
    if ball.unlimited:
        return None
    return max(0, int(inventory["balls"].get(slug, 0)))


def commits_until_next(inventory: Inventory, slug: str) -> int | None:
    every = COMMIT_REWARD_EVERY.get(slug)
    if every is None:
        return None
    completed = int(inventory["activity"].get("work_commits") or 0)
    remainder = completed % every
    return every - remainder if remainder else every


def consume_ball(slug: str, path: Path | None = None) -> Inventory:
    ball = BALLS[slug]
    if ball.unlimited:
        return load_inventory(path)
    with _inventory_transaction(path) as inventory:
        count = stock_count(inventory, slug) or 0
        if count <= 0:
            raise ValueError(f"No te queda ninguna {ball.name}.")
        inventory["balls"][slug] = count - 1
    return inventory


def discover_repositories(home: Path | None = None) -> list[Path]:
    """Encuentra repos y worktrees bajo HOME evitando árboles de dependencias."""
    return GitActivitySource(skip_dirs=_SKIP_DIRS).discover_repositories(home or Path.home())


def _new_work_commits(
    repositories: list[Path], since: datetime, already_processed: set[str]
) -> dict[str, WorkCommit]:
    return GitActivitySource(
        workday_start_hour=WORKDAY_START_HOUR,
        workday_end_hour=WORKDAY_END_HOUR,
        skip_dirs=_SKIP_DIRS,
    ).new_work_commits(repositories, since, already_processed)


def _add_stock(inventory: Inventory, slug: str, requested: int) -> int:
    current = stock_count(inventory, slug) or 0
    maximum = BALLS[slug].max_stock
    if maximum is None:
        return 0
    new_stock, granted = reward_rules.grant_stock(current, requested, maximum)
    inventory["balls"][slug] = new_stock
    return granted


def _sync_passive(inventory: Inventory, now: datetime) -> list[Reward]:
    activity = inventory["activity"]
    last = _parse_iso(activity.get("last_passive_at"), now)
    intervals = reward_rules.elapsed_intervals(last, now, PASSIVE_INTERVAL)
    if intervals == 0:
        return []
    # Se consume todo el tiempo transcurrido aunque la bolsa esté llena: no hay
    # una deuda invisible de bolas esperando a que el usuario gaste una.
    activity["last_passive_at"] = _iso(last + PASSIVE_INTERVAL * intervals)
    granted = _add_stock(inventory, "superball", intervals)
    return [Reward("superball", granted, "tiempo")] if granted else []


def _sync_commit_rewards(inventory: Inventory, new_commits: int) -> list[Reward]:
    if new_commits <= 0:
        return []
    activity = inventory["activity"]
    previous = int(activity.get("work_commits") or 0)
    current = previous + new_commits
    activity["work_commits"] = current
    rewards: list[Reward] = []
    for slug, every in COMMIT_REWARD_EVERY.items():
        crossed = reward_rules.threshold_crossings(previous, new_commits, every)
        granted = _add_stock(inventory, slug, crossed)
        if granted:
            rewards.append(Reward(slug, granted, "commits"))
    return rewards


def sync_activity(
    path: Path | None = None,
    home: Path | None = None,
    now: datetime | None = None,
    force_repo_scan: bool = False,
) -> SyncResult:
    """Actualiza recompensas sin contar actividad anterior a la primera ejecución."""
    current = now or _now_utc()

    class LegacyBoundary:
        """Keeps the public monkeypatch seam while delegating Git to its adapter."""

        def discover_repositories(self, root: Path) -> list[Path]:
            return discover_repositories(root)

        def new_work_commits(
            self,
            repositories: list[Path],
            since: datetime,
            already_processed: set[str],
        ) -> dict[str, WorkCommit]:
            return _new_work_commits(repositories, since, already_processed)

    policy = activity_application.ActivityPolicy(
        passive_interval=PASSIVE_INTERVAL,
        repo_scan_interval=REPO_SCAN_INTERVAL,
        reward_every=COMMIT_REWARD_EVERY,
        maximum_stock={
            slug: ball.max_stock for slug, ball in BALLS.items() if ball.max_stock is not None
        },
        max_remembered_commits=MAX_REMEMBERED_COMMITS,
    )
    use_case = activity_application.SyncActivity(
        transaction=lambda: _inventory_transaction(path, current),
        activity_source=LegacyBoundary(),
        clock=lambda: current,
        home=home or Path.home(),
        policy=policy,
    )
    return use_case.execute(force_repo_scan=force_repo_scan)
