import json

from pokedex_cli.infrastructure import database
from pokedex_cli.infrastructure.repositories import SQLiteCollectionRepository


def test_collection_repository_left_joins_cache_and_tolerates_corrupt_types(tmp_path):
    path = tmp_path / "pokedex.db"
    connection = database.connect(path)
    try:
        connection.execute(
            "INSERT INTO captures (species, form, shiny, caught_at, in_team) "
            "VALUES ('offline', 'regular', 0, '2026-07-14', 0), "
            "('pikachu', 'regular', 1, '2026-07-15', 1)"
        )
        connection.execute(
            "INSERT INTO species_cache "
            "(species, form, pokedex_id, types, hp, atk, def, spa, spd, spe, "
            "is_legendary, is_mythical, form_data_exact, growth_rate, gender_rate, "
            "abilities, fetched_at) "
            "VALUES ('pikachu', 'regular', 25, ?, 35, 55, 40, 50, 50, 90, 0, 0, 1, "
            "'medium', 4, ?, 'now')",
            (json.dumps(["electric"]), json.dumps(["static", "lightning-rod"])),
        )
        connection.commit()
    finally:
        connection.close()

    repository = SQLiteCollectionRepository(path)
    rows = repository.list_captures()
    assert [item["species"] for item in rows] == ["pikachu", "offline"]
    assert rows[0]["types"] == ["electric"]
    assert rows[0]["pokedex_id"] == 25
    assert rows[0]["gender_rate"] == 4
    assert rows[0]["abilities"] == ["static", "lightning-rod"]
    assert rows[1]["types"] is None
    assert rows[1]["growth_rate"] == "medium"
    assert rows[1]["gender_rate"] is None
    assert rows[1]["abilities"] is None

    connection = database.connect(path)
    try:
        connection.execute("UPDATE species_cache SET types = '{' WHERE species = 'pikachu'")
        connection.execute("UPDATE species_cache SET abilities = '{' WHERE species = 'pikachu'")
        connection.commit()
    finally:
        connection.close()
    refreshed = repository.list_captures()
    assert refreshed[0]["types"] == []
    assert refreshed[0]["abilities"] == []


def test_catalog_cache_rows_include_evolution_targets(tmp_path):
    path = tmp_path / "pokedex.db"
    connection = database.connect(path)
    try:
        connection.execute(
            "INSERT INTO species_cache (species, form, level_evolutions, fetched_at) "
            "VALUES ('bulbasaur', 'regular', ?, 'now')",
            (json.dumps([{"species": "ivysaur", "form": "regular", "min_level": 16}]),),
        )
        connection.commit()
    finally:
        connection.close()

    rows = SQLiteCollectionRepository(path).species_cache_by_species()
    assert json.loads(rows["bulbasaur"]["level_evolutions"]) == [
        {"species": "ivysaur", "form": "regular", "min_level": 16}
    ]


def test_species_and_variant_capture_lookups(tmp_path):
    path = tmp_path / "pokedex.db"
    connection = database.connect(path)
    try:
        connection.execute(
            "INSERT INTO captures (species, form, shiny, caught_at) "
            "VALUES ('sudowoodo', 'regular', 0, '2026-07-14'), "
            "('pikachu', 'regular', 1, '2026-07-15')"
        )
        # dex_caught keeps a species/form registered even without a live capture
        # (e.g. it evolved away). No shiny column here by design.
        connection.execute(
            "INSERT INTO dex_caught (species, form, first_caught_at) "
            "VALUES ('raichu', 'alola', '2026-07-16')"
        )
        connection.commit()
    finally:
        connection.close()

    repository = SQLiteCollectionRepository(path)

    # Species-level: a plain encounter of an owned species is captured
    # regardless of the individual, and dex_caught alone counts too.
    assert repository.is_species_captured("sudowoodo") is True
    assert repository.is_species_captured("raichu") is True
    assert repository.is_species_captured("mew") is False

    # Alt form via dex_caught (no live capture) still registered.
    assert repository.is_variant_captured("raichu", "alola", False) is True
    assert repository.is_variant_captured("raichu", "galar", False) is False

    # Shiny only counts against a shiny capture, never dex_caught.
    assert repository.is_variant_captured("pikachu", "regular", True) is True
    assert repository.is_variant_captured("sudowoodo", "regular", True) is False
    assert repository.is_variant_captured("raichu", "alola", True) is False
