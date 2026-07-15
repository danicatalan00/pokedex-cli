"""Compatibility exports for progression rules and persisted training."""

from pokedex_cli.domain.progression import (
    DEFAULT_BASE_EXPERIENCE,
    MAX_LEVEL,
    STARTING_LEVEL,
    commit_difficulty,
    commit_experience,
    experience_for_level,
    level_for_experience,
)
from pokedex_cli.infrastructure.training import (
    TrainingResult,
    apply_commit_experience,
    queue_current_evolution,
)

__all__ = [
    "DEFAULT_BASE_EXPERIENCE",
    "MAX_LEVEL",
    "STARTING_LEVEL",
    "TrainingResult",
    "apply_commit_experience",
    "commit_difficulty",
    "commit_experience",
    "experience_for_level",
    "level_for_experience",
    "queue_current_evolution",
]
