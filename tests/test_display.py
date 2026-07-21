import unittest

from rich.console import Console

from pokedex_cli import display
from pokedex_cli.domain.individuality import NATURES, STAT_KEYS, compute_stats


def enriched_row(**overrides):
    bases = {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90}
    ivs = {key: 31 for key in STAT_KEYS}
    nature = NATURES[3]  # adamant: +atk -spa
    level = 50
    row = {
        "id": 7,
        "species": "pikachu",
        "form": "regular",
        "types": ["electric"],
        "is_legendary": 0,
        "is_mythical": 0,
        "pokedex_id": 25,
        "shiny": 0,
        "caught_at": "2026-07-13T10:00:00+00:00",
        "in_team": 0,
        "generation": "generation-i",
        "flavor_text": None,
        "ball_slug": "pokeball",
        "level": level,
        "is_max_level": False,
        "experience_into_level": 0,
        "experience_for_next_level": 91,
        "form_data_exact": 1,
        "gender": "male",
        "ability": "static",
        "gender_rate": 4,
        "abilities": ["static", "lightning-rod"],
        "nature": nature,
        "ivs": ivs,
        "stats": compute_stats(bases, ivs, level, nature),
        **bases,
    }
    row.update(overrides)
    return row


def render(row) -> str:
    console = Console(width=140)
    with console.capture() as captured:
        display.render_vision_card(console, row, sprite=None)
    return captured.get()


def test_vision_shows_male_gender_symbol_and_nature_ability_line():
    output = render(enriched_row())
    assert "♂" in output
    assert "Naturaleza Firme (+Ataque −At. Esp.) · Habilidad Static" in output


def test_vision_shows_current_stats_with_subtle_base_and_iv_hint():
    output = render(enriched_row())
    # HP at level 50 with base 35, IV 31, adamant (neutral for hp) nature.
    expected_hp = compute_stats(
        {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90},
        {key: 31 for key in STAT_KEYS},
        50,
        NATURES[3],
    )["hp"]
    assert str(expected_hp) in output
    assert "base 35" in output
    assert "IV 31" in output


def test_vision_shows_female_symbol_and_neutral_nature_wording():
    row = enriched_row(gender="female", nature=NATURES[12])  # serious: neutral
    output = render(row)
    assert "♀" in output
    assert "Naturaleza Seria (neutra)" in output


def test_vision_shows_genderless_without_a_symbol():
    output = render(enriched_row(gender="genderless"))
    assert "♂" not in output
    assert "♀" not in output


def test_vision_shows_placeholder_ability_and_refresh_hint_when_unknown():
    row = enriched_row(ability=None, abilities=[], gender_rate=None, gender=None)
    output = render(row)
    assert "Habilidad —" in output
    assert "datos pendientes: pokedex refresh" in output


def test_list_table_shows_gender_symbol_after_the_name():
    row = {
        "id": 1,
        "pokedex_id": 25,
        "species": "pikachu",
        "form": "regular",
        "level": 10,
        "types": ["electric"],
        "shiny": 0,
        "in_team": 0,
        "caught_at": "2026-07-15",
        "gender": "female",
    }
    console = Console(width=140)
    with console.capture() as captured:
        display.render_list_table(console, [row])
    assert "♀" in captured.get()


def test_ranking_table_shows_current_and_base_totals_with_new_title():
    row = {
        "species": "pikachu",
        "form": "regular",
        "level": 50,
        "types": ["electric"],
        "gender": None,
        "total": 250,
        "base_total": 226,
        "in_team": 0,
    }
    console = Console(width=140)
    with console.capture() as captured:
        display.render_ranking_table(console, [row], missing=0)
    output = captured.get()
    assert "Ranking (stats actuales)" in output
    assert "250" in output
    assert "226" in output


def test_ranking_table_always_shows_team_column():
    rows = [
        {
            "species": "pikachu",
            "form": "regular",
            "level": 50,
            "types": ["electric"],
            "gender": None,
            "total": 250,
            "base_total": 226,
            "in_team": 1,
        },
        {
            "species": "charmander",
            "form": "regular",
            "level": 30,
            "types": ["fire"],
            "gender": None,
            "total": 180,
            "base_total": 176,
            "in_team": 0,
        },
    ]
    console = Console(width=160)
    with console.capture() as captured:
        display.render_ranking_table(console, rows, missing=0)
    output = captured.get()
    assert "Equipo" in output
    assert "⭐" in output


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
