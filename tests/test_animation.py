import random
import unittest
from io import StringIO
from unittest.mock import MagicMock

from rich.console import Console

from pokedex_cli import animation


class BallAnimationTests(unittest.TestCase):
    def test_non_tty_playback_skips_decorative_external_work(self):
        renderer = MagicMock()
        console = Console(file=StringIO(), force_terminal=False)

        animation.play_capture_animation(
            console,
            "pikachu",
            "regular",
            False,
            True,
            sprite_renderer=renderer,
        )
        animation.play_evolution_animation(
            console,
            "bulbasaur",
            "regular",
            "ivysaur",
            "regular",
            False,
            sprite_renderer=renderer,
        )

        renderer.capture_sprite.assert_not_called()
        renderer.render_sprite.assert_not_called()

    def test_public_frame_generation_has_stable_dimensions_and_final_click(self):
        sprite = "\x1b[38;2;100;200;100mP\x1b[0m"
        frames = animation.generate_capture_frames(
            sprite, caught=True, ball_slug="ultraball", rng=random.Random(1)
        )
        dimensions = {
            (len(frame.plain.splitlines()), max(map(len, frame.plain.splitlines())))
            for frame, _ in frames
        }
        self.assertEqual(len(dimensions), 1)
        self.assertNotIn("P", frames[-1][0].plain)
        self.assertIn("●", frames[-1][0].plain)

    def test_public_frame_generation_falls_back_without_sprite(self):
        frames = animation.generate_capture_frames(
            None, caught=False, ball_slug="pokeball", rng=random.Random(1)
        )
        self.assertGreater(len(frames), 1)
        self.assertIn("se soltó", frames[-1][0].plain)

    def test_effects_grow_with_ball_tier(self):
        grid = [[animation.Cell() for _ in range(25)] for _ in range(12)]
        grid[5][12] = animation.Cell("P", (100, 200, 100))

        frame_counts = []
        for slug in ("pokeball", "superball", "ultraball", "masterball"):
            style = animation._ball_style(slug)
            frames = list(
                animation._frames_composited(grid, caught=True, rng=random.Random(1), style=style)
            )
            frame_counts.append(len(frames))

        self.assertEqual(frame_counts, sorted(frame_counts))
        self.assertEqual(len(set(frame_counts)), 4)

    def test_each_ball_has_distinct_colors_and_pattern(self):
        styles = [
            animation._ball_style(slug)
            for slug in ("pokeball", "superball", "ultraball", "masterball")
        ]
        self.assertEqual(len({style.top for style in styles}), 4)
        self.assertEqual(len({style.pattern for style in styles}), 4)

    def test_unknown_ball_falls_back_to_basic_ball(self):
        self.assertEqual(animation._ball_style("inventada"), animation._ball_style("pokeball"))

    def test_ball_uses_compact_block_footprint(self):
        grid = [[animation.Cell() for _ in range(11)] for _ in range(6)]
        style = animation._ball_style("pokeball")

        animation._draw_pokeball(grid, 2, 5, style)

        occupied = {
            (row, col)
            for row, cells in enumerate(grid)
            for col, cell in enumerate(cells)
            if cell.ch != " " or cell.bg is not None
        }
        self.assertEqual(occupied, {(r, c) for r in (2, 3) for c in range(4, 7)})
        self.assertEqual(grid[2][5].bg, style.top)
        self.assertEqual(grid[3][4].ch, "▜")
        self.assertEqual(grid[3][5].fg, animation._BALL_WHITE)
        self.assertEqual(grid[3][5].bg, style.accent)

    def test_evolution_alternates_silhouettes_and_ends_in_color(self):
        old = [[animation.Cell("A", (20, 120, 20))]]
        new = [[animation.Cell("B", (200, 40, 40))]]
        frames = list(animation._evolution_frames(old, new, speed=0.5))

        self.assertGreaterEqual(len(frames), 13)
        rendered = [frame.plain for frame, _ in frames]
        self.assertIn("A", rendered[0])
        self.assertIn("B", rendered[-1])
        self.assertTrue(all(delay > 0 for _, delay in frames))

    def test_public_evolution_frames_fit_different_sprite_sizes(self):
        old_sprite = "A"
        new_sprite = "BB\nBB\nBB"
        frames = animation.generate_evolution_frames(old_sprite, new_sprite, speed=0.5)
        dimensions = {
            (len(frame.plain.splitlines()), max(map(len, frame.plain.splitlines())))
            for frame, _ in frames
        }
        self.assertEqual(len(dimensions), 1)
        self.assertIn("BB", frames[-1][0].plain)


if __name__ == "__main__":
    unittest.main()
