"""Pure Safari Zone rules: rocks and bait bend an encounter's two axes.

Como en la Zona Safari de Rojo Fuego y Esmeralda, cada encuentro tiene dos
magnitudes en tensión: lo fácil que es capturarlo (*catch factor*) y lo que
aguanta antes de largarse (*escape factor*). Una roca lo aturde y lo enfada:
sube la captura y le quita paciencia. El cebo lo acerca y lo distrae: le da
paciencia pero, mientras come, es más difícil acertarle.

Los ítems son ilimitados; el recurso escaso es el turno. Toda acción gasta uno,
y la paciencia tiene un techo, así que el encuentro siempre termina.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

MIN_CATCH_STAGE = -3
MAX_CATCH_STAGE = 3
MAX_PATIENCE = 9
ROCK_STAGE_STEP = 1
ROCK_PATIENCE_COST = 1
BAIT_STAGE_STEP = -1
BAIT_PATIENCE_GAIN = 2


class SafariAction(str, Enum):
    ROCK = "rock"
    BAIT = "bait"


class SafariMood(str, Enum):
    CALM = "calm"
    ANGRY = "angry"
    EATING = "eating"


@dataclass(frozen=True)
class SafariOutcome:
    """Estado del encuentro tras una acción, listo para persistir o descartar."""

    catch_stage: int
    patience: int
    spent_turns: int
    fled: bool
    mood: SafariMood
    stage_delta: int
    patience_gained: int

    @property
    def remaining_turns(self) -> int:
        return max(0, self.patience - self.spent_turns)


def _clamp_stage(stage: int) -> int:
    return max(MIN_CATCH_STAGE, min(MAX_CATCH_STAGE, int(stage)))


def catch_stage_multiplier(stage: int) -> float:
    """Cada escalón dobla o divide a la mitad la probabilidad de captura."""
    return 2.0 ** _clamp_stage(stage)


def describe_mood(stage: int) -> SafariMood:
    """El ánimo es una lectura del escalón: no necesita estado propio."""
    if stage > 0:
        return SafariMood.ANGRY
    if stage < 0:
        return SafariMood.EATING
    return SafariMood.CALM


def throw(
    action: SafariAction,
    *,
    catch_stage: int,
    patience: int,
    spent_turns: int,
) -> SafariOutcome:
    """Apply one Safari action to an encounter and report the resulting state."""
    if patience < 1:
        raise ValueError("patience must be positive")
    if spent_turns < 0:
        raise ValueError("spent turns cannot be negative")

    current_stage = _clamp_stage(catch_stage)
    if action is SafariAction.ROCK:
        new_stage = _clamp_stage(current_stage + ROCK_STAGE_STEP)
        new_patience = patience - ROCK_PATIENCE_COST
    else:
        new_stage = _clamp_stage(current_stage + BAIT_STAGE_STEP)
        new_patience = min(MAX_PATIENCE, patience + BAIT_PATIENCE_GAIN)

    new_spent = spent_turns + 1
    fled = new_spent >= new_patience
    return SafariOutcome(
        catch_stage=new_stage,
        # Se reporta la paciencia tal cual: si ha bajado hasta el turno gastado
        # el desenlace es huida, y entonces el encuentro se descarta en vez de
        # guardarse. Todo estado que llega a persistirse tiene margen de sobra.
        patience=new_patience,
        spent_turns=new_spent,
        fled=fled,
        mood=SafariMood.ANGRY if action is SafariAction.ROCK else SafariMood.EATING,
        stage_delta=new_stage - current_stage,
        patience_gained=max(0, new_patience - patience),
    )
