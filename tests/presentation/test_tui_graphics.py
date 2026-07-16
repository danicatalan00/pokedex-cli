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


def test_half_blocks_roundtrip_preserves_the_pixel_grid():
    from pokedex_cli.presentation.tui.graphics import to_half_blocks

    grid = parse_ansi_sprite(_SPRITE)
    rendered = "\n".join(to_half_blocks(grid))
    assert parse_ansi_sprite(rendered) == grid


def test_downscale_by_two_halves_dimensions_and_keeps_silhouette():
    from pokedex_cli.presentation.tui.graphics import downscale

    red = (200, 0, 0)
    grid = [
        [red, red, None, None],
        [red, red, None, None],
        [None, None, red, red],
        [None, None, red, red],
    ]
    small = downscale(grid, 2)
    assert small == [[red, None], [None, red]]
    assert downscale(grid, 1) == grid


def test_fit_sprite_leaves_small_sprites_untouched_and_shrinks_large_ones():
    from pokedex_cli.presentation.tui.graphics import fit_sprite

    assert fit_sprite(_SPRITE, max_rows=10, max_columns=10) == _SPRITE

    # 40 filas de texto (80 píxeles de alto), 40 de ancho: no cabe en 16x50.
    red_row = "\x1b[38;2;200;0;0m" + "█" * 40 + "\x1b[0m"
    big = "\n".join([red_row] * 40)
    fitted = fit_sprite(big, max_rows=16, max_columns=50)
    fitted_grid = parse_ansi_sprite(fitted)
    assert (len(fitted_grid) + 1) // 2 <= 16
    assert len(fitted_grid[0]) <= 50
