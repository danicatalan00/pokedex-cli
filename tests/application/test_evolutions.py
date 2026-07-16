import random
from pathlib import Path

from pokedex_cli.application.evolutions import ProcessEvolutions
from pokedex_cli.infrastructure import database
from pokedex_cli.infrastructure.repositories import SQLiteEvolutionRepository


def build_use_case(tmp_path: Path) -> tuple[ProcessEvolutions, Path]:
    path = tmp_path / "pokedex.db"
    return (
        ProcessEvolutions(
            connection_factory=lambda: database.connect(path),
            repository=SQLiteEvolutionRepository(),
            random_source=random.Random(1),
        ),
        path,
    )


def seed_pending(path: Path, species: str, target: str) -> int:
    connection = database.connect(path)
    try:
        cursor = connection.execute(
            "INSERT INTO captures "
            "(species, form, shiny, caught_at, in_team, level, "
            "pending_evolution_species, pending_evolution_form) "
            "VALUES (?, 'regular', 0, 'now', 1, 16, ?, 'regular')",
            (species, target),
        )
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


def test_processes_exact_preexisting_snapshot_in_order(tmp_path: Path) -> None:
    use_case, path = build_use_case(tmp_path)
    first = seed_pending(path, "bulbasaur", "ivysaur")
    second = seed_pending(path, "charmander", "charmeleon")
    snapshot = use_case.pending()
    late = seed_pending(path, "squirtle", "wartortle")

    transitions = use_case.execute([item.capture_id for item in snapshot])

    assert [item.capture_id for item in transitions] == [first, second]
    connection = database.connect(path)
    try:
        rows = connection.execute(
            "SELECT id, species, pending_evolution_species FROM captures ORDER BY id"
        ).fetchall()
        assert [(row["id"], row["species"]) for row in rows] == [
            (first, "ivysaur"),
            (second, "charmeleon"),
            (late, "squirtle"),
        ]
        assert rows[2]["pending_evolution_species"] == "wartortle"
    finally:
        connection.close()


def test_completed_evolution_can_queue_next_linear_stage(tmp_path: Path) -> None:
    use_case, path = build_use_case(tmp_path)
    capture_id = seed_pending(path, "bulbasaur", "ivysaur")
    connection = database.connect(path)
    try:
        connection.execute(
            "INSERT INTO species_cache "
            "(species, form, level_evolutions, fetched_at) VALUES (?, ?, ?, 'now')",
            (
                "ivysaur",
                "regular",
                '[{"species":"venusaur","form":"regular","min_level":16}]',
            ),
        )
        connection.commit()
    finally:
        connection.close()

    use_case.execute([capture_id])

    connection = database.connect(path)
    try:
        row = connection.execute(
            "SELECT pending_evolution_species FROM captures WHERE id = ?",
            (capture_id,),
        ).fetchone()
        assert row[0] == "venusaur"
    finally:
        connection.close()


def test_invalid_and_non_level_options_do_not_queue(tmp_path: Path) -> None:
    repository = SQLiteEvolutionRepository()
    assert repository.decode_options('[{"species":"x"}, {"trigger":"stone"}]') == []
    assert repository.decode_options("{") == []
    assert repository.decode_options(None) == []


def test_completed_evolution_clears_the_ability_but_leaves_gender_untouched(
    tmp_path: Path,
) -> None:
    use_case, path = build_use_case(tmp_path)
    capture_id = seed_pending(path, "bulbasaur", "ivysaur")
    connection = database.connect(path)
    try:
        connection.execute(
            "UPDATE captures SET gender = 'female', ability = 'overgrow' WHERE id = ?",
            (capture_id,),
        )
        connection.commit()
    finally:
        connection.close()

    use_case.execute([capture_id])

    connection = database.connect(path)
    try:
        row = connection.execute(
            "SELECT gender, ability FROM captures WHERE id = ?", (capture_id,)
        ).fetchone()
        assert row["gender"] == "female"
        assert row["ability"] is None
    finally:
        connection.close()


def test_completed_evolution_registers_the_evolved_species_in_the_dex(
    tmp_path: Path,
) -> None:
    use_case, path = build_use_case(tmp_path)
    capture_id = seed_pending(path, "pichu", "pikachu")

    use_case.execute([capture_id])

    connection = database.connect(path)
    try:
        caught = {row["species"] for row in connection.execute("SELECT species FROM dex_caught")}
        # pichu quedó registrado al capturarlo (backfill de la migración) y
        # pikachu se registra al completar la evolución, como en el juego.
        assert "pikachu" in caught
    finally:
        connection.close()
