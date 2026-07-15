import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pokedex_cli.infrastructure.git_activity import GitActivitySource


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    environment = os.environ.copy()
    environment.update(env or {})
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env=environment,
    )


def init_repo(path: Path) -> None:
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.name", "Trainer")
    git(path, "config", "user.email", "trainer@example.com")


def commit_file(
    repo: Path,
    name: str,
    contents: bytes,
    date: str,
    *,
    author_email: str = "trainer@example.com",
) -> None:
    (repo / name).write_bytes(contents)
    git(repo, "add", name)
    git(
        repo,
        "commit",
        "-q",
        "-m",
        name,
        env={
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_DATE": date,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_EMAIL": author_email,
        },
    )


def test_empty_repository_has_no_activity(tmp_path: Path) -> None:
    repo = tmp_path / "empty"
    init_repo(repo)
    source = GitActivitySource()
    assert source.new_work_commits([repo], datetime(2026, 7, 1, tzinfo=timezone.utc), set()) == {}


def test_detached_head_commit_is_discovered(tmp_path: Path) -> None:
    repo = tmp_path / "detached"
    init_repo(repo)
    commit_file(repo, "base.txt", b"base", "2026-07-15T09:00:00+00:00")
    git(repo, "checkout", "--detach", "-q")
    commit_file(repo, "head.txt", b"head", "2026-07-15T10:00:00+00:00")

    found = GitActivitySource().new_work_commits(
        [repo], datetime(2026, 7, 15, 9, 30, tzinfo=timezone.utc), set()
    )
    assert len(found) == 1
    assert next(iter(found.values())).changed_lines == 1


def test_binary_commit_counts_without_fake_changed_lines(tmp_path: Path) -> None:
    repo = tmp_path / "binary"
    init_repo(repo)
    commit_file(repo, "image.bin", b"\x00\x01\x02", "2026-07-15T10:00:00+00:00")
    found = GitActivitySource().new_work_commits(
        [repo], datetime(2026, 7, 15, 9, tzinfo=timezone.utc), set()
    )
    assert len(found) == 1
    assert next(iter(found.values())).changed_lines == 0


def test_unknown_author_email_is_ignored(tmp_path: Path) -> None:
    repo = tmp_path / "foreign"
    init_repo(repo)
    commit_file(
        repo,
        "foreign.txt",
        b"foreign",
        "2026-07-15T10:00:00+00:00",
        author_email="someone@example.com",
    )
    assert (
        GitActivitySource().new_work_commits(
            [repo], datetime(2026, 7, 15, 9, tzinfo=timezone.utc), set()
        )
        == {}
    )


def test_merge_commit_is_counted_once(tmp_path: Path) -> None:
    repo = tmp_path / "merge"
    init_repo(repo)
    commit_file(repo, "base.txt", b"base", "2026-07-15T09:00:00+00:00")
    git(repo, "checkout", "-q", "-b", "feature")
    commit_file(repo, "feature.txt", b"feature", "2026-07-15T10:00:00+00:00")
    git(repo, "checkout", "-q", "master")
    commit_file(repo, "main.txt", b"main", "2026-07-15T10:05:00+00:00")
    git(
        repo,
        "merge",
        "--no-ff",
        "-q",
        "-m",
        "merge feature",
        "feature",
        env={
            "GIT_AUTHOR_DATE": "2026-07-15T10:10:00+00:00",
            "GIT_COMMITTER_DATE": "2026-07-15T10:10:00+00:00",
        },
    )

    all_commits = set(
        subprocess.run(
            ["git", "-C", str(repo), "rev-list", "--all"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )
    merge_oid = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    found = GitActivitySource().new_work_commits(
        [repo],
        datetime(2026, 7, 15, 10, 9, tzinfo=timezone.utc),
        all_commits - {merge_oid},
    )
    assert len(found) == 1


def test_unreadable_repository_is_ignored() -> None:
    def denied(*args, **kwargs):
        raise PermissionError("denied")

    source = GitActivitySource(runner=denied)
    assert (
        source.new_work_commits(
            [Path("/denied")], datetime(2026, 7, 15, tzinfo=timezone.utc), set()
        )
        == {}
    )


def test_work_hour_boundaries_and_weekend() -> None:
    source = GitActivitySource()
    assert source.is_work_hours("2026-07-15T08:00:00+02:00")
    assert source.is_work_hours("2026-07-15T18:59:59+02:00")
    assert not source.is_work_hours("2026-07-15T19:00:00+02:00")
    assert not source.is_work_hours("2026-07-18T10:00:00+02:00")


def test_work_hours_follow_the_authors_local_offset_across_dst() -> None:
    source = GitActivitySource()
    assert source.is_work_hours("2026-03-27T08:00:00+01:00")
    assert source.is_work_hours("2026-03-30T08:00:00+02:00")
    assert not source.is_work_hours("2026-10-26T19:00:00+01:00")
