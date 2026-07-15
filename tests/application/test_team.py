from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pokedex_cli.application.team import ManageTeam, TeamAction, TeamStatus
from pokedex_cli.infrastructure import database
from pokedex_cli.infrastructure.repositories import SQLiteTeamRepository


def build_use_case(tmp_path: Path) -> tuple[ManageTeam, Path]:
    database_path = tmp_path / "pokedex.db"
    return (
        ManageTeam(
            connection_factory=lambda: database.connect(database_path),
            repository=SQLiteTeamRepository(),
        ),
        database_path,
    )


def seed_captures(database_path: Path, count: int) -> None:
    connection = database.connect(database_path)
    try:
        for index in range(count):
            connection.execute(
                "INSERT INTO captures (species, form, shiny, caught_at) "
                "VALUES (?, 'regular', 0, 'now')",
                (f"pokemon-{index}",),
            )
        connection.commit()
    finally:
        connection.close()


def test_seven_concurrent_additions_leave_exactly_six_members(tmp_path: Path) -> None:
    use_case, database_path = build_use_case(tmp_path)
    seed_captures(database_path, 7)

    with ThreadPoolExecutor(max_workers=7) as pool:
        results = list(
            pool.map(
                lambda capture_id: use_case.execute(TeamAction.ADD, capture_id),
                range(1, 8),
            )
        )

    assert sum(result.status is TeamStatus.ADDED for result in results) == 6
    assert sum(result.status is TeamStatus.FULL for result in results) == 1
    connection = database.connect(database_path)
    try:
        assert SQLiteTeamRepository().count(connection) == 6
    finally:
        connection.close()


def test_add_is_idempotent_and_missing_capture_is_reported(tmp_path: Path) -> None:
    use_case, database_path = build_use_case(tmp_path)
    seed_captures(database_path, 1)

    assert use_case.execute(TeamAction.ADD, 1).status is TeamStatus.ADDED
    assert use_case.execute(TeamAction.ADD, 1).status is TeamStatus.ALREADY_MEMBER
    assert use_case.execute(TeamAction.ADD, 999).status is TeamStatus.NOT_FOUND


def test_remove_is_atomic_and_idempotent(tmp_path: Path) -> None:
    use_case, database_path = build_use_case(tmp_path)
    seed_captures(database_path, 1)
    use_case.execute(TeamAction.ADD, 1)

    assert use_case.execute(TeamAction.REMOVE, 1).status is TeamStatus.REMOVED
    assert use_case.execute(TeamAction.REMOVE, 1).status is TeamStatus.ALREADY_REMOVED
