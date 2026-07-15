"""Render the README logo from a configurable Krabby sprite."""

from __future__ import annotations

import argparse
import colorsys
import html
import re
import subprocess
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

RGB = tuple[int, int, int]
ANSI = re.compile(r"\x1b\[([0-9;]*)m")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "docs" / "assets" / "logo.toml"
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "assets" / "rotom-logo.svg"


@dataclass(frozen=True)
class LogoConfig:
    pokemon: str = "rotom"
    form: str = "regular"
    shiny: bool = False
    title: str = "POKÉDEX CLI"
    tagline: str = "catch · train · evolve"


@dataclass(frozen=True)
class Cell:
    row: int
    column: int
    character: str
    foreground: RGB | None
    background: RGB | None


@dataclass(frozen=True)
class Sprite:
    cells: tuple[Cell, ...]
    palette: Counter[RGB]
    width: int
    height: int


@dataclass(frozen=True)
class Theme:
    accent: str
    background_start: str
    background_end: str


def _hex(rgb: RGB) -> str:
    return "#" + "".join(f"{channel:02x}" for channel in rgb)


def _dark_colour(rgb: RGB, lightness: float) -> RGB:
    hue, _, saturation = colorsys.rgb_to_hls(*(channel / 255 for channel in rgb))
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, min(0.72, max(0.38, saturation)))
    return round(red * 255), round(green * 255), round(blue * 255)


def theme_from_palette(palette: Counter[RGB]) -> Theme:
    candidates: list[tuple[float, RGB, float]] = []
    for rgb, count in palette.items():
        _, lightness, saturation = colorsys.rgb_to_hls(*(channel / 255 for channel in rgb))
        if saturation >= 0.18 and 0.06 < lightness < 0.96:
            candidates.append((count * (0.5 + saturation), rgb, saturation))

    candidates.sort(reverse=True)
    if not candidates:
        return Theme("#73ceff", "#111827", "#0b1020")

    primary = candidates[0][1]
    secondary = next((entry[1] for entry in candidates[1:] if entry[1] != primary), primary)
    return Theme(
        accent=_hex(primary),
        background_start=_hex(_dark_colour(primary, 0.13)),
        background_end=_hex(_dark_colour(secondary, 0.065)),
    )


def _apply_sgr(codes: list[int], foreground: RGB | None, background: RGB | None):
    if not codes or codes == [0]:
        return None, None
    index = 0
    while index < len(codes):
        if codes[index : index + 2] == [38, 2] and index + 4 < len(codes):
            foreground = tuple(codes[index + 2 : index + 5])  # type: ignore[assignment]
            index += 5
        elif codes[index : index + 2] == [48, 2] and index + 4 < len(codes):
            background = tuple(codes[index + 2 : index + 5])  # type: ignore[assignment]
            index += 5
        else:
            index += 1
    return foreground, background


def parse_sprite(raw: str) -> Sprite:
    lines = raw.splitlines()
    while lines and not ANSI.sub("", lines[0]).strip():
        lines.pop(0)
    while lines and not ANSI.sub("", lines[-1]).strip():
        lines.pop()

    cells: list[Cell] = []
    palette: Counter[RGB] = Counter()
    foreground: RGB | None = None
    background: RGB | None = None
    width = 0

    for row, line in enumerate(lines):
        column = 0
        cursor = 0
        for match in ANSI.finditer(line + "\x1b[0m"):
            for character in line[cursor : match.start()]:
                cell = Cell(row, column, character, foreground, background)
                cells.append(cell)
                if character in {"▀", "▄"}:
                    if foreground is not None:
                        palette[foreground] += 1
                    if background is not None:
                        palette[background] += 1
                elif character != " " and foreground is not None:
                    palette[foreground] += 2
                elif character == " " and background is not None:
                    palette[background] += 2
                column += 1
            codes = [int(code) for code in match.group(1).split(";") if code]
            foreground, background = _apply_sgr(codes, foreground, background)
            cursor = match.end()
        width = max(width, column)

    return Sprite(tuple(cells), palette, width, len(lines))


