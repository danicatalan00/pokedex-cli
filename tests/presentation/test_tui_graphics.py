from pokedex_cli.presentation.tui.graphics import parse_ansi_sprite, to_braille_silhouette

# A tiny synthetic 2-line half-block sprite exercising every code path the
# parser must handle: ``▀``/``▄``/``█``/space, truecolor fg (38;2) and bg
# (48;2), and a full reset (0). Not real krabby output, but shaped exactly
# like it (this is what the validated prototype was checked against).
_LINE_1 = "\x1b[38;2;200;10;10m▀\x1b[48;2;10;10;200m \x1b[0m"
_LINE_2 = "\x1b[38;2;0;150;0m▄ \x1b[38;2;255;255;0m█\x1b[0m"
_SPRITE = _LINE_1 + "\n" + _LINE_2


def test_parses_half_block_truecolor_sprite_into_an_exact_pixel_grid():
    grid = parse_ansi_sprite(_SPRITE)
    assert grid == [
        [(200, 10, 10), (10, 10, 200), None],
        [None, (10, 10, 200), None],
        [None, None, (255, 255, 0)],
        [(0, 150, 0), None, (255, 255, 0)],
    ]


def test_braille_silhouette_of_the_synthetic_sprite_matches_exactly():
    grid = parse_ansi_sprite(_SPRITE)
    assert to_braille_silhouette(grid) == ["⠛⣿", "⣤ ⣿"]


def test_empty_sprite_text_yields_an_empty_grid_and_empty_silhouette():
    assert parse_ansi_sprite("") == []
    assert to_braille_silhouette([]) == []


def test_a_sprite_that_is_entirely_transparent_trims_down_to_an_empty_grid():
    assert parse_ansi_sprite("\x1b[0m  \n  \x1b[0m") == []


def test_reset_codes_39_and_49_clear_only_foreground_or_background():
    # fg red, bg blue, then 39 clears fg (leaving bg blue), then 49 clears bg.
    sprite = "\x1b[38;2;255;0;0m\x1b[48;2;0;0;255m▀\x1b[39m▀\x1b[49m▀"
    grid = parse_ansi_sprite(sprite)
    # cell 0: fg=red, bg=blue -> top=red, bottom=blue
    # cell 1: fg cleared (None), bg still blue -> top=None, bottom=blue
    # cell 2: bg cleared too -> top=None, bottom=None
    assert grid[0] == [(255, 0, 0), None, None]
    assert grid[1] == [(0, 0, 255), (0, 0, 255), None]
