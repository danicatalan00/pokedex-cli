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
        prepare_evolution: Callable[[PendingEvolution], object | None],
    ) -> None:
        self._evolutions = evolutions
        self._sync_activity = sync_activity
        self._start_wild_encounter = start_wild_encounter
        self._prepare_evolution = prepare_evolution

    def execute(self, generations: str) -> OpenTerminalResult:
        activity, training = self._sync_activity()
        # La sincronización puede subir niveles y encolar evoluciones. Tomar la
        # foto después evita que se pierdan hasta un arranque posterior.
        snapshot = self._evolutions.pending()
        if snapshot:
            # El perfil del destino contiene el siguiente eslabón de la cadena.
            # Prepararlo antes de completar permite dejarlo ya encolado si el
            # nivel actual también alcanza ese umbral.
            for evolution in snapshot:
                self._prepare_evolution(evolution)
            transitions = self._evolutions.execute([evolution.capture_id for evolution in snapshot])
            return OpenTerminalResult(activity, training, transitions, False)
        self._start_wild_encounter(generations)
        return OpenTerminalResult(activity, training, (), True)
