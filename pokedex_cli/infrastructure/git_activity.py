"""Bounded local Git activity adapter."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pokedex_cli.domain.models import WorkCommit

DEFAULT_SKIP_DIRS = {
    ".cache",
    ".cargo",
    ".local",
    ".npm",
    ".rustup",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "Library",
    "snap",
}


class GitActivitySource:
    def __init__(
        self,
        *,
        runner: Callable[..., Any] = subprocess.run,
        workday_start_hour: int = 8,
        workday_end_hour: int = 19,
        skip_dirs: set[str] | None = None,
    ) -> None:
        self._runner = runner
        self._workday_start_hour = workday_start_hour
        self._workday_end_hour = workday_end_hour
        self._skip_dirs = skip_dirs or DEFAULT_SKIP_DIRS

    def discover_repositories(self, home: Path) -> list[Path]:
        root = home.expanduser().resolve()
        repositories: list[Path] = []
        for current, dirs, files in os.walk(root, topdown=True, onerror=lambda error: None):
            if ".git" in dirs or ".git" in files:
                repositories.append(Path(current))
                if ".git" in dirs:
                    dirs.remove(".git")
            dirs[:] = [
                name for name in dirs if name not in self._skip_dirs and not name.startswith(".")
            ]
        return sorted(set(repositories))

    def _git(self, repo: Path, *args: str) -> str:
        try:
            result = self._runner(
                ["git", "-C", str(repo), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return str(result.stdout) if result.returncode == 0 else ""

    def _known_emails(self, repo: Path) -> set[str]:
        emails = {
            self._git(repo, "config", "--get", "user.email").strip().lower(),
            self._git(repo, "config", "--global", "--get", "user.email").strip().lower(),
        }
        return {email for email in emails if email}

    def is_work_hours(self, author_date: str) -> bool:
        try:
            local_date = datetime.fromisoformat(author_date)
        except ValueError:
            return False
        return (
            local_date.weekday() < 5
            and self._workday_start_hour <= local_date.hour < self._workday_end_hour
        )

    def new_work_commits(
        self,
        repositories: list[Path],
        since: datetime,
        already_processed: set[str],
    ) -> dict[str, WorkCommit]:
        found: dict[str, WorkCommit] = {}
        git_since = (since - timedelta(minutes=5)).isoformat()
        for repo in repositories:
            emails = self._known_emails(repo)
            if not emails:
                continue
            output = self._git(
                repo,
                "log",
                "HEAD",
                "--all",
                f"--since={git_since}",
                "--format=%x1e%H%x1f%aI%x1f%ae",
                "--numstat",
            )
            for block in output.split("\x1e"):
                lines = block.strip("\n").splitlines()
                if not lines:
                    continue
                try:
                    commit, author_date, author_email = lines[0].split("\x1f", 2)
                except ValueError:
                    continue
                if (
                    commit in already_processed
                    or author_email.strip().lower() not in emails
                    or not self.is_work_hours(author_date)
                ):
                    continue
                additions = deletions = 0
                for stat in lines[1:]:
                    parts = stat.split("\t", 2)
                    if len(parts) < 2:
                        continue
                    if parts[0].isdigit():
                        additions += int(parts[0])
                    if parts[1].isdigit():
                        deletions += int(parts[1])
                found[commit] = WorkCommit(commit, additions, deletions)
        return found
