import unittest

from rich.console import Console

from pokedex_cli import display


class VisionCaptureBallTests(unittest.TestCase):
    def test_vision_shows_the_ball_used(self):
        row = {
            "id": 7,
            "species": "mewtwo",
            "form": "regular",
            "types": None,
            "is_legendary": 1,
            "is_mythical": 0,
            "pokedex_id": 150,
            "shiny": 0,
            "caught_at": "2026-07-13T10:00:00+00:00",
            "in_team": 0,
            "generation": "generation-i",
            "flavor_text": None,
            "ball_slug": "masterball",
            "level": 5,
            "is_max_level": False,
            "experience_into_level": 0,
            "experience_for_next_level": 91,
        }
        console = Console(width=140)
        with console.capture() as captured:
            display.render_vision_card(console, row, sprite=None)
        self.assertIn("Masterball", captured.get())

    def test_vision_treats_legacy_capture_as_basic_ball(self):
        row = {
            "id": 1,
            "species": "pikachu",
            "form": "regular",
            "types": None,
            "is_legendary": 0,
            "is_mythical": 0,
            "pokedex_id": 25,
            "shiny": 0,
            "caught_at": "2026-07-13T09:00:00+00:00",
            "in_team": 0,
            "generation": "generation-i",
            "flavor_text": None,
            "level": 5,
            "is_max_level": False,
            "experience_into_level": 0,
            "experience_for_next_level": 91,
        }
        console = Console(width=140)
        with console.capture() as captured:
            display.render_vision_card(console, row, sprite=None)
        self.assertIn("Pokeball", captured.get())


if __name__ == "__main__":
    unittest.main()
