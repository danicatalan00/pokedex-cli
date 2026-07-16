"""Pure ANSI-sprite parsing and braille-silhouette rendering for the TUI.

Both functions here are free of I/O and subprocess calls: they only ever see
a string already produced by `krabby` (or a synthetic test fixture shaped the
same way), which keeps them fully unit-testable. The parser and the braille
bit table were validated against real `krabby` output before being adapted
here with types and docstrings.
"""

from __future__ import annotations

import re

_SGR = re.compile(r"\x1b\[([0-9;]*)m")

# A pixel is an RGB triple, or ``None`` for "transparent" (no colour set).
Pixel = tuple[int, int, int] | None
PixelGrid = list[list[Pixel]]


def parse_ansi_sprite(text: str) -> PixelGrid:
    """Convert krabby's half-block ANSI art into a grid of pixels.

    Each text cell packs two pixels: ``▀`` paints its top half with the
    current foreground colour and its bottom half with the background;
    ``▄`` is the mirror image; ``█`` is foreground top and bottom; any other
    character (typically a space) is background top and bottom. Only the
    truecolor SGR codes krabby emits are handled: ``38;2;r;g;b`` (set
    foreground), ``48;2;r;g;b`` (set background), ``0`` (reset both),
    ``39``/``49`` (reset foreground/background only).

    Rows made entirely of transparent pixels at the very top or bottom of
    the sprite are trimmed, and every row is padded to the widest row so the
    grid is rectangular.
    """
    lines = text.split("\n")
    top_rows: list[list[Pixel]] = []
    bottom_rows: list[list[Pixel]] = []
    for line in lines:
        fg: Pixel = None
        bg: Pixel = None
        top: list[Pixel] = []
        bottom: list[Pixel] = []
        i = 0
        while i < len(line):
            match = _SGR.match(line, i)
            if match:
                raw_params = match.group(1)
                parameters = [int(p) for p in raw_params.split(";") if p != ""] or [0]
                j = 0
                while j < len(parameters):
                    code = parameters[j]
                    if code == 0:
                        fg = bg = None
                        j += 1
                    elif code == 38 and parameters[j + 1 : j + 2] == [2]:
                        fg = (parameters[j + 2], parameters[j + 3], parameters[j + 4])
                        j += 5
                    elif code == 48 and parameters[j + 1 : j + 2] == [2]:
                        bg = (parameters[j + 2], parameters[j + 3], parameters[j + 4])
                        j += 5
                    elif code == 39:
                        fg = None
                        j += 1
                    elif code == 49:
                        bg = None
                        j += 1
                    else:
                        j += 1
                i = match.end()
                continue
            char = line[i]
            if char == "▀":
                top.append(fg)
                bottom.append(bg)
            elif char == "▄":
                top.append(bg)
                bottom.append(fg)
            elif char == "█":
                top.append(fg)
                bottom.append(fg)
            else:  # space or anything else krabby never emits
                top.append(bg)
                bottom.append(bg)
            i += 1
        top_rows.append(top)
        bottom_rows.append(bottom)

    grid: PixelGrid = []
    width = max((len(row) for row in top_rows), default=0)
    for top, bottom in zip(top_rows, bottom_rows):
        grid.append(top + [None] * (width - len(top)))
        grid.append(bottom + [None] * (width - len(bottom)))

    while grid and all(pixel is None for pixel in grid[0]):
        grid.pop(0)
    while grid and all(pixel is None for pixel in grid[-1]):
        grid.pop()
    return grid


# A braille character is an 8-dot cell, 2 columns by 4 rows. Each (row,
# column) position maps to one bit of the U+2800 block, per the Unicode
# braille pattern layout.
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


def to_braille_silhouette(grid: PixelGrid, scale: int = 2) -> list[str]:
    """Render a pixel grid as a braille silhouette (filled vs. transparent).

    ``scale`` duplicates every pixel into a ``scale`` x ``scale`` block
    first, so at the default of 2 the silhouette occupies roughly the same
    on-screen footprint as the original half-block sprite (each braille
    character packs a 2x4 dot cell, twice as dense as a half-block cell).
    Trailing empty columns in each row are stripped; a fully empty row
    becomes an empty string rather than a row of literal spaces.
    """
    if not grid:
        return []
    height, width = len(grid), len(grid[0])
    filled = [
        [grid[y // scale][x // scale] is not None for x in range(width * scale)]
        for y in range(height * scale)
    ]
    rows: list[str] = []
    for cell_y in range(0, len(filled), 4):
        row_chars: list[str] = []
        for cell_x in range(0, len(filled[0]), 2):
            code = 0
            for (dy, dx), bit in _BRAILLE_BITS.items():
                y, x = cell_y + dy, cell_x + dx
                if y < len(filled) and x < len(filled[0]) and filled[y][x]:
                    code |= bit
            row_chars.append(chr(0x2800 + code) if code else " ")
        rows.append("".join(row_chars).rstrip())
    return rows
