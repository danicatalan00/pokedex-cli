from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pokedex_cli.application.activity import ActivityPolicy, SyncActivity, _iso, _parse_iso
from pokedex_cli.domain.models import WorkCommit

NOW = datetime(2026, 7, 15, 10, tzinfo=timezone.utc)


def inventory_state(**activity_overrides):
    activity = {
        "started_at": NOW.isoformat(),
        "last_passive_at": NOW.isoformat(),
        "last_synced_at": NOW.isoformat(),
        "last_repo_scan_at": NOW.isoformat(),
        "repositories": ["/work/repo"],
        "processed_commits": [],
        "work_commits": 2,
    }
    activity.update(activity_overrides)
    return {
        "version": 1,
        "balls": {"superball": 3, "ultraball": 1, "masterball": 0},
        "activity": activity,
    }


class Source:
    def __init__(self, commits=()):
        self.commits = commits
        self.discoveries = 0
        self.requests = []

    def discover_repositories(self, home):
        self.discoveries += 1
        return [Path(home) / "fresh"]

    def new_work_commits(self, repositories, since, already_processed):
        self.requests.append((repositories, since, already_processed))
        return {
            commit.oid: commit for commit in self.commits if commit.oid not in already_processed
        }


def build(state, source, *, now=NOW, max_remembered_commits=2000):
    @contextmanager
    def transaction():
        yield state

    return SyncActivity(
        transaction=transaction,
        activity_source=source,
        clock=lambda: now,
        home=Path("/work"),
        policy=ActivityPolicy(
            passive_interval=timedelta(hours=24),
            repo_scan_interval=timedelta(hours=6),
            reward_every={"superball": 3, "ultraball": 10, "masterball": 50},
            maximum_stock={"superball": 10, "ultraball": 5, "masterball": 1},
            max_remembered_commits=max_remembered_commits,
        ),
    )


def test_sync_rewards_elapsed_time_and_new_commit_in_one_transaction():
    state = inventory_state(
        last_passive_at=(NOW - timedelta(days=1)).isoformat(),
    )
    commit = WorkCommit("abc", 4, 2)

    result = build(state, Source((commit,))).execute()

    assert result.new_work_commits == 1
    assert result.commits == (commit,)
    assert [(reward.slug, reward.count, reward.source) for reward in result.rewards] == [
        ("superball", 1, "tiempo"),
        ("superball", 1, "commits"),
    ]
    assert state["balls"]["superball"] == 5
    assert state["activity"]["work_commits"] == 3
    assert state["activity"]["processed_commits"] == ["abc"]
    assert state["activity"]["last_passive_at"] == NOW.isoformat()
    assert state["activity"]["last_synced_at"] == NOW.isoformat()


def test_first_sync_records_recent_commits_without_rewarding_them():
    state = inventory_state(last_repo_scan_at=None)
    source = Source((WorkCommit("existing", 1, 0),))

    result = build(state, source).execute()

    assert result.new_work_commits == 0
    assert result.commits == ()
    assert result.repositories == 1
    assert source.discoveries == 1
    assert state["activity"]["processed_commits"] == ["existing"]
    assert state["activity"]["work_commits"] == 2
    assert state["activity"]["last_repo_scan_at"] == NOW.isoformat()


def test_scan_policy_uses_cached_repositories_until_forced():
    state = inventory_state()
    source = Source()
    use_case = build(state, source)

    cached = use_case.execute()
    forced = use_case.execute(force_repo_scan=True)

    assert cached.repositories == 1
    assert source.discoveries == 1
    assert forced.repositories == 1
    assert state["activity"]["repositories"] == ["/work/fresh"]
    assert state["activity"]["last_repo_scan_at"] == NOW.isoformat()


def test_stock_is_capped_and_processed_history_is_bounded():
    old = [f"old-{number}" for number in range(4)]
    state = inventory_state(
        last_passive_at=(NOW - timedelta(days=20)).isoformat(),
        processed_commits=old,
    )
    source = Source((WorkCommit("new", 1, 0),))
    use_case = build(state, source, max_remembered_commits=3)

    result = use_case.execute()

    assert state["balls"]["superball"] == 10
    assert result.rewards[0].count == 7
    assert state["activity"]["processed_commits"] == ["old-2", "old-3", "new"]


def test_datetime_conversion_preserves_instants_and_defines_naive_values_as_utc():
    local = datetime(2026, 7, 15, 12, tzinfo=timezone(timedelta(hours=2)))
    assert _iso(local) == NOW.isoformat()
    assert _iso(NOW.replace(tzinfo=None)) == NOW.isoformat()
    assert _parse_iso(local.isoformat(), NOW) == NOW
    assert _parse_iso("2026-07-15T10:00:00", NOW - timedelta(days=1)) == NOW
    assert _parse_iso("invalid", NOW) is NOW
    assert _parse_iso(None, NOW) is NOW


def test_zero_stock_and_zero_commit_count_are_not_promoted_before_rewarding():
    state = inventory_state(work_commits=0)
    state["balls"]["superball"] = 0
    source = Source((WorkCommit("a", 1, 0), WorkCommit("b", 1, 0), WorkCommit("c", 1, 0)))

    result = build(state, source).execute()

    assert state["activity"]["work_commits"] == 3
    assert state["balls"]["superball"] == 1
    assert result.rewards == (type(result.rewards[0])("superball", 1, "commits"),)


def test_passive_clock_advances_only_by_complete_intervals_even_when_stock_is_full():
    start = NOW - timedelta(hours=49)
    state = inventory_state(last_passive_at=start.isoformat())
    state["balls"]["superball"] = 10

    result = build(state, Source()).execute()

    assert result.rewards == ()
    assert state["balls"]["superball"] == 10
    assert state["activity"]["last_passive_at"] == (start + timedelta(hours=48)).isoformat()


def test_repository_scan_runs_at_exact_interval_and_passes_cached_sync_state():
    last_sync = NOW - timedelta(hours=1)
    state = inventory_state(
        last_repo_scan_at=(NOW - timedelta(hours=6)).isoformat(),
        last_synced_at=last_sync.isoformat(),
        processed_commits=["done"],
    )
    source = Source()

    result = build(state, source).execute()

    assert source.discoveries == 1
    assert result.repositories == 1
    assert source.requests == [([Path("/work/fresh")], last_sync, {"done"})]


def test_invalid_saved_timestamps_fall_back_without_creating_backlog():
    state = inventory_state(
        last_passive_at="broken",
        last_repo_scan_at="broken",
        last_synced_at="broken",
    )
    source = Source()

    result = build(state, source).execute()

    assert result.rewards == ()
    assert source.discoveries == 1
    assert source.requests[0][1] == NOW
