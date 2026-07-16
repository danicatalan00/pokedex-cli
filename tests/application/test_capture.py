from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pokedex_cli.application.capture import (
    CaptureCommand,
    CaptureEncounter,
    CaptureStatus,
)
from pokedex_cli.infrastructure import database
from pokedex_cli.infrastructure.repositories import (
    SQLiteCaptureRepository,
    SQLiteEncounterRepository,
    SQLiteInventoryRepository,
)

NOW = datetime(2026, 7, 15, 10, tzinfo=timezone.utc)


class FixedRandom:
    def __init__(self, roll: float = 0.0, escape_after: int = 3) -> None:
        self.roll = roll
        self.escape_after = escape_after

    def random(self) -> float:
        return self.roll

    def randint(self, low: int, high: int) -> int:
        return min(high, max(low, self.escape_after))


def inventory_state(stock: int = 1) -> dict:
    timestamp = NOW.isoformat()
    return {
        "version": 1,
        "balls": {"superball": 3, "ultraball": stock, "masterball": 0},
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


def encounter_state() -> dict:
    return {
        "species": "pikachu",
        "form": "regular",
        "shiny": False,
        "seen_at": NOW.isoformat(),
        "captured": False,
        "failed_capture_attempts": 0,
        "escape_after_attempts": None,
    }


def normalise_inventory(raw: object | None) -> dict:
    return raw if isinstance(raw, dict) else inventory_state()


def build_use_case(
    tmp_path: Path,
    *,
    rng: FixedRandom | None = None,
    capture_repository: SQLiteCaptureRepository | None = None,
) -> tuple[CaptureEncounter, SQLiteInventoryRepository, SQLiteEncounterRepository]:
    db_path = tmp_path / "pokedex.db"
    inventory_repository = SQLiteInventoryRepository(db_path, tmp_path / "inventory.json")
    encounter_repository = SQLiteEncounterRepository(db_path, tmp_path / "last_seen.json")
    use_case = CaptureEncounter(
        connection_factory=lambda: database.connect(db_path),
        inventory_repository=inventory_repository,
        encounter_repository=encounter_repository,
        capture_repository=capture_repository or SQLiteCaptureRepository(),
        inventory_normaliser=normalise_inventory,
        random_source=rng or FixedRandom(),
    )
    return use_case, inventory_repository, encounter_repository


def command() -> CaptureCommand:
    return CaptureCommand(
        ball_slug="ultraball",
        ball_multiplier=2.0,
        caught_at=NOW.isoformat(),
        capture_rate=190,
        speed=90,
        is_legendary=False,
        is_mythical=False,
        growth_rate="medium",
    )


def seed(
    inventory_repository: SQLiteInventoryRepository,
    encounter_repository: SQLiteEncounterRepository,
) -> None:
    inventory_repository.update(normalise_inventory, lambda state: None)
    encounter_repository.write(encounter_state())


def test_success_atomically_consumes_ball_inserts_capture_and_marks_encounter(
    tmp_path: Path,
) -> None:
    use_case, inventory_repository, encounter_repository = build_use_case(tmp_path)
    seed(inventory_repository, encounter_repository)

    result = use_case.execute(command())

    assert result.status is CaptureStatus.CAUGHT
    assert result.capture_id == 1
    assert (
        inventory_repository.update(normalise_inventory, lambda state: None)["balls"]["ultraball"]
        == 0
    )
    assert encounter_repository.read()["captured"] is True


def test_success_with_unknown_gender_and_no_abilities_persists_nulls(tmp_path: Path) -> None:
    # command() defaults gender_rate=None and abilities=(): both traits that
    # need species data the capturer didn't have must be stored as NULL.
    use_case, inventory_repository, encounter_repository = build_use_case(tmp_path)
    seed(inventory_repository, encounter_repository)

    result = use_case.execute(command())

    connection = database.connect(tmp_path / "pokedex.db")
    try:
        row = connection.execute(
            "SELECT iv_hp, iv_atk, iv_def, iv_spa, iv_spd, iv_spe, nature, gender, ability "
            "FROM captures WHERE id = ?",
            (result.capture_id,),
        ).fetchone()
    finally:
        connection.close()
    # FixedRandom().randint always returns 3 (the default escape_after),
    # regardless of the (low, high) bounds it's called with.
    assert (row["iv_hp"], row["iv_atk"], row["iv_def"], row["iv_spa"], row["iv_spd"]) == (
        3,
        3,
        3,
        3,
        3,
    )
    assert row["iv_spe"] == 3
    assert row["nature"] == "adamant"  # NATURES[3]
    assert row["gender"] is None
    assert row["ability"] is None


def test_success_registers_the_species_in_the_dex_forever(tmp_path: Path) -> None:
    use_case, inventory_repository, encounter_repository = build_use_case(tmp_path)
    seed(inventory_repository, encounter_repository)

    result = use_case.execute(command())
    assert result.capture_id is not None

    connection = database.connect(tmp_path / "pokedex.db")
    try:
        row = connection.execute("SELECT species, form, first_caught_at FROM dex_caught").fetchone()
        assert (row["species"], row["form"]) == ("pikachu", "regular")
    finally:
        connection.close()


class SequencedRandom:
    """Replays fixed float/int sequences, one call at a time, to make the
    fixed roll order (ivs, nature, gender, ability) observable in a test."""

    def __init__(self, floats: list[float], ints: list[int]) -> None:
        self._floats = list(floats)
        self._ints = list(ints)

    def random(self) -> float:
        return self._floats.pop(0)

    def randint(self, low: int, high: int) -> int:
        value = self._ints.pop(0)
        assert low <= value <= high
        return value


def test_success_persists_rolled_ivs_nature_gender_and_ability_in_fixed_order(
    tmp_path: Path,
) -> None:
    rng = SequencedRandom(
        floats=[0.0, 0.5],  # 1st: capture-check roll; 2nd: gender roll
        ints=[1, 2, 3, 4, 5, 6, 7, 1],  # ivs (hp..spe), nature index, ability index
    )
    use_case, inventory_repository, encounter_repository = build_use_case(tmp_path, rng=rng)
    seed(inventory_repository, encounter_repository)

    result = use_case.execute(
        replace(
            command(),
            gender_rate=1,  # threshold at roll < 1/8 = 0.125; 0.5 -> male
            abilities=("static", "lightning-rod"),
        )
    )

    connection = database.connect(tmp_path / "pokedex.db")
    try:
        row = connection.execute(
            "SELECT iv_hp, iv_atk, iv_def, iv_spa, iv_spd, iv_spe, nature, gender, ability "
            "FROM captures WHERE id = ?",
            (result.capture_id,),
        ).fetchone()
    finally:
        connection.close()
    assert (row["iv_hp"], row["iv_atk"], row["iv_def"], row["iv_spa"], row["iv_spd"]) == (
        1,
        2,
        3,
        4,
        5,
    )
    assert row["iv_spe"] == 6
    assert row["nature"] == "relaxed"  # NATURES[7]
    assert row["gender"] == "male"
    assert row["ability"] == "lightning-rod"


def test_failure_after_consumption_rolls_back_every_mutation(tmp_path: Path) -> None:
    class FailingCaptureRepository(SQLiteCaptureRepository):
        def insert(self, *args, **kwargs):
            raise RuntimeError("disk failure")

    use_case, inventory_repository, encounter_repository = build_use_case(
        tmp_path, capture_repository=FailingCaptureRepository()
    )
    seed(inventory_repository, encounter_repository)

    with pytest.raises(RuntimeError, match="disk failure"):
        use_case.execute(command())

    assert (
        inventory_repository.update(normalise_inventory, lambda state: None)["balls"]["ultraball"]
        == 1
    )
    assert encounter_repository.read()["captured"] is False


def test_two_concurrent_attempts_cannot_duplicate_capture_or_consume_last_ball_twice(
    tmp_path: Path,
) -> None:
    use_case, inventory_repository, encounter_repository = build_use_case(tmp_path)
    seed(inventory_repository, encounter_repository)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: use_case.execute(command()), range(2)))

    assert sorted((result.status for result in results), key=lambda status: status.value) == [
        CaptureStatus.ALREADY_CAPTURED,
        CaptureStatus.CAUGHT,
    ]
    connection = database.connect(tmp_path / "pokedex.db")
    try:
        assert connection.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 1
    finally:
        connection.close()


