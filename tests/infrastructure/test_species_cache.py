import json

from pokedex_cli.infrastructure import database
from pokedex_cli.infrastructure.repositories import SQLiteSpeciesCacheRepository


def payload(pokedex_id=25):
    return {
        "pokedex_id": pokedex_id,
        "capture_rate": 190,
        "types": ["electric"],
        "hp": 35,
        "atk": 55,
        "def": 40,
        "spa": 50,
        "spd": 50,
        "spe": 90,
        "is_legendary": False,
        "is_mythical": False,
        "generation": "generation-i",
        "flavor_text": "Mouse Pokémon",
        "form_data_exact": True,
        "growth_rate": "medium",
        "base_experience": 112,
        "level_evolutions": [{"species": "raichu", "form": "regular", "min_level": 20}],
        "gender_rate": 4,
        "abilities": ["static", "lightning-rod"],
    }


def test_sqlite_species_cache_round_trips_and_updates_payload(tmp_path):
    repository = SQLiteSpeciesCacheRepository(tmp_path / "pokedex.db")
    assert repository.get("pikachu", "regular") is None

    repository.put("pikachu", "regular", payload(), "2026-07-15T10:00:00+00:00")
    cached = repository.get("pikachu", "regular")
    assert cached["pokedex_id"] == 25
    assert json.loads(cached["types"]) == ["electric"]
    assert json.loads(cached["level_evolutions"])[0]["species"] == "raichu"
    assert cached["gender_rate"] == 4
    assert json.loads(cached["abilities"]) == ["static", "lightning-rod"]

    repository.put("pikachu", "regular", payload(26), "2026-07-16T10:00:00+00:00")
    updated = repository.get("pikachu", "regular")
    assert updated["pokedex_id"] == 26
    assert updated["fetched_at"] == "2026-07-16T10:00:00+00:00"


def test_sqlite_species_cache_defaults_gender_rate_and_abilities_when_absent(tmp_path):
    repository = SQLiteSpeciesCacheRepository(tmp_path / "pokedex.db")
    payload_without_individuality = payload()
    del payload_without_individuality["gender_rate"]
    del payload_without_individuality["abilities"]

    repository.put("eevee", "regular", payload_without_individuality, "now")

    cached = repository.get("eevee", "regular")
    assert cached["gender_rate"] is None
    assert json.loads(cached["abilities"]) == []


def test_refresh_catalog_lists_unique_captures_and_clears_all_cached_profiles(tmp_path):
    path = tmp_path / "pokedex.db"
    repository = SQLiteSpeciesCacheRepository(path)
    connection = database.connect(path)
    try:
        connection.executemany(
            "INSERT INTO captures (species, form, shiny, caught_at) VALUES (?, ?, 0, 'now')",
            [("pichu", "regular"), ("pichu", "regular"), ("slowking", "galar")],
        )
        connection.commit()
    finally:
        connection.close()
    repository.put("pichu", "regular", payload(), "now")
    repository.put("eevee", "regular", payload(), "now")

    assert [(item.species, item.form) for item in repository.captured()] == [
        ("pichu", "regular"),
        ("slowking", "galar"),
    ]

    repository.clear()

    assert repository.get("pichu", "regular") is None
    assert repository.get("eevee", "regular") is None
