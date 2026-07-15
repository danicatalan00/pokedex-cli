from pokedex_cli import storage
from pokedex_cli.domain.models import WorkCommit
from pokedex_cli.infrastructure import database
from pokedex_cli.infrastructure.repositories import SQLiteTrainingRepository


def test_training_repository_finds_missing_profiles_and_persists_experience(tmp_path):
    path = tmp_path / "training.db"
    connection = database.connect(path)
    try:
        missing = storage.insert_capture(connection, "bulbasaur", "regular", False, "first")
        cached = storage.insert_capture(connection, "pikachu", "regular", False, "second")
        storage.set_team(connection, missing, True)
        storage.set_team(connection, cached, True)
        storage.upsert_species_cache(
            connection,
            "pikachu",
            "regular",
            {"growth_rate": "medium", "base_experience": 112},
            "now",
        )
    finally:
        connection.close()

    repository = SQLiteTrainingRepository(path)
    assert repository.members_missing_progression()[0].species == "bulbasaur"
    results = repository.apply((WorkCommit("commit", 10, 5),))

    assert len(results) == 1
    check = database.connect(path)
    try:
        assert check.execute("SELECT SUM(experience) FROM captures").fetchone()[0] > 0
    finally:
        check.close()