def test_failed_attempt_and_escape_are_committed_with_ball_consumption(
    tmp_path: Path,
) -> None:
    use_case, inventory_repository, encounter_repository = build_use_case(
        tmp_path, rng=FixedRandom(roll=0.99, escape_after=1)
    )
    seed(inventory_repository, encounter_repository)
    encounter_repository.update(
        lambda state: state.update(escape_after_attempts=1) if state else None
    )

    result = use_case.execute(replace(command(), capture_rate=1))

    assert result.status is CaptureStatus.FLED
    assert result.attempts == 1
    assert encounter_repository.read() is None
    assert (
        inventory_repository.update(normalise_inventory, lambda state: None)["balls"]["ultraball"]
        == 0
    )


def test_missing_encounter_and_empty_stock_do_not_mutate_state(tmp_path: Path) -> None:
    use_case, inventory_repository, encounter_repository = build_use_case(tmp_path)
    inventory_repository.update(normalise_inventory, lambda state: None)

    assert use_case.execute(command()).status is CaptureStatus.NO_ENCOUNTER

    encounter_repository.write(encounter_state())
    inventory_repository.update(
        normalise_inventory, lambda state: state["balls"].update(ultraball=0)
    )
    assert use_case.execute(command()).status is CaptureStatus.NO_STOCK
    assert encounter_repository.read()["captured"] is False


