import json
from pathlib import Path

import pytest

from pokedex_cli.application.individuality import BackfillIndividuality
from pokedex_cli.domain.individuality import derive_ability, derive_gender, derive_ivs_nature
from pokedex_cli.infrastructure import database
from pokedex_cli.infrastructure.repositories import SQLiteIndividualityRepository


def build_use_case(tmp_path: Path) -> tuple[BackfillIndividuality, Path]:
    path = tmp_path / "pokedex.db"
    database.connect(path).close()
    return (
        BackfillIndividuality(
            connection_factory=lambda: database.connect(path),
            repository=SQLiteIndividualityRepository(),
        ),
        path,
    )


def insert_capture(path: Path, capture_id: int, caught_at: str, species: str = "pikachu") -> None:
    connection = database.connect(path)
    try:
        connection.execute(
            "INSERT INTO captures (id, species, form, shiny, caught_at) "
            "VALUES (?, ?, 'regular', 0, ?)",
            (capture_id, species, caught_at),
        )
        connection.commit()
    finally:
        connection.close()


def cache_species(
    path: Path,
    species: str,
    *,
    gender_rate: int | None = None,
    abilities: list[str] | None = None,
) -> None:
    connection = database.connect(path)
    try:
        connection.execute(
            "INSERT INTO species_cache (species, form, gender_rate, abilities, fetched_at) "
            "VALUES (?, 'regular', ?, ?, 'now')",
            (species, gender_rate, json.dumps(abilities if abilities is not None else [])),
        )
        connection.commit()
    finally:
        connection.close()


def fetch_capture(path: Path, capture_id: int) -> dict:
    connection = database.connect(path)
    try:
        row = connection.execute("SELECT * FROM captures WHERE id = ?", (capture_id,)).fetchone()
        return dict(row)
    finally:
        connection.close()


def test_backfill_fills_only_the_missing_fields_and_is_idempotent(tmp_path: Path) -> None:
    use_case, path = build_use_case(tmp_path)

    # (a) brand new capture: no species cache at all -> only ivs/nature.
    insert_capture(path, 1, "2026-07-15T10:00:00+00:00", "pikachu")

    # (b) ivs already present (as migration 007 would leave them), gender
    # missing but the species is cached with a gender_rate -> only gender.
    insert_capture(path, 2, "2026-07-14T10:00:00+00:00", "eevee")
    ivs, nature = derive_ivs_nature("2:2026-07-14T10:00:00+00:00")
    connection = database.connect(path)
    try:
        connection.execute(
            "UPDATE captures SET iv_hp=?, iv_atk=?, iv_def=?, iv_spa=?, iv_spd=?, iv_spe=?, "
            "nature=? WHERE id = 2",
            (*(ivs[k] for k in ("hp", "atk", "def", "spa", "spd", "spe")), nature.name),
        )
        connection.commit()
    finally:
        connection.close()
    cache_species(path, "eevee", gender_rate=4, abilities=["run-away"])

    result = use_case.execute()

    # Only capture 1 was missing IVs (capture 2's were seeded above); only
    # capture 2 was missing gender (capture 1 has no species cache at all).
    assert result.filled_ivs == 1
    assert result.filled_gender == 1
    assert result.filled_ability == 1
    row1 = fetch_capture(path, 1)
    row2 = fetch_capture(path, 2)

    expected_ivs_1, expected_nature_1 = derive_ivs_nature("1:2026-07-15T10:00:00+00:00")
    assert row1["iv_hp"] == expected_ivs_1["hp"]
    assert row1["nature"] == expected_nature_1.name
    assert row1["gender"] is None
    assert row1["ability"] is None

    assert row2["gender"] == derive_gender("2:2026-07-14T10:00:00+00:00", 4)
    assert row2["ability"] == derive_ability("2:2026-07-14T10:00:00+00:00", ("run-away",))

    # Running it again must be a no-op: nothing left to fill.
    again = use_case.execute()
    assert again.filled_ivs == 0
    assert again.filled_gender == 0
    assert again.filled_ability == 0
    assert fetch_capture(path, 1) == row1
    assert fetch_capture(path, 2) == row2


def test_ability_is_not_filled_when_the_cached_abilities_list_is_empty(tmp_path: Path) -> None:
    use_case, path = build_use_case(tmp_path)
    insert_capture(path, 1, "now", "ditto")
    connection = database.connect(path)
    try:
        connection.execute(
            "UPDATE captures SET iv_hp=0, iv_atk=0, iv_def=0, iv_spa=0, iv_spd=0, iv_spe=0, "
            "nature='hardy' WHERE id = 1"
        )
        connection.commit()
    finally:
        connection.close()
    cache_species(path, "ditto", gender_rate=None, abilities=[])

    result = use_case.execute()

    assert result.filled_ivs == 0
    assert result.filled_gender == 0
    assert result.filled_ability == 0
    row = fetch_capture(path, 1)
    assert row["gender"] is None
    assert row["ability"] is None


def test_backfill_counts_multiple_captures_needing_different_fields(tmp_path: Path) -> None:
    use_case, path = build_use_case(tmp_path)
    insert_capture(path, 1, "2026-07-15T10:00:00+00:00", "bulbasaur")
    insert_capture(path, 2, "2026-07-15T11:00:00+00:00", "charmander")
    insert_capture(path, 3, "2026-07-15T12:00:00+00:00", "squirtle")
    cache_species(path, "charmander", gender_rate=1, abilities=["blaze"])
    cache_species(path, "squirtle", gender_rate=1, abilities=["torrent"])

    result = use_case.execute()

    assert result.filled_ivs == 3
    assert result.filled_gender == 2
    assert result.filled_ability == 2


def test_execute_runs_in_a_single_transaction_and_rolls_back_on_failure(tmp_path: Path) -> None:
    use_case, path = build_use_case(tmp_path)
    insert_capture(path, 1, "now", "bulbasaur")

    class FailingRepository(SQLiteIndividualityRepository):
        def update_ivs_and_nature(self, *args, **kwargs):
            raise RuntimeError("disk failure")

    failing_use_case = BackfillIndividuality(
        connection_factory=lambda: database.connect(path),
        repository=FailingRepository(),
    )

    with pytest.raises(RuntimeError, match="disk failure"):
        failing_use_case.execute()

    row = fetch_capture(path, 1)
    assert row["iv_hp"] is None
