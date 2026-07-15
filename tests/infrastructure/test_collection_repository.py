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
