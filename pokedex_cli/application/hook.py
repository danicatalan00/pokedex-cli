"""Open-terminal orchestration without presentation dependencies."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pokedex_cli.application.evolutions import PendingEvolution


class EvolutionProcessor(Protocol):
    def pending(self) -> tuple[PendingEvolution, ...]: ...

    def execute(self, capture_ids: Sequence[int]) -> tuple[PendingEvolution, ...]: ...


@dataclass(frozen=True)
class OpenTerminalResult:
    activity: Any
    training: tuple[Any, ...]
    evolutions: tuple[PendingEvolution, ...]
    wild_encounter_started: bool


class OpenTerminal:
    def __init__(
        self,
        *,
        evolutions: EvolutionProcessor,
        sync_activity: Callable[[], tuple[Any, tuple[Any, ...]]],
        start_wild_encounter: Callable[[str], None],
    ) -> None:
        self._evolutions = evolutions
        self._sync_activity = sync_activity
        self._start_wild_encounter = start_wild_encounter

    def execute(self, generations: str) -> OpenTerminalResult:
        snapshot = self._evolutions.pending()
        activity, training = self._sync_activity()
        if snapshot:
            transitions = self._evolutions.execute([evolution.capture_id for evolution in snapshot])
            return OpenTerminalResult(activity, training, transitions, False)
        self._start_wild_encounter(generations)
        return OpenTerminalResult(activity, training, (), True)
