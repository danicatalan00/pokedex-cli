"""Dev tool: render the boot-screen Pokéball as braille art with Pillow.

Draws a small procedural Pokéball, classifies the colour of each 2x4-pixel
braille cell, and writes `pokedex_cli/presentation/tui/assets.py` with the
resulting `POKEBALL_BRAILLE` tuple of rich-markup lines.

This is a *dev* tool, not part of the runtime package: it is never imported
by the CLI or the TUI, only run by hand to regenerate `assets.py` (which is
checked in, generated). It needs Pillow, which is deliberately NOT a runtime
dependency of pokedex-cli.

Usage:
    .venv/bin/python tools/render_braille.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "pokedex_cli" / "presentation" / "tui" / "assets.py"

CANVAS = 64
CENTER = CANVAS / 2
OUTER_RADIUS = 31.0
RING_WIDTH = 3.0
BAND_HALF_THICKNESS = 2.0
BUTTON_OUTER_RADIUS = 9.0
BUTTON_INNER_RADIUS = 6.0

# Rich colour names -> truecolor RGB (matches `rich.color.Color.parse(...)`).
GREY27 = (68, 68, 68)
RED3 = (215, 0, 0)
GREY93 = (238, 238, 238)
_NAME_BY_RGB = {GREY27: "grey27", RED3: "red3", GREY93: "grey93"}

RGB = tuple[int, int, int]


def _classify_pixel(x: int, y: int) -> RGB | None:
    """Colour of a single Pokéball pixel, or None if outside the ball."""
    dx = (x + 0.5) - CENTER
    dy = (y + 0.5) - CENTER
    distance = (dx * dx + dy * dy) ** 0.5
    if distance > OUTER_RADIUS:
        return None
    if distance <= BUTTON_INNER_RADIUS:
        return GREY93
    if distance <= BUTTON_OUTER_RADIUS:
        return GREY27
    if abs(dy) <= BAND_HALF_THICKNESS:
        return GREY27
    if distance > OUTER_RADIUS - RING_WIDTH:
        return GREY27
    return RED3 if dy < 0 else GREY93


def render_pokeball_image() -> "Image":
    try:
        from PIL import Image
    except ImportError as error:  # pragma: no cover - exercised manually, dev-only
        raise SystemExit("render_braille.py necesita Pillow: pip install pillow") from error

    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(CANVAS):
        for x in range(CANVAS):
            colour = _classify_pixel(x, y)
            if colour is not None:
                pixels[x, y] = (*colour, 255)
    return image


def _cell_colour(filled: list[list[RGB | None]], cell_y: int, cell_x: int) -> RGB | None:
    """Majority colour among the up-to-8 dots of one braille cell."""
    counts: dict[RGB, int] = {}
    for dy in range(4):
        for dx in range(2):
            y, x = cell_y + dy, cell_x + dx
            if y >= len(filled) or x >= len(filled[0]):
                continue
            colour = filled[y][x]
            if colour is not None:
                counts[colour] = counts.get(colour, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


_BRAILLE_BITS: dict[tuple[int, int], int] = {
    (0, 0): 0x01,
    (1, 0): 0x02,
    (2, 0): 0x04,
    (3, 0): 0x40,
    (0, 1): 0x08,
    (1, 1): 0x10,
    (2, 1): 0x20,
    (3, 1): 0x80,
}


def image_to_braille_lines(image: "Image") -> tuple[str, ...]:
    width, height = image.size
    pixels = image.convert("RGBA").load()
    grid: list[list[RGB | None]] = []
    for y in range(height):
        row: list[RGB | None] = []
        for x in range(width):
            r, g, b, a = pixels[x, y]
            row.append((r, g, b) if a else None)
        grid.append(row)

    lines: list[str] = []
    for cell_y in range(0, height, 4):
        spans: list[tuple[RGB | None, str]] = []
        current_colour: RGB | None | object = object()  # sentinel: never equal
        current_chars: list[str] = []
        for cell_x in range(0, width, 2):
            colour = _cell_colour(grid, cell_y, cell_x)
            code = 0
            if colour is not None:
                for (dy, dx), bit in _BRAILLE_BITS.items():
                    y, x = cell_y + dy, cell_x + dx
                    if y < height and x < width and grid[y][x] is not None:
                        code |= bit
            char = chr(0x2800 + code) if code else " "
            if colour != current_colour:
                if current_chars:
                    spans.append((current_colour, "".join(current_chars)))  # type: ignore[arg-type]
                current_colour = colour
                current_chars = [char]
            else:
                current_chars.append(char)
        if current_chars:
            spans.append((current_colour, "".join(current_chars)))  # type: ignore[arg-type]

        line = ""
        for colour, chars in spans:
            if colour is None:
                line += chars
            else:
                name = _NAME_BY_RGB.get(colour, "grey93")
                line += f"[{name}]{chars}[/]"
        lines.append(line.rstrip())
    return tuple(lines)


def render_assets_module(lines: tuple[str, ...]) -> str:
    body = ",\n".join(f"    {line!r}" for line in lines)
    return (
        '"""Boot-screen Pokéball art: 16 lines of rich markup over braille dots.\n'
        "\n"
        "GENERADO por tools/render_braille.py — no editar a mano. Para regenerar:\n"
        "\n"
        "    .venv/bin/python tools/render_braille.py\n"
        "\n"
        "The tool draws a procedural 64x64 Pokéball with Pillow, classifies the\n"
        "colour of each 2x4 braille cell, and emits one rich-markup line per row of\n"
        "cells (16 rows for a 64px-tall drawing).\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "POKEBALL_BRAILLE: tuple[str, ...] = (\n"
        f"{body},\n"
        ")\n"
    )


def main() -> int:
    image = render_pokeball_image()
    lines = image_to_braille_lines(image)
    OUTPUT_PATH.write_text(render_assets_module(lines))
    print(f"Escritas {len(lines)} líneas en {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
