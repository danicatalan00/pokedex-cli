import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from pokedex_cli import paths


class AtomicJsonWriteTests(unittest.TestCase):
    def test_concurrent_writers_do_not_share_a_temporary_file(self):
        """Reproduce the inventory.json.tmp collision seen during WSL startup."""
        with tempfile.TemporaryDirectory() as tempdir:
            destination = Path(tempdir) / "inventory.json"
            both_writers_reached_replace = threading.Barrier(2)
            original_write_text = Path.write_text
            errors: list[BaseException] = []

            def synchronised_write(path: Path, *args, **kwargs):
                result = original_write_text(path, *args, **kwargs)
                both_writers_reached_replace.wait(timeout=2)
                return result

            def write(value: int) -> None:
                try:
                    paths._atomic_write_json(destination, {"value": value})
                except BaseException as error:
                    errors.append(error)

            with patch.object(Path, "write_text", synchronised_write):
                writers = [threading.Thread(target=write, args=(value,)) for value in (1, 2)]
                for writer in writers:
                    writer.start()
                for writer in writers:
                    writer.join(timeout=3)

            self.assertFalse(any(writer.is_alive() for writer in writers))
            self.assertEqual(errors, [])
            self.assertIn(json.loads(destination.read_text()), ({"value": 1}, {"value": 2}))


class LastSeenConcurrencyTests(unittest.TestCase):
    def test_legacy_encounter_is_imported_then_sqlite_is_authoritative(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            database_path = root / "pokedex.db"
            legacy_path = root / "last_seen.json"
            initial = {
                "species": "pikachu",
                "form": "regular",
                "shiny": False,
                "seen_at": "2026-07-15T10:00:00+00:00",
                "captured": False,
                "failed_capture_attempts": 0,
                "escape_after_attempts": None,
            }
            legacy_path.write_text(json.dumps(initial))

            with (
                patch.object(paths, "DB_PATH", database_path),
                patch.object(paths, "LAST_SEEN_PATH", legacy_path),
            ):
                self.assertEqual(paths.read_last_seen(), initial)
                paths.mark_last_seen_captured()
                self.assertTrue(paths.read_last_seen()["captured"])

            self.assertFalse(json.loads(legacy_path.read_text())["captured"])

    def test_concurrent_failed_captures_preserve_both_attempts(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            database_path = root / "pokedex.db"
            legacy_path = root / "last_seen.json"
            errors: list[BaseException] = []

            def record_failure() -> None:
                try:
                    paths.record_last_seen_failed_capture(25)
                except BaseException as error:
                    errors.append(error)

            with (
                patch.object(paths, "DB_PATH", database_path),
                patch.object(paths, "LAST_SEEN_PATH", legacy_path),
            ):
                paths.write_last_seen("pikachu", "regular", False, "2026-07-15T10:00:00+00:00")
                callers = [threading.Thread(target=record_failure) for _ in range(20)]
                for caller in callers:
                    caller.start()
                for caller in callers:
                    caller.join(timeout=10)
                final = paths.read_last_seen()

            self.assertFalse(any(caller.is_alive() for caller in callers))
            self.assertEqual(errors, [])
            self.assertEqual(final["failed_capture_attempts"], 20)


if __name__ == "__main__":
    unittest.main()
