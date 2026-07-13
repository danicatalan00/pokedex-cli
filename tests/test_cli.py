import argparse
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from pokedex_cli import cli, inventory


class BallSelectionTests(unittest.TestCase):
    def setUp(self):
        self.state = inventory._new_inventory(datetime.now(timezone.utc))

    def test_direct_alias_selects_without_menu(self):
        ball = cli._choose_ball(argparse.Namespace(bola="ultra"), self.state)
        self.assertEqual(ball.slug, "ultrabola")

    def test_menu_can_select_an_available_special_ball(self):
        with patch("builtins.input", return_value="3"):
            ball = cli._choose_ball(argparse.Namespace(bola=None), self.state)
        self.assertEqual(ball.slug, "ultrabola")

    def test_menu_is_skipped_when_only_basic_ball_is_available(self):
        self.state["balls"] = {"superbola": 0, "ultrabola": 0, "masterbola": 0}
        with patch("builtins.input") as prompt:
            ball = cli._choose_ball(argparse.Namespace(bola=None), self.state)
        self.assertEqual(ball.slug, "pokebola")
        prompt.assert_not_called()

    def test_direct_empty_special_ball_is_rejected(self):
        self.state["balls"]["masterbola"] = 0
        with self.assertRaisesRegex(ValueError, "Masterbola"):
            cli._choose_ball(argparse.Namespace(bola="master"), self.state)

    def test_new_detail_flags_are_optional(self):
        capture_args = cli.build_parser().parse_args(["capturar"])
        bag_args = cli.build_parser().parse_args(["bolsas"])
        self.assertFalse(capture_args.debug)
        self.assertFalse(bag_args.info)

        capture_debug = cli.build_parser().parse_args(["capturar", "--debug"])
        bag_info = cli.build_parser().parse_args(["bolsas", "--info"])
        self.assertTrue(capture_debug.debug)
        self.assertTrue(bag_info.info)

    def test_capture_probability_is_only_printed_in_debug_mode(self):
        normal = self._run_mock_capture(debug=False)
        debug = self._run_mock_capture(debug=True)
        self.assertNotIn("probabilidad de captura", normal)
        self.assertIn("Pokébola · probabilidad de captura: 17.6%", debug)

    def _run_mock_capture(self, debug: bool) -> str:
        last_seen = {
            "species": "pikachu",
            "form": "regular",
            "shiny": False,
            "captured": False,
        }
        cache = {
            "capture_rate": 190,
            "is_legendary": 0,
            "is_mythical": 0,
            "spe": 90,
            "pokedex_id": 25,
        }
        sync = inventory.SyncResult(self.state, (), 0, 0)
        args = argparse.Namespace(bola="poke", debug=debug)
        with (
            patch.object(cli.paths, "read_last_seen", return_value=last_seen),
            patch.object(cli.inventory, "sync_activity", return_value=sync),
            patch.object(cli.inventory, "consume_ball"),
            patch.object(cli.storage, "get_connection"),
            patch.object(cli.storage, "get_species_cache", return_value=cache),
            patch.object(cli.capture, "catch_chance", return_value=0.176),
            patch.object(cli.capture, "roll_capture", return_value=True),
            patch.object(cli.storage, "insert_capture", return_value=1),
            patch.object(cli.paths, "mark_last_seen_captured"),
            patch.object(cli.animation, "play_capture_animation"),
            cli.console.capture() as captured,
        ):
            self.assertEqual(cli.cmd_capturar(args), 0)
        return captured.get()


if __name__ == "__main__":
    unittest.main()
