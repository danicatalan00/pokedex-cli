"""Activity synchronization use case with explicit external dependencies."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, ContextManager, Protocol

from pokedex_cli.domain.models import WorkCommit
from pokedex_cli.domain.rewards import elapsed_intervals, grant_stock, threshold_crossings

Inventory = dict[str, Any]
InventoryTransaction = Callable[[], ContextManager[Inventory]]


class ActivitySource(Protocol):
    def discover_repositories(self, home: Path) -> list[Path]: ...

    def new_work_commits(
        self,
        repositories: list[Path],
        since: datetime,
        already_processed: set[str],
    ) -> dict[str, WorkCommit]: ...


@dataclass(frozen=True)
class ActivityPolicy:
    passive_interval: timedelta
    repo_scan_interval: timedelta
    reward_every: Mapping[str, int]
    maximum_stock: Mapping[str, int]
    max_remembered_commits: int


@dataclass(frozen=True)
class Reward:
    slug: str
    count: int
    source: str


@dataclass(frozen=True)
class SyncActivityResult:
    inventory: Inventory
    rewards: tuple[Reward, ...]
    new_work_commits: int
    repositories: int
    commits: tuple[WorkCommit, ...] = ()


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: object, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(value if isinstance(value, str) else "")
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class SyncActivity:
    """Synchronize passive and Git rewards inside one inventory transaction."""

    def __init__(
        self,
        *,
        transaction: InventoryTransaction,
        activity_source: ActivitySource,
        clock: Callable[[], datetime],
        home: Path,
        policy: ActivityPolicy,
    ) -> None:
        self._transaction = transaction
        self._activity_source = activity_source
        self._clock = clock
        self._home = home
        self._policy = policy

    def _add_stock(self, inventory: Inventory, slug: str, requested: int) -> int:
        maximum = self._policy.maximum_stock.get(slug)
        if maximum is None:
            return 0
        current = max(0, int(inventory["balls"].get(slug, 0)))
        new_stock, granted = grant_stock(current, requested, maximum)
        inventory["balls"][slug] = new_stock
        return granted

    def _sync_passive(self, inventory: Inventory, now: datetime) -> list[Reward]:
        activity = inventory["activity"]
        last = _parse_iso(activity.get("last_passive_at"), now)
        intervals = elapsed_intervals(last, now, self._policy.passive_interval)
        if intervals == 0:
            return []
        activity["last_passive_at"] = _iso(last + self._policy.passive_interval * intervals)
        granted = self._add_stock(inventory, "superball", intervals)
        return [Reward("superball", granted, "tiempo")] if granted else []

    def _sync_commit_rewards(self, inventory: Inventory, new_commits: int) -> list[Reward]:
        if new_commits <= 0:
            return []
        activity = inventory["activity"]
        previous = max(0, int(activity.get("work_commits") or 0))
        activity["work_commits"] = previous + new_commits
        rewards: list[Reward] = []
        for slug, every in self._policy.reward_every.items():
            crossed = threshold_crossings(previous, new_commits, every)
            granted = self._add_stock(inventory, slug, crossed)
            if granted:
                rewards.append(Reward(slug, granted, "commits"))
        return rewards

    def execute(self, *, force_repo_scan: bool = False) -> SyncActivityResult:
        now = self._clock()
        with self._transaction() as inventory:
            activity = inventory["activity"]
            rewards = self._sync_passive(inventory, now)

            first_sync = activity.get("last_repo_scan_at") is None
            last_scan = _parse_iso(
                activity.get("last_repo_scan_at"),
                datetime.min.replace(tzinfo=timezone.utc),
            )
            repositories = [Path(repo) for repo in activity.get("repositories", [])]
            if force_repo_scan or first_sync or now - last_scan >= self._policy.repo_scan_interval:
                repositories = self._activity_source.discover_repositories(self._home)
                activity["repositories"] = [str(repo) for repo in repositories]
                activity["last_repo_scan_at"] = _iso(now)

            last_synced = _parse_iso(activity.get("last_synced_at"), now)
            processed_list = [str(oid) for oid in activity.get("processed_commits", [])]
            candidates = self._activity_source.new_work_commits(
                repositories, last_synced, set(processed_list)
            )
            commits = {} if first_sync else candidates
            rewards.extend(self._sync_commit_rewards(inventory, len(commits)))
            activity["processed_commits"] = (processed_list + sorted(candidates))[
                -self._policy.max_remembered_commits :
            ]
            activity["last_synced_at"] = _iso(now)

        details = tuple(commits[oid] for oid in sorted(commits))
        return SyncActivityResult(
            inventory,
            tuple(rewards),
            len(commits),
            len(repositories),
            details,
        )
