import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pokedex_cli import inventory


UTC = timezone.utc


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.inventory_path = self.root / "data" / "inventory.json"
        self.inventory_path.parent.mkdir()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_initial_stock_and_infinite_basic_ball(self):
        state = inventory.load_inventory(
            self.inventory_path, datetime(2026, 7, 13, 9, tzinfo=UTC)
        )
        self.assertEqual(state["balls"], inventory.INITIAL_STOCK)
        self.assertIsNone(inventory.stock_count(state, "pokebola"))

        inventory.consume_ball("pokebola", self.inventory_path)
        after = inventory.load_inventory(self.inventory_path)
        self.assertEqual(after["balls"], inventory.INITIAL_STOCK)

    def test_special_ball_is_consumed_and_cannot_go_below_zero(self):
        state = inventory.load_inventory(self.inventory_path)
        state["balls"]["ultrabola"] = 1
        inventory.save_inventory(state, self.inventory_path)

        inventory.consume_ball("ultrabola", self.inventory_path)
        self.assertEqual(
            inventory.load_inventory(self.inventory_path)["balls"]["ultrabola"], 0
        )
        with self.assertRaisesRegex(ValueError, "Ultrabola"):
            inventory.consume_ball("ultrabola", self.inventory_path)

    def test_time_grants_one_super_ball_per_day(self):
        start = datetime(2026, 7, 13, 9, tzinfo=UTC)
        inventory.load_inventory(self.inventory_path, start)

        result = inventory.sync_activity(
            self.inventory_path,
            home=self.root,
            now=datetime(2026, 7, 15, 10, tzinfo=UTC),
        )
        self.assertEqual(result.inventory["balls"]["superbola"], 5)
        self.assertEqual(result.rewards, (inventory.Reward("superbola", 2, "tiempo"),))

    def test_commit_thresholds_are_cumulative(self):
        state = inventory.load_inventory(self.inventory_path)
        state["activity"]["work_commits"] = 8
        rewards = inventory._sync_commit_rewards(state, 2)

        self.assertEqual(state["activity"]["work_commits"], 10)
        self.assertEqual(state["balls"]["superbola"], 4)
        self.assertEqual(state["balls"]["ultrabola"], 2)
        self.assertEqual(
            rewards,
            [
                inventory.Reward("superbola", 1, "commits"),
                inventory.Reward("ultrabola", 1, "commits"),
            ],
        )

    def test_commits_until_next_reward(self):
        state = inventory.load_inventory(self.inventory_path)
        self.assertEqual(inventory.commits_until_next(state, "superbola"), 3)
        self.assertEqual(inventory.commits_until_next(state, "ultrabola"), 10)
        self.assertEqual(inventory.commits_until_next(state, "masterbola"), 50)
        self.assertIsNone(inventory.commits_until_next(state, "pokebola"))

        state["activity"]["work_commits"] = 9
        self.assertEqual(inventory.commits_until_next(state, "superbola"), 3)
        self.assertEqual(inventory.commits_until_next(state, "ultrabola"), 1)
        self.assertEqual(inventory.commits_until_next(state, "masterbola"), 41)

    def test_discovers_and_counts_only_own_work_hour_commits(self):
        repo = self.root / "project"
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.name", "Trainer")
        self._git(repo, "config", "user.email", "trainer@example.com")

        baseline = datetime(2026, 7, 13, 9, tzinfo=UTC)  # lunes
        inventory.sync_activity(
            self.inventory_path, home=self.root, now=baseline, force_repo_scan=True
        )

        for minute in (10, 11, 12):
            tracked = repo / f"work-{minute}.txt"
            tracked.write_text(str(minute))
            self._git(repo, "add", tracked.name)
            commit_date = f"2026-07-13T10:{minute}:00+00:00"
            self._git(
                repo,
                "commit",
                "-q",
                "-m",
                f"work {minute}",
                env={"GIT_AUTHOR_DATE": commit_date, "GIT_COMMITTER_DATE": commit_date},
            )

        # Este commit es propio, pero fuera de horario laboral.
        (repo / "late.txt").write_text("late")
        self._git(repo, "add", "late.txt")
        self._git(
            repo,
            "commit",
            "-q",
            "-m",
            "late",
            env={
                "GIT_AUTHOR_DATE": "2026-07-13T22:00:00+00:00",
                "GIT_COMMITTER_DATE": "2026-07-13T22:00:00+00:00",
            },
        )

        result = inventory.sync_activity(
            self.inventory_path,
            home=self.root,
            now=datetime(2026, 7, 14, 7, tzinfo=UTC),
        )
        self.assertEqual(result.new_work_commits, 3)
        self.assertEqual(result.inventory["activity"]["work_commits"], 3)
        self.assertEqual(result.inventory["balls"]["superbola"], 4)

    def test_first_sync_does_not_import_recent_history(self):
        repo = self.root / "old-project"
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.name", "Trainer")
        self._git(repo, "config", "user.email", "trainer@example.com")
        (repo / "old.txt").write_text("old")
        self._git(repo, "add", "old.txt")
        self._git(
            repo,
            "commit",
            "-q",
            "-m",
            "before inventory",
            env={
                "GIT_AUTHOR_DATE": "2026-07-13T09:58:00+00:00",
                "GIT_COMMITTER_DATE": "2026-07-13T09:58:00+00:00",
            },
        )

        result = inventory.sync_activity(
            self.inventory_path,
            home=self.root,
            now=datetime(2026, 7, 13, 10, tzinfo=UTC),
            force_repo_scan=True,
        )
        self.assertEqual(result.new_work_commits, 0)
        self.assertEqual(result.inventory["activity"]["work_commits"], 0)

        second = inventory.sync_activity(
            self.inventory_path,
            home=self.root,
            now=datetime(2026, 7, 13, 10, 1, tzinfo=UTC),
        )
        self.assertEqual(second.new_work_commits, 0)
        self.assertEqual(second.inventory["activity"]["work_commits"], 0)

    def _git(self, repo: Path, *args: str, env: dict | None = None) -> None:
        full_env = os.environ.copy()
        full_env.update(env or {})
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            env=full_env,
        )


if __name__ == "__main__":
    unittest.main()
