import argparse
import contextlib
import io
import sqlite3
import sys
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

    def test_ball_menu_interrupt_defaults_to_basic_ball(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            ball = cli._choose_ball(argparse.Namespace(bola=None), self.state)
        self.assertEqual(ball.slug, "pokeball")

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
        use_case = MagicMock()
        use_case.execute.return_value = cli.capture_application.CaptureResult(
            cli.capture_application.CaptureStatus.CAUGHT,
            chance=0.176,
            capture_id=1,
        )
        species_data = MagicMock()
        species_data.execute.return_value = cache
        with (
            patch.object(cli.composition, "read_encounter", return_value=last_seen),
            patch.object(inventory, "sync_activity", return_value=sync),
            patch.object(cli, "_species_data_use_case", return_value=species_data),
            patch.object(cli, "_capture_encounter_use_case", return_value=use_case),
            patch.object(cli.animation, "play_capture_animation") as play_animation,
            cli.console.capture() as captured,
        ):
            self.assertEqual(cli.cmd_capturar(args), 0)
            self.assertEqual(use_case.execute.call_args.args[0].ball_slug, "pokeball")
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
        use_case = MagicMock()
        use_case.execute.return_value = cli.team_application.TeamResult(
            cli.team_application.TeamStatus.ADDED, 2
        )
        with (
            patch.object(cli, "_select_capture_for_team", return_value=2) as picker,
            patch.object(cli, "_manage_team_use_case", return_value=use_case),
        ):
            self.assertEqual(cli.cmd_equipo(args), 0)
        picker.assert_called_once_with()
        use_case.execute.assert_called_once_with(cli.team_application.TeamAction.ADD, 2)

    def _patch_collection(self):
        rows = [dict(row) for row in storage.list_captures(self.conn)]
        for row in rows:
            row["types"] = None
        queries = MagicMock()
        queries.available_for_team.return_value = rows
        return patch.object(cli, "_collection_queries", return_value=queries)

    def test_empty_collection_cancels_selection(self):
        queries = MagicMock()
        queries.available_for_team.return_value = []
        with patch.object(cli, "_collection_queries", return_value=queries):
            self.assertIsNone(cli._select_capture_for_team())

    def test_arrow_navigation_wraps_and_enter_selects(self):
        live = MagicMock()
        live.__enter__.return_value = live
        with (
            self._patch_collection(),
            patch.object(cli.sys.stdin, "isatty", return_value=True),
            patch.object(cli, "_read_menu_key", side_effect=["down", "enter"]),
            patch.object(cli, "Live", return_value=live),
        ):
            # list_captures ordena por fecha descendente: 3, 2, 1.
            self.assertEqual(cli._select_capture_for_team(), 2)

    def test_non_tty_interrupt_cancels_selection(self):
        with (
            self._patch_collection(),
            patch.object(cli.sys.stdin, "isatty", return_value=False),
            patch("builtins.input", side_effect=KeyboardInterrupt),
        ):
            self.assertIsNone(cli._select_capture_for_team())

    def test_tty_interrupt_cancels_selection(self):
        live = MagicMock()
        live.__enter__.return_value = live
        with (
            self._patch_collection(),
            patch.object(cli.sys.stdin, "isatty", return_value=True),
            patch.object(cli, "_read_menu_key", side_effect=KeyboardInterrupt),
            patch.object(cli, "Live", return_value=live),
        ):
            self.assertIsNone(cli._select_capture_for_team())


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

        processor = MagicMock()
        processor.pending.return_value = (
            cli.evolution_application.PendingEvolution(
                1, "bulbasaur", "regular", "ivysaur", "regular", False, 16
            ),
            cli.evolution_application.PendingEvolution(
                2, "charmander", "regular", "charmeleon", "regular", False, 16
            ),
        )
        processor.execute.return_value = processor.pending.return_value

        args = argparse.Namespace(generations="1-3")
        with (
            patch.object(cli, "_process_evolutions_use_case", return_value=processor),
            patch.object(cli, "_sync_training", return_value=(MagicMock(), ())),
            patch.object(cli, "_species_data_use_case") as species_data,
            patch.object(cli.animation, "play_evolution_animation") as animate,
            patch.object(cli.composition, "clear_encounter") as clear_seen,
            patch.object(cli.composition, "run_wild_encounter") as wild_hook,
            cli.console.capture() as output,
        ):
            self.assertEqual(cli.cmd_hook(args), 0)

        self.assertEqual(animate.call_count, 2)
        self.assertEqual(
            [call.args[3] for call in animate.call_args_list],
            ["ivysaur", "charmeleon"],
        )
        processor.execute.assert_called_once_with([1, 2])
        clear_seen.assert_called_once()
        wild_hook.assert_not_called()
        self.assertEqual(species_data.return_value.execute.call_count, 2)
        self.assertNotIn("evoluciones pendientes", output.get())
        self.assertNotIn("Evolución 1 de", output.get())
        conn.close()


class HookFailureBoundaryTests(unittest.TestCase):
    def test_recoverable_hook_failure_never_breaks_shell_startup(self):
        stderr = io.StringIO()
        with (
            patch.object(cli, "cmd_hook", side_effect=OSError("temporary lock failure")),
            patch.object(sys, "argv", ["pokedex", "hook"]),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(cli.main(), 0)

        output = stderr.getvalue()
        self.assertNotIn("Traceback", output)
        self.assertIn("temporary lock failure", output)

    def test_recoverable_database_failure_is_clean_for_manual_command(self):
        stderr = io.StringIO()
        with (
            patch.object(cli, "cmd_bolsas", side_effect=sqlite3.DatabaseError("corrupt")),
            patch.object(sys, "argv", ["pokedex", "bolsas"]),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(cli.main(), 1)
        self.assertIn("corrupt", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