def test_unlimited_basic_ball_never_requires_or_creates_stock(tmp_path: Path) -> None:
    use_case, inventory_repository, encounter_repository = build_use_case(tmp_path)
    seed(inventory_repository, encounter_repository)

    result = use_case.execute(replace(command(), ball_slug="pokeball", ball_multiplier=1.0))

    assert result.status is CaptureStatus.CAUGHT
    inventory = inventory_repository.update(normalise_inventory, lambda state: None)
    assert "pokeball" not in inventory["balls"]


def test_non_final_failure_persists_attempt_counter_and_escape_threshold(tmp_path: Path) -> None:
    use_case, inventory_repository, encounter_repository = build_use_case(
        tmp_path, rng=FixedRandom(roll=0.99, escape_after=4)
    )
    seed(inventory_repository, encounter_repository)

    result = use_case.execute(replace(command(), capture_rate=1))

    assert result.status is CaptureStatus.FAILED
    assert result.attempts == 1
    assert result.escape_after == 4
    encounter = encounter_repository.read()
    assert encounter["failed_capture_attempts"] == 1
    assert encounter["escape_after_attempts"] == 4


@pytest.mark.parametrize(
    ("capture_rate", "speed", "legendary", "shiny", "expected_range"),
    [
        (0, 180, True, False, (2, 4)),
        (127, 80, False, False, (3, 5)),
        (255, 1, False, False, (4, 6)),
        (0, 180, True, True, (2, 5)),
    ],
)
def test_escape_threshold_uses_capture_speed_rarity_and_shiny_rules(
    tmp_path: Path,
    capture_rate: int,
    speed: int,
    legendary: bool,
    shiny: bool,
    expected_range: tuple[int, int],
) -> None:
    class RecordingRandom(FixedRandom):
        requested_range: tuple[int, int] | None = None

        def randint(self, low: int, high: int) -> int:
            self.requested_range = (low, high)
            return high

    rng = RecordingRandom(roll=0.99)
    use_case, inventory_repository, encounter_repository = build_use_case(tmp_path, rng=rng)
    seed(inventory_repository, encounter_repository)
    encounter_repository.update(lambda state: state.update(shiny=shiny) if state else None)

    use_case.execute(
        replace(
            command(),
            capture_rate=capture_rate,
            speed=speed,
            is_legendary=legendary,
            ball_multiplier=0.0,
        )
    )

    assert rng.requested_range == expected_range
