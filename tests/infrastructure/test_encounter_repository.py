import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pokedex_cli.infrastructure.repositories import SQLiteEncounterRepository


def encounter() -> dict:
    return {
        "species": "pikachu",
        "form": "regular",
        "shiny": False,
        "seen_at": "2026-07-15T10:00:00+00:00",
        "captured": False,
        "failed_capture_attempts": 0,
        "escape_after_attempts": None,
    }


def test_legacy_encounter_imports_once_and_clear_does_not_resurrect_it(
    tmp_path: Path,
) -> None:
    legacy_path = tmp_path / "last_seen.json"
    legacy_path.write_text(json.dumps(encounter()))
    repository = SQLiteEncounterRepository(tmp_path / "pokedex.db", legacy_path)

    assert repository.read() == encounter()
    repository.clear()
    assert repository.read() is None
    assert legacy_path.exists()


def test_invalid_or_incomplete_legacy_encounter_is_ignored(tmp_path: Path) -> None:
    legacy_path = tmp_path / "last_seen.json"
    legacy_path.write_text('{"species": "pikachu"}')
    repository = SQLiteEncounterRepository(tmp_path / "pokedex.db", legacy_path)

    assert repository.read() is None


def test_twenty_concurrent_updates_preserve_every_failed_attempt(tmp_path: Path) -> None:
    repository = SQLiteEncounterRepository(tmp_path / "pokedex.db", tmp_path / "last_seen.json")
    repository.write(encounter())

    def record_failure(_: int) -> None:
        def operation(state: dict | None) -> None:
            assert state is not None
            state["failed_capture_attempts"] += 1
            state["escape_after_attempts"] = 25

        repository.update(operation)

    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(record_failure, range(20)))

    final = repository.read()
    assert final is not None
    assert final["failed_capture_attempts"] == 20
    assert final["escape_after_attempts"] == 25
