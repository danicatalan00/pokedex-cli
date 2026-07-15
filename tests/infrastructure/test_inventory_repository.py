import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pokedex_cli.infrastructure.repositories import SQLiteInventoryRepository

NOW = datetime(2026, 7, 15, 10, tzinfo=timezone.utc)


def inventory_state(stock: int = 2) -> dict:
    timestamp = NOW.isoformat()
    return {
        "version": 1,
        "balls": {"superball": 3, "ultraball": stock, "masterball": 0},
        "activity": {
            "started_at": timestamp,
            "last_passive_at": timestamp,
            "last_synced_at": timestamp,
            "last_repo_scan_at": None,
            "repositories": ["/tmp/repo"],
            "processed_commits": ["a", "b"],
            "work_commits": 2,
        },
    }


def normalise(raw: object | None) -> dict:
    return raw if isinstance(raw, dict) else inventory_state()


def test_legacy_json_is_imported_once_and_sqlite_becomes_authoritative(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "pokedex.db"
    legacy_path = tmp_path / "inventory.json"
    legacy_path.write_text(json.dumps(inventory_state()))
    repository = SQLiteInventoryRepository(database_path, legacy_path)

    imported = repository.update(normalise, lambda state: None)
    assert imported == inventory_state()

    changed_legacy = inventory_state(stock=5)
    legacy_path.write_text(json.dumps(changed_legacy))
    loaded = repository.update(normalise, lambda state: None)
    assert loaded["balls"]["ultraball"] == 2


@pytest.mark.parametrize("contents", ["{", "null", "[]"])
def test_invalid_legacy_json_falls_back_without_partial_state(
    tmp_path: Path, contents: str
) -> None:
    legacy_path = tmp_path / "inventory.json"
    legacy_path.write_text(contents)
    repository = SQLiteInventoryRepository(tmp_path / "pokedex.db", legacy_path)

    loaded = repository.update(normalise, lambda state: None)
    assert loaded == inventory_state()


def test_operation_failure_rolls_back_all_inventory_changes(tmp_path: Path) -> None:
    repository = SQLiteInventoryRepository(tmp_path / "pokedex.db", tmp_path / "inventory.json")
    repository.update(normalise, lambda state: None)

    def fail_after_change(state: dict) -> None:
        state["balls"]["ultraball"] = 0
        state["activity"]["work_commits"] = 99
        raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        repository.update(normalise, fail_after_change)

    loaded = repository.update(normalise, lambda state: None)
    assert loaded["balls"]["ultraball"] == 2
    assert loaded["activity"]["work_commits"] == 2


def test_concurrent_updates_consume_each_unit_exactly_once(tmp_path: Path) -> None:
    repository = SQLiteInventoryRepository(tmp_path / "pokedex.db", tmp_path / "inventory.json")
    repository.update(normalise, lambda state: None)

    def consume(_: int) -> bool:
        consumed = False

        def operation(state: dict) -> None:
            nonlocal consumed
            if state["balls"]["ultraball"] > 0:
                state["balls"]["ultraball"] -= 1
                consumed = True

        repository.update(normalise, operation)
        return consumed

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(consume, range(20)))

    loaded = repository.update(normalise, lambda state: None)
    assert sum(results) == 2
    assert loaded["balls"]["ultraball"] == 0
