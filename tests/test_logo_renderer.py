from collections import Counter

from tools.render_logo import LogoConfig, krabby_command, parse_sprite, theme_from_palette


def test_parser_preserves_half_block_colours_and_visible_palette() -> None:
    sprite = parse_sprite("\x1b[38;2;115;206;255m▄\x1b[0m\n")

    assert sprite.width == 1
    assert sprite.height == 1
    assert sprite.cells[0].character == "▄"
    assert sprite.cells[0].foreground == (115, 206, 255)
    assert sprite.palette == Counter({(115, 206, 255): 1})


def test_theme_uses_the_dominant_chromatic_sprite_colours() -> None:
    palette = Counter(
        {
            (115, 206, 255): 120,
            (247, 140, 66): 45,
            (255, 255, 255): 30,
            (0, 0, 0): 20,
        }
    )

    theme = theme_from_palette(palette)

    assert theme.accent == "#73ceff"
    assert theme.background_start.startswith("#")
    assert theme.background_end.startswith("#")
    assert theme.background_start != theme.background_end


def test_krabby_command_reflects_name_form_and_shiny_configuration() -> None:
    config = LogoConfig(pokemon="charizard", form="mega-x", shiny=True)

    assert krabby_command(config) == [
        "krabby",
        "name",
        "charizard",
        "--form",
        "mega-x",
        "--shiny",
        "--no-title",
    ]
