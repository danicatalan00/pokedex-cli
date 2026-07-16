"""Boot-screen Pokéball art: 16 lines of rich markup over braille dots.

GENERADO por tools/render_braille.py — no editar a mano. Para regenerar:

    .venv/bin/python tools/render_braille.py

The tool draws a procedural 64x64 Pokéball with Pillow, classifies the
colour of each 2x4 braille cell, and emits one rich-markup line per row of
cells (16 rows for a 64px-tall drawing).
"""

from __future__ import annotations

POKEBALL_BRAILLE: tuple[str, ...] = (
    "          [grey27]⣀⣤⣤⣶⣶⣶⣶⣶⣶⣤⣤⣀[/]",
    "      [grey27]⢀⣤⣶⣿⣿[/][red3]⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⣿⣶⣤⡀[/]",
    "    [grey27]⢀⣴⣿[/][red3]⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⣦⡀[/]",
    "   [grey27]⣴⣿[/][red3]⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⣦[/]",
    "  [grey27]⣼⣿[/][red3]⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⣧[/]",
    " [grey27]⣼⣿[/][red3]⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⣧[/]",
    "[grey27]⢠⣿[/][red3]⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⣿⣿⣿⣿⣿[/][red3]⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⡄[/]",
    "[grey27]⢸⣿[/][red3]⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⣿[/][grey93]⣿⣿⣿⣿⣿⣿[/][grey27]⣿⣿[/][red3]⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⡇[/]",
    "[grey27]⢸⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey93]⣿⣿⣿⣿⣿⣿[/][grey27]⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇[/]",
    "[grey27]⠘⣿[/][grey93]⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⣿[/][grey93]⣿⣿[/][grey27]⣿⣿[/][grey93]⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⠃[/]",
    " [grey27]⢻⣿[/][grey93]⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⡟[/]",
    "  [grey27]⢻⣿[/][grey93]⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⡟[/]",
    "   [grey27]⠻⣿[/][grey93]⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⠟[/]",
    "    [grey27]⠈⠻⣿[/][grey93]⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⠟⠁[/]",
    "      [grey27]⠈⠛⠿⣿[/][grey93]⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿[/][grey27]⣿⠿⠛⠁[/]",
    "          [grey27]⠉⠛⠛⠿⠿⠿⠿⠿⠿⠛⠛⠉[/]",
)
