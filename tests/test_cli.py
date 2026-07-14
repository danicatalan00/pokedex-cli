import argparse
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from pokedex_cli import cli, inventory, storage


class BallSelectionTests(unittest.TestCase):
    def setUp(self):
        self.state = inventory._new_inventory(datetime.now(timezone.utc))

    def test_direct_alias_selects_without_menu(self):
        ball = cli._choose_ball(argparse.Namespace(bola="ultra"), self.state)
        self.assertEqual(ball.slug, "ultraball")

    def test_menu_can_select_an_available_special_ball(self):
        with patch("builtins.input", return_value="3"):
            ball = cli._choose_ball(argparse.Namespace(bola=None), self.state)
        self.assertEqual(ball.slug, "ultraball")

    def test_menu_is_skipped_when_only_basic_ball_is_available(self):
        self.state["balls"] = {"superball": 0, "ultraball": 0, "masterball": 0}
        with patch("builtins.input") as prompt:
            ball = cli._choose_ball(argparse.Namespace(bola=None), self.state)
        self.assertEqual(ball.slug, "pokeball")
        prompt.assert_not_called()

    def test_direct_empty_special_ball_is_rejected(self):
        self.state["balls"]["masterball"] = 0
        with self.assertRaisesRegex(ValueError, "Masterball"):
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

        evolution = cli.build_parser().parse_args(
            ["demo-evolucion", "bulbasaur", "ivysaur", "--speed", "0.7"]
        )
        self.assertEqual(evolution.origen, "bulbasaur")
        self.assertEqual(evolution.destino, "ivysaur")
        self.assertEqual(evolution.speed, 0.7)

    def test_capture_probability_is_only_printed_in_debug_mode(self):
        normal = self._run_mock_capture(debug=False)
        debug = self._run_mock_capture(debug=True)
        self.assertNotIn("probabilidad de captura", normal)
        self.assertIn("Pokeball · probabilidad de captura: 17.6%", debug)

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
            patch.object(cli.storage, "insert_capture", return_value=1) as insert_capture,
            patch.object(cli.paths, "mark_last_seen_captured"),
            patch.object(cli.animation, "play_capture_animation") as play_animation,
            cli.console.capture() as captured,
        ):
            self.assertEqual(cli.cmd_capturar(args), 0)
            self.assertEqual(insert_capture.call_args.args[-1], "pokeball")
            self.assertEqual(play_animation.call_args.kwargs["ball_slug"], "pokeball")
        return captured.get()


class TeamSelectorTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(storage.SCHEMA)
        for index, species in enumerate(("bulbasaur", "charmander", "squirtle"), 1):
            self.conn.execute(
                "INSERT INTO captures (species, form, shiny, caught_at) "
                "VALUES (?, 'regular', 0, ?)",
                (species, f"2026-07-14T0{index}:00:00+00:00"),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_add_without_id_uses_interactive_selection(self):
        args = argparse.Namespace(accion="add", id=None)
        with (
            patch.object(cli.storage, "get_connection", return_value=self.conn),
            patch.object(cli, "_select_capture_for_team", return_value=2) as picker,
        ):
            self.assertEqual(cli.cmd_equipo(args), 0)
        picker.assert_called_once_with(self.conn)
        self.assertEqual(storage.get_capture(self.conn, 2)["in_team"], 1)

    def test_six_member_limit_prevents_opening_selector(self):
        for capture_id in (1, 2, 3):
            storage.set_team(self.conn, capture_id, True)
        for index in range(3):
            self.conn.execute(
                "INSERT INTO captures (species, form, shiny, caught_at, in_team) "
                "VALUES (?, 'regular', 0, ?, 1)",
                (f"extra-{index}", f"2026-07-14T1{index}:00:00+00:00"),
            )
        self.conn.commit()
        args = argparse.Namespace(accion="add", id=None)
        with (
            patch.object(cli.storage, "get_connection", return_value=self.conn),
            patch.object(cli, "_select_capture_for_team") as picker,
        ):
            self.assertEqual(cli.cmd_equipo(args), 1)
        picker.assert_not_called()

    def test_arrow_navigation_wraps_and_enter_selects(self):
        live = MagicMock()
        live.__enter__.return_value = live
        with (
            patch.object(cli.sys.stdin, "isatty", return_value=True),
            patch.object(cli, "_read_menu_key", side_effect=["down", "enter"]),
            patch.object(cli, "Live", return_value=live),
        ):
            # list_captures ordena por fecha descendente: 3, 2, 1.
            self.assertEqual(cli._select_capture_for_team(self.conn), 2)


class EvolutionHookTests(unittest.TestCase):
    def test_all_preexisting_pending_evolutions_run_before_wild_encounter(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(storage.SCHEMA)
        for species, target in (("bulbasaur", "ivysaur"), ("charmander", "charmeleon")):
            conn.execute(
                "INSERT INTO captures "
                "(species, form, shiny, caught_at, in_team, level, "
                "pending_evolution_species, pending_evolution_form) "
                "VALUES (?, 'regular', 0, 'now', 1, 16, ?, 'regular')",
                (species, target),
            )
        conn.commit()

        args = argparse.Namespace(generations="1-3")
        with (
            patch.object(cli.storage, "get_connection", return_value=conn),
            patch.object(cli, "_sync_training", return_value=(MagicMock(), ())),
            patch.object(cli.pokeapi, "fetch_species_data", return_value=None),
            patch.object(cli.animation, "play_evolution_animation") as animate,
            patch.object(cli.progression, "queue_current_evolution"),
            patch.object(cli.paths, "clear_last_seen") as clear_seen,
            patch.object(cli.krabby_bridge, "run_hook") as wild_hook,
            cli.console.capture() as output,
        ):
            self.assertEqual(cli.cmd_hook(args), 0)

        self.assertEqual(animate.call_count, 2)
        self.assertEqual(
            [call.args[3] for call in animate.call_args_list],
            ["ivysaur", "charmeleon"],
        )
        self.assertEqual(
            [storage.get_capture(conn, capture_id)["species"] for capture_id in (1, 2)],
            ["ivysaur", "charmeleon"],
        )
        clear_seen.assert_called_once()
        wild_hook.assert_not_called()
        self.assertNotIn("evoluciones pendientes", output.get())
        self.assertNotIn("Evolución 1 de", output.get())
        conn.close()


if __name__ == "__main__":
    unittest.main()
