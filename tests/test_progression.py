import json
import random
import sqlite3
import unittest

from pokedex_cli import progression, storage


class ExperienceCurveTests(unittest.TestCase):
    def test_official_growth_curve_landmarks(self):
        expected_at_50 = {
            "fast": 100000,
            "medium": 125000,
            "medium-slow": 117360,
            "slow": 156250,
            "erratic": 125000,
            "fluctuating": 142500,
        }
        for growth, expected in expected_at_50.items():
            with self.subTest(growth=growth):
                self.assertEqual(progression.experience_for_level(growth, 50), expected)

    def test_generation_one_commit_formula(self):
        self.assertEqual(progression.commit_experience(5, 64), 45)
        self.assertEqual(progression.commit_experience(16, 64), 146)

    def test_diff_size_scales_and_caps_commit_difficulty(self):
        self.assertEqual(progression.commit_difficulty(0), 1.0)
        self.assertEqual(progression.commit_difficulty(100), 3.0)
        self.assertEqual(progression.commit_difficulty(2688), 50.0)
        self.assertEqual(progression.commit_experience(5, 64, 2688), 45 * 50)
        total = progression.experience_for_level("medium-slow", 5) + 45 * 50
        self.assertEqual(progression.level_for_experience("medium-slow", total), 15)


class CommitTrainingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(storage.SCHEMA)
        self.conn.execute(
            "INSERT INTO species_cache "
            "(species, form, growth_rate, base_experience, level_evolutions, fetched_at) "
            "VALUES (?, 'regular', ?, ?, ?, 'now')",
            (
                "bulbasaur",
                "medium-slow",
                64,
                json.dumps([{"species": "ivysaur", "form": "regular", "min_level": 16}]),
            ),
        )
        exp_15 = progression.experience_for_level("medium-slow", 15)
        self.conn.execute(
            "INSERT INTO captures "
            "(species, form, shiny, caught_at, in_team, level, experience) "
            "VALUES ('bulbasaur', 'regular', 0, 'now', 1, 15, ?)",
            (exp_15,),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_commits_gain_experience_and_queue_level_evolution(self):
        results = progression.apply_commit_experience(self.conn, 4, rng=random.Random(7))
        pokemon = self.conn.execute("SELECT * FROM captures").fetchone()
        self.assertEqual(results[0].experience, 4 * progression.commit_experience(15, 64))
        self.assertEqual(pokemon["level"], 16)
        self.assertEqual(pokemon["pending_evolution_species"], "ivysaur")

    def test_completed_evolution_preserves_progress_and_team(self):
        progression.apply_commit_experience(self.conn, 4, rng=random.Random(7))
        before = self.conn.execute("SELECT * FROM captures").fetchone()
        storage.complete_evolution(self.conn, before["id"])
        after = self.conn.execute("SELECT * FROM captures").fetchone()
        self.assertEqual(after["species"], "ivysaur")
        self.assertEqual(after["level"], before["level"])
        self.assertEqual(after["experience"], before["experience"])
        self.assertEqual(after["in_team"], 1)
        self.assertIsNone(after["pending_evolution_species"])

    def test_reconciles_an_eligible_evolution_without_new_commits(self):
        self.conn.execute(
            "UPDATE captures SET level = 16, experience = ?",
            (progression.experience_for_level("medium-slow", 16),),
        )
        self.conn.commit()

        results = progression.apply_commit_experience(self.conn, 0, rng=random.Random(7))

        pokemon = self.conn.execute("SELECT * FROM captures").fetchone()
        self.assertEqual(results, ())
        self.assertEqual(pokemon["pending_evolution_species"], "ivysaur")


if __name__ == "__main__":
    unittest.main()
