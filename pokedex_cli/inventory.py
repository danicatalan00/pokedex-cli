"""Inventario de Pokeballs y recompensas por actividad local.

La Pokeball normal es deliberadamente infinita: el inventario añade decisiones
interesantes a la captura, pero nunca bloquea el bucle principal del programa.
Las Pokeballs especiales se reponen con el paso del tiempo y con commits propios
hechos en horario laboral en repositorios Git bajo HOME.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pokedex_cli import paths


@dataclass(frozen=True)
class Ball:
    slug: str
    name: str
    multiplier: float
    max_stock: int | None

    @property
    def unlimited(self) -> bool:
        return self.max_stock is None

    @property
    def guaranteed(self) -> bool:
        return self.multiplier >= 255


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
    ".cache", ".cargo", ".local", ".npm", ".rustup", ".venv", "venv",
    "node_modules", "__pycache__", "Library", "snap",
}


@dataclass(frozen=True)
class Reward:
    slug: str
    count: int
    source: str


@dataclass(frozen=True)
class WorkCommit:
    oid: str
    additions: int
    deletions: int

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions


@dataclass(frozen=True)
class SyncResult:
    inventory: dict
    rewards: tuple[Reward, ...]
    new_work_commits: int
    repositories: int
    commits: tuple[WorkCommit, ...] = ()


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


def _new_inventory(now: datetime) -> dict:
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


def _normalise_inventory(data: object, now: datetime) -> dict:
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
        base["activity"]["work_commits"] = max(
            0, int(base["activity"].get("work_commits") or 0)
        )
    except (TypeError, ValueError):
        base["activity"]["work_commits"] = 0
    base["activity"]["repositories"] = [
        str(repo) for repo in base["activity"].get("repositories", [])
        if isinstance(repo, str)
    ]
    base["activity"]["processed_commits"] = [
        str(commit) for commit in base["activity"].get("processed_commits", [])
        if isinstance(commit, str)
    ][-MAX_REMEMBERED_COMMITS:]
    return base


def load_inventory(path: Path | None = None, now: datetime | None = None) -> dict:
    inventory_path = path or paths.INVENTORY_PATH
    current = now or _now_utc()
    try:
        data = json.loads(inventory_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = None
    inventory = _normalise_inventory(data, current)
    if data is None:
        save_inventory(inventory, inventory_path)
    return inventory


def save_inventory(inventory: dict, path: Path | None = None) -> None:
    paths._atomic_write_json(path or paths.INVENTORY_PATH, inventory)


def resolve_ball(value: str) -> Ball | None:
    slug = value.strip().lower().replace("é", "e").replace("-", "")
    slug = BALL_ALIASES.get(slug, slug)
    return BALLS.get(slug)


def stock_count(inventory: dict, slug: str) -> int | None:
    ball = BALLS[slug]
    if ball.unlimited:
        return None
    return max(0, int(inventory["balls"].get(slug, 0)))


def commits_until_next(inventory: dict, slug: str) -> int | None:
    every = COMMIT_REWARD_EVERY.get(slug)
    if every is None:
        return None
    completed = int(inventory["activity"].get("work_commits") or 0)
    remainder = completed % every
    return every - remainder if remainder else every


def consume_ball(slug: str, path: Path | None = None) -> dict:
    ball = BALLS[slug]
    inventory = load_inventory(path)
    if ball.unlimited:
        return inventory
    count = stock_count(inventory, slug) or 0
    if count <= 0:
        raise ValueError(f"No te queda ninguna {ball.name}.")
    inventory["balls"][slug] = count - 1
    save_inventory(inventory, path)
    return inventory


def discover_repositories(home: Path | None = None) -> list[Path]:
    """Encuentra repos y worktrees bajo HOME evitando árboles de dependencias."""
    root = (home or Path.home()).expanduser().resolve()
    repositories: list[Path] = []

    def onerror(_: OSError) -> None:
        return

    for current, dirs, files in os.walk(root, topdown=True, onerror=onerror):
        if ".git" in dirs or ".git" in files:
            repositories.append(Path(current))
            if ".git" in dirs:
                dirs.remove(".git")
        dirs[:] = [
            name for name in dirs
            if name not in _SKIP_DIRS and not name.startswith(".")
        ]
    return sorted(set(repositories))


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _known_emails(repo: Path) -> set[str]:
    emails = {
        _git(repo, "config", "--get", "user.email").strip().lower(),
        _git(repo, "config", "--global", "--get", "user.email").strip().lower(),
    }
    return {email for email in emails if email}


def _is_work_hours(author_date: str) -> bool:
    try:
        local_date = datetime.fromisoformat(author_date)
    except ValueError:
        return False
    return (
        local_date.weekday() < 5
        and WORKDAY_START_HOUR <= local_date.hour < WORKDAY_END_HOUR
    )


def _new_work_commits(
    repositories: list[Path], since: datetime, already_processed: set[str]
) -> dict[str, WorkCommit]:
    found: dict[str, WorkCommit] = {}
    # El solape cubre commits con la misma marca temporal que el sync anterior.
    git_since = _iso(since - timedelta(minutes=5))
    for repo in repositories:
        emails = _known_emails(repo)
        if not emails:
            continue
        output = _git(
            repo, "log", "--all", f"--since={git_since}",
            "--format=%x1e%H%x1f%aI%x1f%ae", "--numstat",
        )
        for block in output.split("\x1e"):
            lines = block.strip("\n").splitlines()
            if not lines:
                continue
            try:
                commit, author_date, author_email = lines[0].split("\x1f", 2)
            except ValueError:
                continue
            if (
                commit not in already_processed
                and author_email.strip().lower() in emails
                and _is_work_hours(author_date)
            ):
                additions = deletions = 0
                for stat in lines[1:]:
                    parts = stat.split("\t", 2)
                    if len(parts) < 2:
                        continue
                    # Git representa ficheros binarios como "-\t-"; no hay
                    # lineas comparables y por eso no inflan la dificultad.
                    if parts[0].isdigit():
                        additions += int(parts[0])
                    if parts[1].isdigit():
                        deletions += int(parts[1])
                found[commit] = WorkCommit(commit, additions, deletions)
    return found


def _add_stock(inventory: dict, slug: str, requested: int) -> int:
    current = stock_count(inventory, slug) or 0
    maximum = BALLS[slug].max_stock
    granted = max(0, min(requested, maximum - current))
    inventory["balls"][slug] = current + granted
    return granted


def _sync_passive(inventory: dict, now: datetime) -> list[Reward]:
    activity = inventory["activity"]
    last = _parse_iso(activity.get("last_passive_at"), now)
    intervals = max(0, int((now - last) // PASSIVE_INTERVAL))
    if intervals == 0:
        return []
    # Se consume todo el tiempo transcurrido aunque la bolsa esté llena: no hay
    # una deuda invisible de bolas esperando a que el usuario gaste una.
    activity["last_passive_at"] = _iso(last + PASSIVE_INTERVAL * intervals)
    granted = _add_stock(inventory, "superball", intervals)
    return [Reward("superball", granted, "tiempo")] if granted else []


def _sync_commit_rewards(inventory: dict, new_commits: int) -> list[Reward]:
    if new_commits <= 0:
        return []
    activity = inventory["activity"]
    previous = int(activity.get("work_commits") or 0)
    current = previous + new_commits
    activity["work_commits"] = current
    rewards: list[Reward] = []
    for slug, every in COMMIT_REWARD_EVERY.items():
        crossed = (current // every) - (previous // every)
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
    inventory = load_inventory(path, current)
    activity = inventory["activity"]
    rewards = _sync_passive(inventory, current)

    first_sync = activity.get("last_repo_scan_at") is None
    last_scan = _parse_iso(
        activity.get("last_repo_scan_at"),
        datetime.min.replace(tzinfo=timezone.utc),
    )
    repo_paths = [Path(repo) for repo in activity.get("repositories", [])]
    if force_repo_scan or first_sync or current - last_scan >= REPO_SCAN_INTERVAL:
        repo_paths = discover_repositories(home)
        activity["repositories"] = [str(repo) for repo in repo_paths]
        activity["last_repo_scan_at"] = _iso(current)

    last_synced = _parse_iso(activity.get("last_synced_at"), current)
    processed_list = activity.get("processed_commits", [])
    processed = set(processed_list)
    candidates = _new_work_commits(repo_paths, last_synced, processed)
    # En el primer uso se memorizan los commits dentro del pequeño solape, pero
    # no se premian. Así tampoco reaparecen en la segunda sincronización.
    commits = {} if first_sync else candidates
    rewards.extend(_sync_commit_rewards(inventory, len(commits)))
    activity["processed_commits"] = (
        processed_list + sorted(candidates)
    )[-MAX_REMEMBERED_COMMITS:]
    activity["last_synced_at"] = _iso(current)
    save_inventory(inventory, path)
    commit_details = tuple(commits[oid] for oid in sorted(commits))
    return SyncResult(
        inventory, tuple(rewards), len(commits), len(repo_paths), commit_details
    )
