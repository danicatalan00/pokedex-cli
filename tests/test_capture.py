import unittest

from pokedex_cli.capture import catch_chance


class CatchChanceTests(unittest.TestCase):
    def test_default_multiplier_preserves_old_probability(self):
        self.assertAlmostEqual(catch_chance(127), 127 / 255)

    def test_ball_multiplier_is_applied_and_clamped(self):
        self.assertAlmostEqual(catch_chance(30, ball_multiplier=2.0), 60 / 255)
        self.assertEqual(catch_chance(255, ball_multiplier=2.0), 1.0)

    def test_master_ball_sentinel_is_guaranteed(self):
        self.assertEqual(catch_chance(3, ball_multiplier=255), 1.0)
        self.assertEqual(catch_chance(None, ball_multiplier=255), 1.0)


if __name__ == "__main__":
    unittest.main()
