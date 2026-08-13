from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from pokedex_cli.application.safari import (
    SafariCommand,
    SafariStatus,
    ThrowSafariItem,
)
from pokedex_cli.domain.safari import MAX_PATIENCE, SafariAction, SafariMood
from pokedex_cli.infrastructure import database
from pokedex_cli.infrastructure.repositories import SQLiteEncounterRepository

NOW = datetime(2026, 7, 15, 10, tzinfo=timezone.utc)


class FixedRandom:
    """Sólo se usa para materializar la paciencia inicial del encuentro."""

    def __init__(self, patience: int = 4) -> None:
        self.patience = patience

    def random(self) -> float:
        return 0.5

    def randint(self, low: int, high: int) -> int:
        return min(high, max(low, self.patience))


def encounter_state(**overrides: object) -> dict:
    state = {
        "species": "pikachu",
        "form": "regular",
        "shiny": False,
        "seen_at": NOW.isoformat(),
        "captured": False,
        "failed_capture_attempts": 0,
        "escape_after_attempts": None,
        "catch_stage": 0,
        "item_turns": 0,
    }
    state.update(overrides)
    return state


def build_use_case(
    tmp_path: Path, *, patience: int = 4
) -> tuple[ThrowSafariItem, SQLiteEncounterRepository]:
    db_path = tmp_path / "pokedex.db"
    repository = SQLiteEncounterRepository(db_path, tmp_path / "last_seen.json")
    use_case = ThrowSafariItem(
        connection_factory=lambda: database.connect(db_path),
        encounter_repository=repository,
        random_source=FixedRandom(patience),
    )
    return use_case, repository


def command(action: SafariAction = SafariAction.ROCK) -> SafariCommand:
    return SafariCommand(
        action=action,
        capture_rate=190,
        speed=90,
        is_legendary=False,
        is_mythical=False,
    )


def test_a_rock_persists_the_stage_and_the_patience_it_cost(tmp_path: Path) -> None:
    use_case, repository = build_use_case(tmp_path)
    repository.write(encounter_state())

    result = use_case.execute(command())

    assert result.status is SafariStatus.APPLIED
    assert result.mood is SafariMood.ANGRY
    assert (result.catch_stage, result.patience, result.remaining_turns) == (1, 3, 2)
    stored = repository.read()
    assert stored is not None
    assert stored["catch_stage"] == 1
    assert stored["item_turns"] == 1
    assert stored["escape_after_attempts"] == 3
    # Un ítem no es un lanzamiento fallido: ese contador no se toca.
    assert stored["failed_capture_attempts"] == 0


def test_bait_buys_turns_at_the_cost_of_the_catch_stage(tmp_path: Path) -> None:
    use_case, repository = build_use_case(tmp_path)
    repository.write(encounter_state())

    result = use_case.execute(command(SafariAction.BAIT))

    assert result.status is SafariStatus.APPLIED
    assert result.mood is SafariMood.EATING
    assert (result.catch_stage, result.patience, result.remaining_turns) == (-1, 6, 5)
    stored = repository.read()
    assert stored is not None
    assert (stored["catch_stage"], stored["item_turns"]) == (-1, 1)


def test_a_rock_on_its_last_nerve_clears_the_encounter(tmp_path: Path) -> None:
    use_case, repository = build_use_case(tmp_path, patience=2)
    repository.write(encounter_state(escape_after_attempts=2, failed_capture_attempts=1))

    result = use_case.execute(command())

    assert result.status is SafariStatus.FLED
    assert repository.read() is None


def test_the_patience_ceiling_makes_even_bait_run_out(tmp_path: Path) -> None:
    use_case, repository = build_use_case(tmp_path)
    repository.write(
        encounter_state(escape_after_attempts=MAX_PATIENCE, item_turns=MAX_PATIENCE - 1)
    )

    result = use_case.execute(command(SafariAction.BAIT))

    assert result.status is SafariStatus.FLED
    assert result.patience_gained == 0
    assert repository.read() is None


def test_items_materialise_the_escape_threshold_when_no_ball_was_thrown_yet(
    tmp_path: Path,
) -> None:
    use_case, repository = build_use_case(tmp_path, patience=5)
    repository.write(encounter_state(escape_after_attempts=None))

    result = use_case.execute(command(SafariAction.BAIT))

    # 5 (materializada con la fórmula de huida) + 2 del cebo.
    assert result.patience == 7


def test_no_encounter_and_already_captured_do_not_spend_a_turn(tmp_path: Path) -> None:
    use_case, repository = build_use_case(tmp_path)

    assert use_case.execute(command()).status is SafariStatus.NO_ENCOUNTER

    repository.write(encounter_state(captured=True))
    assert use_case.execute(command()).status is SafariStatus.ALREADY_CAPTURED
    stored = repository.read()
    assert stored is not None
    assert stored["item_turns"] == 0


def test_a_failure_mid_transaction_rolls_the_encounter_back(tmp_path: Path) -> None:
    use_case, repository = build_use_case(tmp_path)
    repository.write(encounter_state(escape_after_attempts=4))

    class Boom(SQLiteEncounterRepository):
        def save_in_transaction(self, connection: object, state: dict) -> None:
            raise RuntimeError("disk failure")

    broken, _ = build_use_case(tmp_path)
    broken._encounter_repository = Boom(  # type: ignore[attr-defined]
        tmp_path / "pokedex.db", tmp_path / "last_seen.json"
    )

    try:
        broken.execute(command())
    except RuntimeError:
        pass

    stored = repository.read()
    assert stored is not None
    assert (stored["catch_stage"], stored["item_turns"]) == (0, 0)
    assert use_case.execute(command()).status is SafariStatus.APPLIED


def test_legendary_rock_uses_the_same_escape_rules_as_a_failed_ball(tmp_path: Path) -> None:
    """La paciencia inicial la fija el dominio de captura: rareza y velocidad
    mandan igual que cuando falla una Pokeball."""
    use_case, repository = build_use_case(tmp_path, patience=99)
    repository.write(encounter_state())

    result = use_case.execute(replace(command(), is_legendary=True, capture_rate=3, speed=180))

    # FixedRandom pide el techo del rango que decide el dominio (2..4 aquí).
    assert result.patience == 4 - 1
