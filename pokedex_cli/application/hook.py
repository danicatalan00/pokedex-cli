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
    # Nombre del rival de un combate salvaje a medias, para recordárselo al
    # jugador; None si no hay combate.
    battle_reminder: str | None = None


class OpenTerminal:
    def __init__(
        self,
        *,
        evolutions: EvolutionProcessor,
        sync_activity: Callable[[], tuple[Any, tuple[Any, ...]]],
        start_wild_encounter: Callable[[str], None],
        prepare_evolution: Callable[[PendingEvolution], object | None],
        battle_in_progress: Callable[[], str | None] | None = None,
    ) -> None:
        self._evolutions = evolutions
        self._sync_activity = sync_activity
        self._start_wild_encounter = start_wild_encounter
        self._prepare_evolution = prepare_evolution
        self._battle_in_progress = battle_in_progress

    def execute(self, generations: str) -> OpenTerminalResult:
        activity, training = self._sync_activity()
        # La sincronización puede subir niveles y encolar evoluciones. Tomar la
        # foto después evita que se pierdan hasta un arranque posterior.
        snapshot = self._evolutions.pending()
        battle = self._battle_reminder()
        if snapshot:
            # El perfil del destino contiene el siguiente eslabón de la cadena.
            # Prepararlo antes de completar permite dejarlo ya encolado si el
            # nivel actual también alcanza ese umbral.
            for evolution in snapshot:
                self._prepare_evolution(evolution)
            transitions = self._evolutions.execute([evolution.capture_id for evolution in snapshot])
            return OpenTerminalResult(
                activity, training, transitions, False, battle_reminder=battle
            )
        if battle is not None:
            # El combate necesita su encuentro: arrancar otro sustituiría al
            # rival y dejaría el combate huérfano.
            return OpenTerminalResult(activity, training, (), False, battle_reminder=battle)
        self._start_wild_encounter(generations)
        return OpenTerminalResult(activity, training, (), True)

    def _battle_reminder(self) -> str | None:
        if self._battle_in_progress is None:
            return None
        try:
            return self._battle_in_progress()
        except Exception:
            # El hook nunca propaga un fallo recuperable: si no se puede saber
            # si hay combate, la terminal sigue como si no lo hubiera.
            return None
