import json
import os
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

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
        state = inventory.load_inventory(self.inventory_path, datetime(2026, 7, 13, 9, tzinfo=UTC))
        self.assertEqual(state["balls"], inventory.INITIAL_STOCK)
        self.assertIsNone(inventory.stock_count(state, "pokeball"))

        inventory.consume_ball("pokeball", self.inventory_path)
        after = inventory.load_inventory(self.inventory_path)
        self.assertEqual(after["balls"], inventory.INITIAL_STOCK)

    def test_special_ball_is_consumed_and_cannot_go_below_zero(self):
        state = inventory.load_inventory(self.inventory_path)
        state["balls"]["ultraball"] = 1
        inventory.save_inventory(state, self.inventory_path)

        inventory.consume_ball("ultraball", self.inventory_path)
        self.assertEqual(inventory.load_inventory(self.inventory_path)["balls"]["ultraball"], 0)
        with self.assertRaisesRegex(ValueError, "Ultraball"):
            inventory.consume_ball("ultraball", self.inventory_path)

    def test_concurrent_consumers_do_not_lose_an_update(self):
        state = inventory.load_inventory(self.inventory_path)
        state["balls"]["ultraball"] = 2
        inventory.save_inventory(state, self.inventory_path)

        both_consumers_loaded = threading.Barrier(2)
        original_load = inventory.load_inventory
        errors: list[BaseException] = []

        def synchronised_load(*args, **kwargs):
            loaded = original_load(*args, **kwargs)
            both_consumers_loaded.wait(timeout=2)
            return loaded

        def consume() -> None:
            try:
                inventory.consume_ball("ultraball", self.inventory_path)
            except BaseException as error:
                errors.append(error)

        with patch.object(inventory, "load_inventory", synchronised_load):
            consumers = [threading.Thread(target=consume) for _ in range(2)]
            for consumer in consumers:
                consumer.start()
            for consumer in consumers:
                consumer.join(timeout=3)

        self.assertFalse(any(consumer.is_alive() for consumer in consumers))
        self.assertEqual(errors, [])
        self.assertEqual(inventory.load_inventory(self.inventory_path)["balls"]["ultraball"], 0)

    def test_time_grants_one_super_ball_per_day(self):
        start = datetime(2026, 7, 13, 9, tzinfo=UTC)
        inventory.load_inventory(self.inventory_path, start)

        result = inventory.sync_activity(
            self.inventory_path,
            home=self.root,
            now=datetime(2026, 7, 15, 10, tzinfo=UTC),
        )
        self.assertEqual(result.inventory["balls"]["superball"], 5)
        self.assertEqual(result.rewards, (inventory.Reward("superball", 2, "tiempo"),))

    def test_twenty_concurrent_syncs_do_not_duplicate_commit_rewards(self):
        now = datetime(2026, 7, 15, 10, tzinfo=UTC)
        state = inventory.load_inventory(self.inventory_path, now)
        state["activity"].update(
            {
                "work_commits": 2,
                "last_repo_scan_at": inventory._iso(now),
                "last_synced_at": inventory._iso(now),
                "repositories": [],
            }
        )
        inventory.save_inventory(state, self.inventory_path)
        commit = inventory.WorkCommit("one-commit", 3, 1)

        def candidates(repositories, since, already_processed):
            if commit.oid in already_processed:
                return {}
            return {commit.oid: commit}

        with patch.object(inventory, "_new_work_commits", side_effect=candidates):
            with ThreadPoolExecutor(max_workers=20) as pool:
                results = list(
                    pool.map(
                        lambda _: inventory.sync_activity(
                            self.inventory_path, home=self.root, now=now
                        ),
                        range(20),
                    )
                )

        final = inventory.load_inventory(self.inventory_path, now)
        self.assertEqual(sum(result.new_work_commits for result in results), 1)
        self.assertEqual(final["activity"]["work_commits"], 3)
        self.assertEqual(final["balls"]["superball"], 4)
        self.assertEqual(final["activity"]["processed_commits"], [commit.oid])

    def test_commit_thresholds_are_cumulative(self):
        state = inventory.load_inventory(self.inventory_path)
        state["activity"]["work_commits"] = 8
        rewards = inventory._sync_commit_rewards(state, 2)

        self.assertEqual(state["activity"]["work_commits"], 10)
        self.assertEqual(state["balls"]["superball"], 4)
        self.assertEqual(state["balls"]["ultraball"], 2)
        self.assertEqual(
            rewards,
            [
                inventory.Reward("superball", 1, "commits"),
                inventory.Reward("ultraball", 1, "commits"),
            ],
        )

    def test_commits_until_next_reward(self):
        state = inventory.load_inventory(self.inventory_path)
        self.assertEqual(inventory.commits_until_next(state, "superball"), 3)
        self.assertEqual(inventory.commits_until_next(state, "ultraball"), 10)
        self.assertEqual(inventory.commits_until_next(state, "masterball"), 50)
        self.assertIsNone(inventory.commits_until_next(state, "pokeball"))

        state["activity"]["work_commits"] = 9
        self.assertEqual(inventory.commits_until_next(state, "superball"), 3)
        self.assertEqual(inventory.commits_until_next(state, "ultraball"), 1)
        self.assertEqual(inventory.commits_until_next(state, "masterball"), 41)

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
        self.assertEqual(len(result.commits), 3)
        self.assertTrue(all(commit.additions == 1 for commit in result.commits))
        self.assertTrue(all(commit.deletions == 0 for commit in result.commits))
        self.assertEqual(result.inventory["activity"]["work_commits"], 3)
        self.assertEqual(result.inventory["balls"]["superball"], 4)

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


class SQLiteInventoryIntegrationTests(unittest.TestCase):
    def test_default_path_imports_legacy_json_then_uses_sqlite_as_authority(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            database_path = root / "pokedex.db"
            legacy_path = root / "inventory.json"
            now = datetime(2026, 7, 15, 10, tzinfo=UTC)
            legacy = inventory._new_inventory(now)
            legacy["balls"]["ultraball"] = 1
            legacy_path.write_text(json.dumps(legacy))

            with (
                patch.object(inventory.paths, "DB_PATH", database_path),
                patch.object(inventory.paths, "INVENTORY_PATH", legacy_path),
            ):
                loaded = inventory.load_inventory(now=now)
                self.assertEqual(loaded["balls"]["ultraball"], 1)
                inventory.consume_ball("ultraball")
                self.assertEqual(inventory.load_inventory(now=now)["balls"]["ultraball"], 0)

            self.assertEqual(json.loads(legacy_path.read_text()), legacy)


if __name__ == "__main__":
    unittest.main()
