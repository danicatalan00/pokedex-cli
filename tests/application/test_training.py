from dataclasses import dataclass
from unittest.mock import MagicMock

from pokedex_cli.application.training import SyncTraining, TeamMember


@dataclass(frozen=True)
class Activity:
    commits: tuple[object, ...]
    new_work_commits: int


def test_enriches_missing_team_profiles_and_trains_with_commit_details():
    activity = Activity((object(), object()), 9)
    sync = MagicMock(return_value=activity)
    repository = MagicMock()
    repository.members_missing_progression.return_value = (
        TeamMember("bulbasaur", "regular"),
        TeamMember("raichu", "alola"),
    )
    repository.apply.return_value = ("trained",)
    species = MagicMock()

    result = SyncTraining(sync_activity=sync, repository=repository, species=species).execute(
        force_repo_scan=True
    )

    assert result == (activity, ("trained",))
    sync.assert_called_once_with(force_repo_scan=True)
    assert species.execute.call_args_list[0].args == ("bulbasaur", "regular")
    assert species.execute.call_args_list[1].args == ("raichu", "alola")
    repository.apply.assert_called_once_with(activity.commits)


def test_uses_aggregate_count_when_no_commit_details_are_available():
    activity = Activity((), 3)
    repository = MagicMock()
    repository.members_missing_progression.return_value = ()
    repository.apply.return_value = ()

    SyncTraining(
        sync_activity=MagicMock(return_value=activity),
        repository=repository,
        species=MagicMock(),
    ).execute()

    repository.apply.assert_called_once_with(3)
