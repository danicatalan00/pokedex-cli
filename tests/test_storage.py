import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pokedex_cli import storage


class CaptureBallStorageTests(unittest.TestCase):
    def test_migration_defaults_old_captures_and_saves_new_ball(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "pokedex.db"
            legacy = sqlite3.connect(db_path)
            legacy.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    species TEXT NOT NULL,
                    form TEXT NOT NULL DEFAULT 'regular',
                    shiny INTEGER NOT NULL DEFAULT 0,
                    caught_at TEXT NOT NULL,
                    nickname TEXT,
                    in_team INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            legacy.execute(
                "INSERT INTO captures (species, form, shiny, caught_at) "
                "VALUES ('pikachu', 'regular', 0, '2026-07-13T09:00:00+00:00')"
            )
            legacy.commit()
            legacy.close()

            with patch.object(storage, "DB_PATH", db_path):
                conn = storage.get_connection()
                old = storage.get_capture(conn, 1)
                self.assertEqual(old["ball_slug"], "pokeball")

                capture_id = storage.insert_capture(
                    conn,
                    "mewtwo",
                    "regular",
                    False,
                    "2026-07-13T10:00:00+00:00",
                    "masterball",
                )
                new = storage.get_capture(conn, capture_id)
                self.assertEqual(new["ball_slug"], "masterball")
                conn.close()

    def test_migration_renames_legacy_ball_slugs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "pokedex.db"
            legacy = sqlite3.connect(db_path)
            legacy.execute(
                """
                CREATE TABLE captures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    species TEXT NOT NULL,
                    form TEXT NOT NULL DEFAULT 'regular',
                    shiny INTEGER NOT NULL DEFAULT 0,
                    caught_at TEXT NOT NULL,
                    ball_slug TEXT NOT NULL DEFAULT 'pokebola',
                    nickname TEXT,
                    in_team INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            for slug in ("pokebola", "superbola", "ultrabola", "masterbola"):
                legacy.execute(
                    "INSERT INTO captures (species, form, shiny, caught_at, ball_slug) "
                    "VALUES ('pikachu', 'regular', 0, '2026-07-13T09:00:00+00:00', ?)",
                    (slug,),
                )
            legacy.commit()
            legacy.close()

            with patch.object(storage, "DB_PATH", db_path):
                conn = storage.get_connection()
                slugs = {r["ball_slug"] for r in storage.list_captures(conn)}
                self.assertEqual(
                    slugs, {"pokeball", "superball", "ultraball", "masterball"}
                )
                conn.close()


if __name__ == "__main__":
    unittest.main()
