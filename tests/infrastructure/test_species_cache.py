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
        "encounter_level": 20,
        "level_evolutions": [{"species": "raichu", "form": "regular", "min_level": 20}],
        "gender_rate": 4,
        "abilities": ["static", "lightning-rod"],
        "height_dm": 4,
        "weight_hg": 60,
        "genus": "Pokémon Ratón",
        "habitat": "forest",
        "color": "yellow",
        "shape": "quadruped",
        "egg_groups": ["field", "fairy"],
        "base_happiness": 50,
        "hatch_counter": 10,
        "evolution_chain": ["pichu", "pikachu", "raichu"],
    }


def test_sqlite_species_cache_round_trips_and_updates_payload(tmp_path):
    repository = SQLiteSpeciesCacheRepository(tmp_path / "pokedex.db")
    assert repository.get("pikachu", "regular") is None

    repository.put("pikachu", "regular", payload(), "2026-07-15T10:00:00+00:00")
    cached = repository.get("pikachu", "regular")
    assert cached["pokedex_id"] == 25
    assert json.loads(cached["types"]) == ["electric"]
    assert json.loads(cached["level_evolutions"])[0]["species"] == "raichu"
    assert cached["encounter_level"] == 20
    assert cached["gender_rate"] == 4
    assert json.loads(cached["abilities"]) == ["static", "lightning-rod"]
    assert cached["height_dm"] == 4
    assert cached["weight_hg"] == 60
    assert cached["genus"] == "Pokémon Ratón"
    assert json.loads(cached["egg_groups"]) == ["field", "fairy"]
    assert json.loads(cached["evolution_chain"]) == ["pichu", "pikachu", "raichu"]

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


def test_learnset_is_stored_as_json_text_like_the_other_json_columns(tmp_path):
    repository = SQLiteSpeciesCacheRepository(tmp_path / "pokedex.db")
    data = payload()
    data["level_moves"] = [["thunder-shock", 1], ["growl", 1], ["thunder-wave", 8]]
    data["moves_version_group"] = "emerald"

    repository.put("pikachu", "regular", data, "now")

    cached = repository.get("pikachu", "regular")
    assert json.loads(cached["level_moves"]) == [
        ["thunder-shock", 1],
        ["growl", 1],
        ["thunder-wave", 8],
    ]
    assert cached["moves_version_group"] == "emerald"


def test_payload_without_learnset_leaves_null_and_never_erases_a_known_one(tmp_path):
    repository = SQLiteSpeciesCacheRepository(tmp_path / "pokedex.db")

    repository.put("pikachu", "regular", payload(), "first")
    first = repository.get("pikachu", "regular")
    # NULL significa «aún sin learnset»: el combate lo pedirá a PokeAPI.
    assert first["level_moves"] is None
    assert first["moves_version_group"] is None

    with_learnset = payload()
    with_learnset["level_moves"] = [["thunder-shock", 1]]
    with_learnset["moves_version_group"] = "emerald"
    repository.put("pikachu", "regular", with_learnset, "second")
    repository.put("pikachu", "regular", payload(30), "third")

    kept = repository.get("pikachu", "regular")
    assert kept["pokedex_id"] == 30
    assert kept["fetched_at"] == "third"
    assert json.loads(kept["level_moves"]) == [["thunder-shock", 1]]
    assert kept["moves_version_group"] == "emerald"


def test_a_present_learnset_key_always_replaces_the_cached_one(tmp_path):
    repository = SQLiteSpeciesCacheRepository(tmp_path / "pokedex.db")
    original = payload()
    original["level_moves"] = [["thunder-shock", 1]]
    original["moves_version_group"] = "emerald"
    repository.put("pikachu", "regular", original, "first")

    without_moves = payload()
    without_moves["level_moves"] = []
    without_moves["moves_version_group"] = None
    repository.put("pikachu", "regular", without_moves, "second")

    cached = repository.get("pikachu", "regular")
    assert json.loads(cached["level_moves"]) == []
    assert cached["moves_version_group"] is None


def test_battle_pool_lists_species_with_complete_stats_only(tmp_path):
    repository = SQLiteSpeciesCacheRepository(tmp_path / "pokedex.db")
    mewtwo = payload(150)
    mewtwo.update(types=["psychic"], is_legendary=True)
    zeraora = payload(807)
    zeraora.update(types=["electric"], is_mythical=True)
    incomplete = payload(132)
    incomplete["spe"] = None
    repository.put("pikachu", "regular", payload(), "now")
    repository.put("mewtwo", "regular", mewtwo, "now")
    repository.put("zeraora", "regular", zeraora, "now")
    repository.put("ditto", "regular", incomplete, "now")
    repository.put("raichu", "alola", {**payload(26), "types": ["electric", "psychic"]}, "now")

    assert repository.battle_pool() == [
        {
            "species": "mewtwo",
            "form": "regular",
            "types": ["psychic"],
            "is_legendary": True,
            "is_mythical": False,
        },
        {
            "species": "pikachu",
            "form": "regular",
            "types": ["electric"],
            "is_legendary": False,
            "is_mythical": False,
        },
        {
            "species": "raichu",
            "form": "alola",
            "types": ["electric", "psychic"],
            "is_legendary": False,
            "is_mythical": False,
        },
        {
            "species": "zeraora",
            "form": "regular",
            "types": ["electric"],
            "is_legendary": False,
            "is_mythical": True,
        },
    ]


def test_battle_pool_is_empty_without_cached_species(tmp_path):
    assert SQLiteSpeciesCacheRepository(tmp_path / "pokedex.db").battle_pool() == []


def test_refresh_catalog_lists_unique_captures_and_clears_all_cached_profiles(tmp_path):
    path = tmp_path / "pokedex.db"
    repository = SQLiteSpeciesCacheRepository(path)
    connection = database.connect(path)
    try:
        connection.executemany(
            "INSERT INTO captures (species, form, shiny, caught_at) VALUES (?, ?, 0, 'now')",
            [("pichu", "regular"), ("pichu", "regular"), ("slowking", "galar")],
        )
        connection.execute(
            "INSERT INTO dex_caught (species, form, first_caught_at) "
            "VALUES ('charmeleon', 'regular', 'before-evolving')"
        )
        connection.commit()
    finally:
        connection.close()
    repository.put("pichu", "regular", payload(), "now")
    repository.put("eevee", "regular", payload(), "now")

    assert [(item.species, item.form) for item in repository.captured()] == [
        ("charmeleon", "regular"),
        ("pichu", "regular"),
        ("slowking", "galar"),
    ]

    repository.clear()

    assert repository.get("pichu", "regular") is None
    assert repository.get("eevee", "regular") is None
