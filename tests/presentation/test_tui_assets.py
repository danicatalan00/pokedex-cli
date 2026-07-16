import re

from rich.text import Text

from pokedex_cli.presentation.tui.assets import POKEBALL_BRAILLE

_ALLOWED = re.compile(r"^[⠀-⣿ \[\]/a-zA-Z0-9]*$")


def test_pokeball_asset_has_sixteen_lines():
    assert len(POKEBALL_BRAILLE) == 16


def test_every_line_only_uses_braille_dots_spaces_or_rich_markup_tags():
    for line in POKEBALL_BRAILLE:
        assert _ALLOWED.match(line), line


def test_every_line_parses_as_valid_rich_markup():
    for line in POKEBALL_BRAILLE:
        Text.from_markup(line)  # must not raise