def krabby_command(config: LogoConfig) -> list[str]:
    command = ["krabby", "name", config.pokemon, "--form", config.form]
    if config.shiny:
        command.append("--shiny")
    command.append("--no-title")
    return command


def load_config(path: Path) -> LogoConfig:
    with path.open("rb") as source:
        raw = tomllib.load(source).get("logo", {})
    return LogoConfig(
        pokemon=str(raw.get("pokemon", "rotom")),
        form=str(raw.get("form", "regular")),
        shiny=bool(raw.get("shiny", False)),
        title=str(raw.get("title", "POKÉDEX CLI")),
        tagline=str(raw.get("tagline", "catch · train · evolve")),
    )


def _rect(x: float, y: float, width: float, height: float, fill: RGB | None) -> str:
    if fill is None:
        return ""
    return f'<rect x="{x:g}" y="{y:g}" width="{width:g}" height="{height:g}" fill="{_hex(fill)}"/>'


def render_svg(sprite: Sprite, config: LogoConfig) -> str:
    theme = theme_from_palette(sprite.palette)
    cell_width = 10
    cell_height = 18
    raw_width = max(1, sprite.width * cell_width)
    raw_height = max(1, sprite.height * cell_height)
    scale = min(1.0, 286 / raw_width, 258 / raw_height)
    offset_x = 28 + (286 - raw_width * scale) / 2
    offset_y = 28 + (258 - raw_height * scale) / 2
    elements: list[str] = []

    for cell in sprite.cells:
        x = cell.column * cell_width
        y = cell.row * cell_height
        if cell.character == "▀":
            elements.append(_rect(x, y, 10, 9, cell.foreground))
            elements.append(_rect(x, y + 9, 10, 9, cell.background))
        elif cell.character == "▄":
            elements.append(_rect(x, y, 10, 9, cell.background))
            elements.append(_rect(x, y + 9, 10, 9, cell.foreground))
        elif cell.character == " ":
            elements.append(_rect(x, y, 10, 18, cell.background))

    title_lines = config.title.rsplit(" ", 1)
    if len(title_lines) == 1:
        title_lines.append("")
    first, second = (html.escape(line) for line in title_lines)
    metadata = html.escape(
        f"Generated from Krabby sprite: {config.pokemon}, form={config.form}, shiny={config.shiny}"
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="720" height="320" viewBox="0 0 720 320" role="img" aria-labelledby="title desc">
  <title id="title">{html.escape(config.title)}</title>
  <desc id="desc">{metadata}</desc>
  <defs>
    <linearGradient id="card" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{theme.background_start}"/>
      <stop offset="1" stop-color="{theme.background_end}"/>
    </linearGradient>
    <filter id="glow"><feGaussianBlur stdDeviation="7"/></filter>
  </defs>
  <rect width="720" height="320" rx="28" fill="url(#card)"/>
  <circle cx="171" cy="160" r="116" fill="{theme.accent}" opacity=".14" filter="url(#glow)"/>
  <g transform="translate({offset_x:g} {offset_y:g}) scale({scale:g})" shape-rendering="crispEdges">{"".join(elements)}</g>
  <g font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">
    <text x="365" y="137" fill="#f8fafc" font-size="46" font-weight="800">{first}</text>
    <text x="365" y="187" fill="{theme.accent}" font-size="46" font-weight="800">{second}</text>
    <text x="368" y="226" fill="#cbd5e1" font-size="15">{html.escape(config.tagline)}</text>
  </g>
</svg>
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    config = load_config(args.config)
    raw = subprocess.run(krabby_command(config), check=True, capture_output=True, text=True).stdout
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_svg(parse_sprite(raw), config))
    print(f"Rendered {config.pokemon} ({config.form}) -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
