"""Synchronise activity and turn new work into team training."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class ActivitySnapshot(Protocol):
    @property
    def commits(self) -> Sequence[object]: ...

    @property
    def new_work_commits(self) -> int: ...


@dataclass(frozen=True)
class TeamMember:
    species: str
    form: str


class TrainingRepository(Protocol):
    def members_missing_progression(self) -> tuple[TeamMember, ...]: ...

    def apply(self, workload: int | Sequence[object]) -> tuple[Any, ...]: ...


class SpeciesEnricher(Protocol):
    def execute(self, species: str, form: str) -> object | None: ...


class SyncTraining:
    """Coordinate activity, cache enrichment and persisted experience gains."""

    def __init__(
        self,
        *,
        sync_activity: Callable[..., ActivitySnapshot],
        repository: TrainingRepository,
        species: SpeciesEnricher,
    ) -> None:
        self._sync_activity = sync_activity
        self._repository = repository
        self._species = species

    def execute(self, *, force_repo_scan: bool = False) -> tuple[ActivitySnapshot, tuple[Any, ...]]:
        activity = self._sync_activity(force_repo_scan=force_repo_scan)
        for member in self._repository.members_missing_progression():
            self._species.execute(member.species, member.form)
        workload: int | Sequence[object]
        workload = activity.commits if activity.commits else activity.new_work_commits
        return activity, self._repository.apply(workload)
