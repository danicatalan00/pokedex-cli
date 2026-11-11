"""Escena de combate estilo GBA: modelo inmutable, disposición y render.

La escena es un dato (``SceneModel``) y su dibujo una función pura
(``render_scene``): la CLI y la TUI la pintan igual y el director de
animaciones solo tiene que producir escenas nuevas. Disposición clásica: el
rival arriba a la derecha con su caja de datos arriba a la izquierda, y el
jugador abajo a la izquierda, espejado para mirar al rival, con su caja abajo a
la derecha. Cada sprite pisa su plataforma.

Los sprites se trabajan como píxeles de medio bloque (dos por celda): así se
reducen, espejan y componen sin pelearse con los códigos ANSI. Las celdas
vacías salen sin color para que la escena funcione igual sobre el papel crema
de la TUI que sobre cualquier terminal; lo que sí se pinta (cajas, barras,
plataformas) lleva sus propios colores de fondo y de tinta.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, NamedTuple

from rich.cells import cell_len
from rich.color import Color
from rich.style import Style
from rich.text import Span, Text

from pokedex_cli.domain.battle.model import (
    BattleState,
    Combatant,
    MoveSlot,
    Outcome,
    SideId,
    Status,
)
from pokedex_cli.domain.progression import MAX_LEVEL, experience_for_level
from pokedex_cli.presentation.animation import Cell
from pokedex_cli.presentation.battle_text import (
    STATUS_BADGES,
    effectiveness_hint,
    move_summary,
)
from pokedex_cli.presentation.tui.graphics import downscale, parse_ansi_sprite

RGB = tuple[int, int, int]
Pixel = RGB | None
Grid = list[list[Cell]]
# (especie, forma, shiny) → sprite ANSI de krabby, o None si no hay.
SpriteLookup = Callable[[str, str, bool], str | None]

# Colores de tipo clásicos; brillantes para que se lean como «chapas» con su
# propio fondo tanto en el papel crema como en un terminal oscuro.
TYPE_RGB: dict[str, RGB] = {
    "normal": (168, 168, 120),
    "fire": (240, 128, 48),
    "water": (104, 144, 240),
    "electric": (248, 208, 48),
    "grass": (120, 200, 80),
    "ice": (152, 216, 216),
    "fighting": (192, 48, 40),
    "poison": (160, 64, 160),
    "ground": (224, 192, 104),
    "flying": (168, 144, 240),
    "psychic": (248, 88, 136),
    "bug": (168, 184, 32),
    "rock": (184, 160, 56),
    "ghost": (112, 88, 152),
    "dragon": (112, 56, 248),
    "dark": (112, 88, 72),
    "steel": (184, 184, 208),
    "fairy": (238, 153, 172),
}

# Cajas de datos: papel claro con tinta oscura y contorno fino, como en GBA.
_INK: RGB = (40, 44, 40)
_HUD_FILL: RGB = (250, 248, 230)
_HUD_FRAME: RGB = (64, 76, 66)
_TRACK: RGB = (66, 76, 70)
_TAG: RGB = (244, 186, 64)
_MALE: RGB = (40, 112, 232)
_FEMALE: RGB = (226, 66, 104)
_SHINY: RGB = (226, 160, 20)
# Barras: (brillo superior, color principal).
_HP_GREEN: tuple[RGB, RGB] = ((128, 250, 176), (72, 200, 120))
_HP_YELLOW: tuple[RGB, RGB] = ((250, 230, 90), (216, 176, 16))
_HP_RED: tuple[RGB, RGB] = ((250, 120, 96), (208, 56, 48))
_EXP_BLUE: tuple[RGB, RGB] = ((120, 208, 255), (56, 144, 232))
# Plataformas en tonos del papel: superficie, falda y borde en sombra.
_PLATFORM_TOP: RGB = (214, 202, 164)
_PLATFORM_MID: RGB = (190, 176, 134)
_PLATFORM_RIM: RGB = (150, 134, 96)
# Destello blanco cálido (salida de la Poké Ball, golpes).
FLASH_RGB: RGB = (255, 255, 244)

_GENDER_GLYPHS: dict[str, tuple[str, RGB]] = {
    "male": ("♂", _MALE),
    "female": ("♀", _FEMALE),
}


# --- Modelo -----------------------------------------------------------------


@dataclass(frozen=True)
class Particle:
    """Un glifo suelto encima de la escena (chispa, llama, flecha, Poké Ball…).

    ``row``/``col`` son coordenadas de la escena; fuera del lienzo se ignora.
    Un glifo de texto sin ``bg`` hereda el color de lo que tape, así no
    agujerea los sprites. Los medios bloques (``▀``, ``▄``, ``█``) son píxeles:
    ``▀`` pinta ``rgb`` arriba y ``bg`` abajo, y la mitad sin color deja ver lo
    de debajo, igual que un sprite; con ellos se dibujan efectos de pixel art.
    """

    row: int
    col: int
    glyph: str
    rgb: RGB
    bg: RGB | None = None


@dataclass(frozen=True)
class Hud:
    """Caja de datos de un Pokémon. ``show_numbers``: el jugador ve «123/200»."""

    name: str
    level: int
    gender: str | None
    status: Status
    hp: int
    max_hp: int
    exp_fraction: float | None = None
    shiny: bool = False
    show_numbers: bool = False


@dataclass(frozen=True)
class SceneModel:
    """Fotograma completo del campo de batalla, listo para ``render_scene``.

    Los efectos son transitorios y los aplica el render: ``*_offset`` desplaza
    el sprite (filas, columnas), ``*_flash`` lo pinta en blanco, ``*_sink`` lo
    hunde bajo su plataforma (filas) y ``shake`` mueve toda la escena en
    horizontal. Las rejillas no se mutan nunca: se comparten entre fotogramas.
    """

    width: int
    height: int
    opponent_sprite: Grid = field(default_factory=list)
    player_sprite: Grid = field(default_factory=list)
    opponent_hud: Hud | None = None
    player_hud: Hud | None = None
    opponent_offset: tuple[int, int] = (0, 0)
    player_offset: tuple[int, int] = (0, 0)
    opponent_visible: bool = True
    player_visible: bool = True
    opponent_flash: bool = False
    player_flash: bool = False
    opponent_sink: int = 0
    player_sink: int = 0
    particles: tuple[Particle, ...] = ()
    shake: int = 0


class SideView(NamedTuple):
    """Los campos de un bando de la escena, sin prefijo."""

    sprite: Grid
    hud: Hud | None
    offset: tuple[int, int]
    visible: bool
    flash: bool
    sink: int


_SIDE_FIELDS = frozenset(SideView._fields)


def side_view(scene: SceneModel, side: SideId) -> SideView:
    if side == "player":
        return SideView(
            scene.player_sprite,
            scene.player_hud,
            scene.player_offset,
            scene.player_visible,
            scene.player_flash,
            scene.player_sink,
        )
    return SideView(
        scene.opponent_sprite,
        scene.opponent_hud,
        scene.opponent_offset,
        scene.opponent_visible,
        scene.opponent_flash,
        scene.opponent_sink,
    )


def replace_side(scene: SceneModel, side: SideId, **changes: Any) -> SceneModel:
    """``dataclasses.replace`` sobre los campos de un bando: ``visible=False``…"""
    unknown = set(changes) - _SIDE_FIELDS
    if unknown:
        raise TypeError(f"campos de bando desconocidos: {sorted(unknown)}")
    prefix = "player_" if side == "player" else "opponent_"
    return replace(scene, **{prefix + key: value for key, value in changes.items()})


# --- Disposición ------------------------------------------------------------


@dataclass(frozen=True)
class Box:
    row: int
    col: int
    height: int
    width: int

    @property
    def bottom(self) -> int:
        return self.row + self.height - 1

    @property
    def right(self) -> int:
        return self.col + self.width - 1

    @property
    def center(self) -> tuple[int, int]:
        return self.row + self.height // 2, self.col + self.width // 2


@dataclass(frozen=True)
class SceneLayout:
    """Cajas de la escena. La última fila de cada ``*_area`` es su suelo: el
    sprite se apoya ahí, centrado, y la plataforma empieza en esa fila."""

    width: int
    height: int
    opponent_hud: Box
    player_hud: Box
    opponent_area: Box
    player_area: Box
    opponent_platform: Box
    player_platform: Box

    def area(self, side: SideId) -> Box:
        return self.player_area if side == "player" else self.opponent_area

    def hud(self, side: SideId) -> Box:
        return self.player_hud if side == "player" else self.opponent_hud


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


@lru_cache(maxsize=32)
def layout(width: int, height: int) -> SceneLayout:
    """Disposición clásica de GBA adaptada al tamaño del lienzo.

    Pensada para 100×30 y probada a 80×24 y 60×18: las cajas encogen y la del
    jugador se compacta (la EXP pasa al borde inferior), los sprites se
    reducen para caber en su zona y nada se solapa. Por debajo de eso las
    cajas pueden pisarse, pero el render nunca se sale del lienzo.
    """
    width = max(1, width)
    height = max(1, height)
    top = 1 if height >= 26 else 0
    margin = 1 if width >= 50 else 0

    opp_hud_w = min(width, _clamp(round(width * 0.40), 20, 36))
    opponent_hud = Box(top, margin, min(4, height), opp_hud_w)
    player_hud_h = min(height, 6 if height >= 26 else 5 if height >= 20 else 4)
    player_hud_w = min(width, _clamp(round(width * 0.42), 22, 38))
    player_hud = Box(
        height - player_hud_h,
        max(0, width - player_hud_w - margin),
        player_hud_h,
        player_hud_w,
    )

    # Las plataformas son elipses de 2 o 3 filas; los pies del Pokémon caen en
    # su fila central, así la parte delantera de la elipse asoma bajo ellos.
    platform_h = 3 if height >= 22 else 2
    feet = (platform_h - 1) // 2

    # Rival: suelo a media altura, a la derecha de su caja.
    opp_ground = _clamp(round(height * 0.47), min(height - 1, opponent_hud.bottom), height - 1)
    opp_cx = round(width * 0.73)
    opp_w = min(
        round(width * 0.42),
        2 * (width - 1 - opp_cx) + 1,
        2 * (opp_cx - opponent_hud.right - 2),
    )
    opp_w = max(1, opp_w)
    opponent_area = Box(top, opp_cx - opp_w // 2, max(1, opp_ground - top + 1), opp_w)

    # Jugador: suelo al pie de la escena, a la izquierda de su caja.
    player_ground = max(0, height - platform_h + feet)
    player_top = min(player_ground, opponent_hud.bottom + 1)
    pl_cx = round(width * 0.25)
    pl_w = max(1, min(round(width * 0.46), 2 * pl_cx, 2 * (player_hud.col - 2 - pl_cx)))
    player_area = Box(player_top, pl_cx - pl_w // 2, player_ground - player_top + 1, pl_w)

    opp_pw = max(4, round(opp_w * 0.9))
    pl_pw = max(4, round(pl_w * 0.95))
    return SceneLayout(
        width=width,
        height=height,
        opponent_hud=opponent_hud,
        player_hud=player_hud,
        opponent_area=opponent_area,
        player_area=player_area,
        opponent_platform=Box(opp_ground - feet, opp_cx - opp_pw // 2, platform_h, opp_pw),
        player_platform=Box(player_ground - feet, pl_cx - pl_pw // 2, platform_h, pl_pw),
    )


# --- Sprites como píxeles ---------------------------------------------------


def _halves(ch: str, fg: Pixel, bg: Pixel) -> tuple[Pixel, Pixel] | None:
    """Colores (arriba, abajo) de una celda de medios bloques; None si es otro glifo."""
    if ch == " ":
        return bg, bg
    if ch == "▀":
        return fg, bg
    if ch == "▄":
        return bg, fg
    if ch == "█":
        return fg, fg
    return None


def _cell_from_halves(top: Pixel, bottom: Pixel) -> Cell:
    if top is None and bottom is None:
        return Cell()
    if bottom is None:
        return Cell("▀", top, None)
    if top is None:
        return Cell("▄", bottom, None)
    return Cell("▀", top, bottom)


def _trim(pixels: list[list[Pixel]]) -> list[list[Pixel]]:
    """Recorta filas y columnas totalmente transparentes alrededor del sprite."""
    rows = [row for row in pixels]
    while rows and all(pixel is None for pixel in rows[0]):
        rows.pop(0)
    while rows and all(pixel is None for pixel in rows[-1]):
        rows.pop()
    if not rows:
        return []
    width = max(len(row) for row in rows)
    rows = [row + [None] * (width - len(row)) for row in rows]
    used = [x for x in range(width) if any(row[x] is not None for row in rows)]
    left, right = used[0], used[-1] + 1
    return [row[left:right] for row in rows]


def _resample(pixels: list[list[Pixel]], height: int, width: int) -> list[list[Pixel]]:
    """Reescala a cualquier tamaño menor sin perder la silueta.

    Cada píxel destino toma el píxel central de su huella; si es transparente,
    el primero opaco de la huella. Así el interior conserva el color exacto y
    los bordes finos no se agujerean, igual que ``graphics.downscale``.
    """
    source_h, source_w = len(pixels), len(pixels[0])
    result: list[list[Pixel]] = []
    for y in range(height):
        y0 = y * source_h // height
        y1 = max(y0 + 1, (y + 1) * source_h // height)
        yc = min(source_h - 1, (2 * y + 1) * source_h // (2 * height))
        footprint_rows = pixels[y0:y1]
        row: list[Pixel] = []
        for x in range(width):
            x0 = x * source_w // width
            x1 = max(x0 + 1, (x + 1) * source_w // width)
            chosen = pixels[yc][min(source_w - 1, (2 * x + 1) * source_w // (2 * width))]
            if chosen is None:
                for footprint in footprint_rows:
                    chosen = next((pixel for pixel in footprint[x0:x1] if pixel is not None), None)
                    if chosen is not None:
                        break
            row.append(chosen)
        result.append(row)
    return result


def _fit(pixels: list[list[Pixel]], max_height: int, max_width: int) -> list[list[Pixel]]:
    """Reduce (nunca amplía) conservando proporciones para caber en la caja.

    Primero aplica la mayor reducción entera con ``graphics.downscale`` y
    después ajusta la fracción que falte, para aprovechar todo el sitio.
    """
    height, width = len(pixels), len(pixels[0])
    if height <= max_height and width <= max_width:
        return pixels
    if max_height * width <= max_width * height:
        target_h, target_w = max_height, max(1, width * max_height // height)
    else:
        target_h, target_w = max(1, height * max_width // width), max_width
    factor = min(height // target_h, width // target_w)
    if factor >= 2:
        pixels = downscale(pixels, factor)
        height, width = len(pixels), len(pixels[0])
    if (height, width) != (target_h, target_w):
        pixels = _resample(pixels, min(target_h, height), min(target_w, width))
    return pixels


def _pixels_to_grid(pixels: list[list[Pixel]]) -> Grid:
    if not pixels:
        return []
    width = len(pixels[0])
    if len(pixels) % 2:
        # Fila par: el relleno va arriba para que los pies toquen el suelo.
        padding: list[Pixel] = [None] * width
        pixels = [padding, *pixels]
    return [
        [_cell_from_halves(top, bottom) for top, bottom in zip(pixels[y], pixels[y + 1])]
        for y in range(0, len(pixels), 2)
    ]


def _grid_to_pixels(grid: Grid) -> list[list[Pixel]]:
    pixels: list[list[Pixel]] = []
    for row in grid:
        top: list[Pixel] = []
        bottom: list[Pixel] = []
        for cell in row:
            halves = _halves(cell.ch, cell.fg, cell.bg)
            if halves is None:
                halves = (cell.fg, cell.fg) if cell.ch.strip() else (cell.bg, cell.bg)
            top.append(halves[0])
            bottom.append(halves[1])
        pixels.append(top)
        pixels.append(bottom)
    return pixels


def grid_size(grid: Grid) -> tuple[int, int]:
    """(filas, columnas) de una rejilla."""
    return len(grid), (len(grid[0]) if grid else 0)


def prepare_sprite(ansi: str | None, max_rows: int, max_cols: int, *, mirror: bool) -> Grid:
    """Sprite ANSI de krabby → rejilla lista para la escena.

    Recorta el aire alrededor, lo reduce si no cabe en ``max_rows`` ×
    ``max_cols`` celdas y, con ``mirror``, lo voltea en horizontal: el del
    jugador mira así hacia el rival. Sin sprite, o con uno ilegible, devuelve
    una rejilla vacía (la escena pone un sustituto); nunca lanza.
    """
    if not ansi or max_rows <= 0 or max_cols <= 0:
        return []
    try:
        pixels = parse_ansi_sprite(ansi)
    except (ValueError, IndexError):
        return []
    pixels = _trim(pixels)
    if not pixels:
        return []
    pixels = _fit(pixels, max_rows * 2, max_cols)
    if mirror:
        pixels = [row[::-1] for row in pixels]
    return _pixels_to_grid(pixels)


def scale_grid(grid: Grid, fraction: float) -> Grid:
    """Copia reducida a ``fraction`` (0–1) del tamaño; para crecer al salir de la
    Poké Ball o encoger al volver a ella."""
    if not grid or not grid[0] or fraction >= 1:
        return grid
    pixels = _grid_to_pixels(grid)
    height = max(1, round(len(pixels) * fraction))
    width = max(1, round(len(pixels[0]) * fraction))
    return _pixels_to_grid(_trim(_resample(pixels, height, width)))


def tint_grid(grid: Grid, rgb: RGB) -> Grid:
    """Silueta del sprite en un solo color (energía de la Poké Ball, destellos)."""
    return [
        [
            _cell_from_halves(
                rgb if top is not None else None,
                rgb if bottom is not None else None,
            )
            for top, bottom in (
                _halves(cell.ch, cell.fg, cell.bg) or (cell.fg, cell.fg) for cell in row
            )
        ]
        for row in grid
    ]


def _placeholder_grid() -> Grid:
    """Poké Ball pequeña que ocupa el sitio del sprite cuando no hay krabby."""
    art = (
        "....XXXX....",
        "..XXRRRRXX..",
        ".XRRRRRRRRX.",
        ".XRRRRRRRRX.",
        "XRRRRRRRRRRX",
        "XXXXXBBXXXXX",
        "XWWWXBBXWWWX",
        "XWWWWXXWWWWX",
        ".XWWWWWWWWX.",
        ".XWWWWWWWWX.",
        "..XXWWWWXX..",
        "....XXXX....",
    )
    palette: dict[str, Pixel] = {
        ".": None,
        "X": (70, 64, 60),
        "R": (216, 78, 66),
        "W": (240, 236, 226),
        "B": (252, 252, 252),
    }
    return _pixels_to_grid([[palette[ch] for ch in line] for line in art])


_PLACEHOLDER = _placeholder_grid()


def _effective_sprite(grid: Grid) -> Grid:
    return grid if grid and grid[0] else _PLACEHOLDER


def _placed_box(area: Box, grid: Grid, offset: tuple[int, int], sink: int) -> Box:
    height, width = grid_size(grid)
    row = area.bottom - height + 1 + offset[0] + max(0, sink)
    col = area.col + area.width // 2 - width // 2 + offset[1]
    return Box(row, col, height, width)


def sprite_box(scene: SceneModel, side: SideId) -> Box:
    """Dónde cae el sprite de ``side`` en este fotograma (con desplazamiento y
    hundimiento). Sirve al director para apuntar proyectiles y auras."""
    view = side_view(scene, side)
    plan = layout(scene.width, scene.height)
    return _placed_box(plan.area(side), _effective_sprite(view.sprite), view.offset, view.sink)


# --- Lienzo -----------------------------------------------------------------

# Celda del lienzo: glifo, tinta, fondo y negrita.
_Slot = tuple[str, Pixel, Pixel, bool]
_EMPTY: _Slot = (" ", None, None, False)


@lru_cache(maxsize=1024)
def _style(fg: Pixel, bg: Pixel, bold: bool) -> Style:
    return Style(
        color=Color.from_rgb(*fg) if fg is not None else None,
        bgcolor=Color.from_rgb(*bg) if bg is not None else None,
        bold=True if bold else None,
    )


class _Canvas:
    """Rejilla de celdas con composición por medios bloques."""

    __slots__ = ("width", "height", "rows")

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.rows: list[list[_Slot]] = [[_EMPTY] * width for _ in range(height)]

    def _under(self, row: int, col: int) -> tuple[Pixel, Pixel]:
        ch, fg, bg, _ = self.rows[row][col]
        return _halves(ch, fg, bg) or (bg, bg)

    def blend(self, row: int, col: int, top: Pixel, bottom: Pixel) -> None:
        """Pinta dos medios píxeles; la mitad transparente deja ver lo de debajo."""
        if (top is None and bottom is None) or not (
            0 <= row < self.height and 0 <= col < self.width
        ):
            return
        if top is None or bottom is None:
            under_top, under_bottom = self._under(row, col)
            top = under_top if top is None else top
            bottom = under_bottom if bottom is None else bottom
        if bottom is None:
            self.rows[row][col] = ("▀", top, None, False)
        elif top is None:
            self.rows[row][col] = ("▄", bottom, None, False)
        else:
            self.rows[row][col] = ("▀", top, bottom, False)

    def put(self, row: int, col: int, slot: _Slot) -> None:
        if 0 <= row < self.height and 0 <= col < self.width:
            self.rows[row][col] = slot

    def line(self, row: int, col: int, slots: Sequence[_Slot]) -> None:
        for offset, slot in enumerate(slots):
            self.put(row, col + offset, slot)

    def glyph(self, row: int, col: int, glyph: str, fg: RGB, bg: Pixel = None) -> None:
        """Glifo encima de lo que haya; sin fondo propio hereda el de debajo."""
        if not (0 <= row < self.height and 0 <= col < self.width):
            return
        if bg is None:
            top, bottom = self._under(row, col)
            bg = bottom if bottom is not None else top
        self.rows[row][col] = (glyph, fg, bg, False)

    def shift(self, cols: int) -> None:
        if cols == 0:
            return
        cols = _clamp(cols, -self.width, self.width)
        for index, row in enumerate(self.rows):
            if cols > 0:
                self.rows[index] = [_EMPTY] * cols + row[: self.width - cols]
            else:
                self.rows[index] = row[-cols:] + [_EMPTY] * (-cols)

    def to_text(self) -> Text:
        parts: list[str] = []
        spans: list[Span] = []
        offset = 0
        for index, row in enumerate(self.rows):
            if index:
                parts.append("\n")
                offset += 1
            run_start = offset
            run_key: tuple[Pixel, Pixel, bool] = (None, None, False)
            for ch, fg, bg, bold in row:
                key = (fg, bg, bold)
                if key != run_key:
                    if run_key != (None, None, False) and offset > run_start:
                        spans.append(Span(run_start, offset, _style(*run_key)))
                    run_key = key
                    run_start = offset
                parts.append(ch)
                offset += 1
            if run_key != (None, None, False) and offset > run_start:
                spans.append(Span(run_start, offset, _style(*run_key)))
        return Text("".join(parts), spans=spans, no_wrap=True, overflow="crop")


def _slots_to_text(slots: Sequence[_Slot]) -> Text:
    canvas = _Canvas(len(slots), 1)
    canvas.line(0, 0, slots)
    return canvas.to_text()


# --- Barras -----------------------------------------------------------------


def hp_units(hp: int, max_hp: int, width: int) -> int:
    """Medias celdas llenas de una barra de PS de ``width`` celdas.

    Con PS > 0 siempre queda al menos media celda, y con PS < máximo nunca se
    ve llena: la barra no miente en los extremos.
    """
    total = 2 * max(0, width)
    if total == 0 or max_hp <= 0 or hp <= 0:
        return 0
    if hp >= max_hp:
        return total
    return _clamp(hp * total // max_hp, 1, total - 1)


def _hp_colors(hp: int, max_hp: int) -> tuple[RGB, RGB]:
    if max_hp > 0 and hp * 2 > max_hp:
        return _HP_GREEN
    if max_hp > 0 and hp * 5 > max_hp:
        return _HP_YELLOW
    return _HP_RED


def hp_color(hp: int, max_hp: int) -> RGB:
    """Verde por encima del 50 %, amarillo por encima del 20 % y rojo después."""
    return _hp_colors(hp, max_hp)[1]


def _bar_slots(units: int, width: int, colors: tuple[RGB, RGB]) -> list[_Slot]:
    light, main = colors
    slots: list[_Slot] = []
    for index in range(max(0, width)):
        filled = units - 2 * index
        if filled >= 2:
            slots.append(("▀", light, main, False))
        elif filled == 1:
            slots.append(("▌", main, _TRACK, False))
        else:
            slots.append((" ", None, _TRACK, False))
    return slots


def hp_bar(hp: int, max_hp: int, width: int) -> Text:
    """Barra de PS de ``width`` celdas con resolución de media celda."""
    return _slots_to_text(_bar_slots(hp_units(hp, max_hp, width), width, _hp_colors(hp, max_hp)))


def _exp_units(fraction: float, width: int) -> int:
    return _clamp(int(max(0.0, min(1.0, fraction)) * 2 * width), 0, 2 * width)


# --- Cajas de datos ---------------------------------------------------------


def _fit_text(text: str, room: int) -> str:
    """Recorta con «…» y sustituye lo que no ocupe exactamente una celda."""
    if room <= 0:
        return ""
    clean = "".join(ch if cell_len(ch) == 1 else "?" for ch in unicodedata.normalize("NFC", text))
    if len(clean) <= room:
        return clean
    return clean[: room - 1] + "…"


def _plain(text: str, fg: RGB = _INK, bg: RGB = _HUD_FILL, bold: bool = False) -> list[_Slot]:
    return [(ch, fg, bg, bold) for ch in text]


def _badge_colors(color: str) -> tuple[RGB, RGB]:
    triplet = Color.parse(color).get_truecolor()
    rgb = (triplet.red, triplet.green, triplet.blue)
    luminance = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    return rgb, ((40, 36, 24) if luminance > 160 else (255, 255, 255))


def _name_line(hud: Hud, inner: int) -> list[_Slot]:
    level = f"Nv.{hud.level}"
    gender = _GENDER_GLYPHS.get(hud.gender or "")
    badge = STATUS_BADGES.get(hud.status)
    extras = (1 if gender else 0) + (1 if hud.shiny else 0)
    badge_width = len(badge[0]) + 3 if badge else 0  # « DOR » más un hueco
    room = inner - 2 - extras - badge_width - len(level) - 1
    name = _fit_text(hud.name, room)
    slots = _plain(" ") + _plain(name, bold=True)
    if gender:
        slots += _plain(gender[0], fg=gender[1], bold=True)
    if hud.shiny:
        slots += _plain("★", fg=_SHINY)
    right: list[_Slot] = []
    if badge:
        bg, fg = _badge_colors(badge[1])
        right += _plain(f" {badge[0]} ", fg=fg, bg=bg, bold=True) + _plain(" ")
    right += _plain(level, bold=True) + _plain(" ")
    gap = inner - len(slots) - len(right)
    return (slots + _plain(" " * max(0, gap)) + right)[:inner]


def _tagged_bar(tag: str, bar: list[_Slot], inner: int, tail: list[_Slot]) -> list[_Slot]:
    slots = _plain(" ") + _plain(tag.rjust(3), fg=_TAG, bg=_TRACK, bold=True) + bar + tail
    return slots + _plain(" " * max(0, inner - len(slots)))


def _numbers(hud: Hud) -> str:
    return f"{max(0, hud.hp):>3}/{hud.max_hp:>3}"


def _draw_frame(canvas: _Canvas, box: Box, exp: tuple[int, int] | None) -> None:
    """Marco fino de medio bloque con esquinas redondeadas.

    El contorno cae en la mitad interior de las celdas del borde, pegado al
    relleno. Con ``exp`` (medias celdas llenas, ancho) el borde inferior hace
    de barra de EXP, como el filo azul de la caja del jugador en GBA.
    """
    top, bottom, left, right = box.row, box.bottom, box.col, box.right
    canvas.put(top, left, ("▗", _HUD_FRAME, None, False))
    canvas.put(top, right, ("▖", _HUD_FRAME, None, False))
    canvas.put(bottom, left, ("▝", _HUD_FRAME, None, False))
    canvas.put(bottom, right, ("▘", _HUD_FRAME, None, False))
    for col in range(left + 1, right):
        canvas.put(top, col, ("▄", _HUD_FRAME, None, False))
        canvas.put(bottom, col, ("▀", _HUD_FRAME, None, False))
    for row in range(top + 1, bottom):
        canvas.put(row, left, ("▐", _HUD_FRAME, None, False))
        canvas.put(row, right, ("▌", _HUD_FRAME, None, False))
    if exp is not None:
        units, width = exp
        start = left + 2
        main = _EXP_BLUE[1]
        for index in range(width):
            filled = units - 2 * index
            if filled >= 2:
                slot: _Slot = ("▀", main, None, False)
            elif filled == 1:
                slot = ("▘", main, None, False)
            else:
                slot = ("▀", _HUD_FRAME, None, False)
            canvas.put(bottom, start + index, slot)


def _draw_hud(canvas: _Canvas, hud: Hud, box: Box) -> None:
    if box.width < 10 or box.height < 3:
        return
    inner = box.width - 2
    content_rows = box.height - 2
    exp_in_frame = hud.exp_fraction is not None and content_rows < 4
    exp_width = max(1, inner - 2)
    frame_exp = (
        (_exp_units(hud.exp_fraction or 0.0, exp_width), exp_width) if exp_in_frame else None
    )
    _draw_frame(canvas, box, frame_exp)
    for index in range(content_rows):
        canvas.line(box.row + 1 + index, box.col + 1, _plain(" " * inner))

    canvas.line(box.row + 1, box.col + 1, _name_line(hud, inner))
    if content_rows < 2:
        return
    numbers_inline = hud.show_numbers and content_rows == 2
    tail = _plain(" " + _numbers(hud), bold=True) if numbers_inline else []
    bar_width = max(1, inner - 5 - len(tail))
    bar = _bar_slots(
        hp_units(hud.hp, hud.max_hp, bar_width), bar_width, _hp_colors(hud.hp, hud.max_hp)
    )
    canvas.line(box.row + 2, box.col + 1, _tagged_bar("PS", bar, inner, tail))
    if content_rows >= 3 and hud.show_numbers and not numbers_inline:
        numbers = _numbers(hud) + " "
        canvas.line(
            box.row + 3,
            box.col + 1,
            _plain(" " * max(0, inner - len(numbers))) + _plain(numbers, bold=True),
        )
    if content_rows >= 4 and hud.exp_fraction is not None:
        exp_bar = _bar_slots(_exp_units(hud.exp_fraction, bar_width), bar_width, _EXP_BLUE)
        canvas.line(box.row + 4, box.col + 1, _tagged_bar("EXP", exp_bar, inner, []))


# --- Render -----------------------------------------------------------------


@lru_cache(maxsize=64)
def _platform_pixels(width: int, rows: int) -> tuple[tuple[Pixel, ...], ...]:
    """Elipse de ``2 × rows`` medios píxeles: superficie clara, falda en sombra
    y contorno (suave arriba, oscuro abajo), como las plataformas de GBA en
    tonos de papel. Es una superelipse: con tan pocas filas, una elipse pura
    deja las puntas como palitos sueltos."""
    height = 2 * rows
    half_w, half_h = width / 2, height / 2

    def inside(x: int, y: int) -> bool:
        if not (0 <= x < width and 0 <= y < height):
            return False
        dx = abs(x + 0.5 - half_w) / half_w
        dy = abs(y + 0.5 - half_h) / half_h
        return dx**3 + dy**3 <= 1.0

    pixels: list[tuple[Pixel, ...]] = []
    for y in range(height):
        row: list[Pixel] = []
        lit = y < half_h
        for x in range(width):
            if not inside(x, y):
                row.append(None)
            elif not all(inside(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                row.append(_PLATFORM_MID if lit else _PLATFORM_RIM)
            else:
                row.append(_PLATFORM_TOP if lit else _PLATFORM_MID)
        pixels.append(tuple(row))
    return tuple(pixels)


def _draw_platform(canvas: _Canvas, box: Box) -> None:
    pixels = _platform_pixels(box.width, box.height)
    for index in range(0, len(pixels), 2):
        for col, (top, bottom) in enumerate(zip(pixels[index], pixels[index + 1])):
            canvas.blend(box.row + index // 2, box.col + col, top, bottom)


def _draw_sprite(canvas: _Canvas, scene: SceneModel, plan: SceneLayout, side: SideId) -> None:
    view = side_view(scene, side)
    if not view.visible:
        return
    grid = _effective_sprite(view.sprite)
    area = plan.area(side)
    box = _placed_box(area, grid, view.offset, view.sink)
    # Solo el hundimiento esconde filas: si el sprite baja con un
    # desplazamiento (el rival que embiste), el suelo baja con él.
    ground = area.bottom + max(0, view.offset[0])
    for r, row in enumerate(grid):
        y = box.row + r
        if y > ground:
            break  # lo hundido bajo la plataforma no se ve
        if y < 0:
            continue
        for c, cell in enumerate(row):
            halves = _halves(cell.ch, cell.fg, cell.bg)
            if halves is None:
                if cell.fg is not None:
                    canvas.glyph(y, box.col + c, cell.ch, FLASH_RGB if view.flash else cell.fg)
                continue
            top, bottom = halves
            if view.flash:
                top = FLASH_RGB if top is not None else None
                bottom = FLASH_RGB if bottom is not None else None
            canvas.blend(y, box.col + c, top, bottom)


def render_scene(scene: SceneModel) -> Text:
    """Dibuja la escena en truecolor; siempre ``height`` líneas de ``width`` celdas."""
    width, height = max(1, scene.width), max(1, scene.height)
    plan = layout(width, height)
    canvas = _Canvas(width, height)
    _draw_platform(canvas, plan.opponent_platform)
    _draw_platform(canvas, plan.player_platform)
    _draw_sprite(canvas, scene, plan, "opponent")
    _draw_sprite(canvas, scene, plan, "player")
    if scene.opponent_hud is not None:
        _draw_hud(canvas, scene.opponent_hud, plan.opponent_hud)
    if scene.player_hud is not None:
        _draw_hud(canvas, scene.player_hud, plan.player_hud)
    for particle in scene.particles:
        halves = _halves(particle.glyph, particle.rgb, particle.bg)
        if halves is not None and particle.glyph != " ":
            canvas.blend(particle.row, particle.col, *halves)
        else:
            canvas.glyph(particle.row, particle.col, particle.glyph, particle.rgb, particle.bg)
    canvas.shift(scene.shake)
    return canvas.to_text()


# --- Menús ------------------------------------------------------------------

_CURSOR = "▶"
_CURSOR_RGB: RGB = (216, 64, 48)


def menu_text(options: Sequence[str], selected: int, columns: int = 2) -> Text:
    """Menú en rejilla (por filas) con el cursor ▶ en la opción elegida."""
    columns = max(1, columns)
    if not options:
        return Text("")
    cell_width = max(len(option) for option in options) + 2
    text = Text(no_wrap=True)
    for index, option in enumerate(options):
        if index and index % columns == 0:
            text.append("\n")
        elif index:
            text.append("  ")
        chosen = index == selected
        text.append(
            _CURSOR if chosen else " ", style=Style(color=Color.from_rgb(*_CURSOR_RGB), bold=True)
        )
        text.append(" ")
        label = option.ljust(cell_width - 2) if (index + 1) % columns else option
        text.append(label, style="bold" if chosen else None)
    return text


def _chip_colors(type_slug: str) -> tuple[RGB, RGB]:
    bg = TYPE_RGB.get(type_slug, (168, 168, 120))
    luminance = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    return bg, ((36, 32, 24) if luminance > 150 else (255, 255, 255))


def moves_panel(slots: Sequence[MoveSlot], selected: int, defender_types: Sequence[str]) -> Text:
    """Rejilla 2×2 de movimientos (chapas del color de su tipo) y, debajo, el
    detalle del elegido: PP, tipo, categoría, potencia, precisión y eficacia."""
    if not slots:
        return Text("Sin movimientos.", no_wrap=True)
    names = [_fit_text(slot.move.name, 18) for slot in slots]
    chip = max(12, max(len(name) for name in names) + 2)
    text = Text(no_wrap=True)
    cursor_style = Style(color=Color.from_rgb(*_CURSOR_RGB), bold=True)
    for index in range(4):
        if index == 2:
            text.append("\n")
        elif index:
            text.append("  ")
        if index >= len(slots):
            text.append("  " + "—".center(chip), style="dim")
            continue
        chosen = index == selected
        bg, fg = _chip_colors(slots[index].move.type)
        text.append(_CURSOR if chosen else " ", style=cursor_style)
        text.append(" ")
        text.append(
            f" {names[index]}".ljust(chip),
            style=Style(color=Color.from_rgb(*fg), bgcolor=Color.from_rgb(*bg), bold=chosen),
        )
    current = slots[max(0, min(len(slots) - 1, selected))]
    text.append("\n")
    if current.pp <= 0:
        pp_style: str | None = "bold red"
    elif current.pp * 4 <= current.max_pp:
        pp_style = "bold dark_orange"
    else:
        pp_style = None
    text.append(f"PP {current.pp}/{current.max_pp}", style=pp_style)
    text.append(f" · {move_summary(current.move)}")
    hint = effectiveness_hint(current.move, defender_types)
    if hint:
        hint_style = {
            "Muy eficaz": "bold green4",
            "Poco eficaz": "bold dark_orange3",
            "No afecta": "bold grey50",
        }[hint]
        text.append(" · ")
        text.append(hint, style=hint_style)
    return text


# --- Estado → escena --------------------------------------------------------


def experience_fraction(level: int, experience: int, growth_rate: str | None) -> float:
    """Avance de 0 a 1 dentro del nivel actual (lleno en el nivel máximo)."""
    if level >= MAX_LEVEL:
        return 1.0
    low = experience_for_level(growth_rate, level)
    high = experience_for_level(growth_rate, level + 1)
    if high <= low:
        return 1.0
    return max(0.0, min(1.0, (experience - low) / (high - low)))


def hud_for(combatant: Combatant, *, player: bool, show_experience: bool = True) -> Hud:
    """Caja de datos de un combatiente; el jugador ve números y EXP."""
    exp = (
        experience_fraction(combatant.level, combatant.experience, combatant.growth_rate)
        if player and show_experience
        else None
    )
    return Hud(
        name=combatant.name,
        level=combatant.level,
        gender=combatant.gender,
        status=combatant.status,
        hp=max(0, combatant.hp),
        max_hp=max(1, combatant.max_hp),
        exp_fraction=exp,
        shiny=combatant.shiny,
        show_numbers=player,
    )


def lookup_sprite(
    sprites: SpriteLookup,
    species: str,
    form: str,
    shiny: bool,
    area: Box,
    *,
    mirror: bool,
) -> Grid:
    """Pide el sprite y lo prepara para ``area``; un fallo del proveedor deja
    la rejilla vacía (la escena pone el sustituto) en vez de romper."""
    try:
        ansi = sprites(species, form, shiny)
    except Exception:  # el sprite es decorativo: sin él se usa el sustituto
        ansi = None
    return prepare_sprite(ansi, area.height, area.width, mirror=mirror)


def scene_from_state(
    state: BattleState, sprites: SpriteLookup, width: int, height: int
) -> SceneModel:
    """Escena en reposo que corresponde a ``state``.

    Se ocultan los debilitados (sin caja), los que están en el aire o bajo
    tierra a mitad de un movimiento de dos turnos y el rival ya capturado o
    huido; tras una huida del jugador, el que se va es su Pokémon.
    """
    plan = layout(width, height)
    opponent = state.opponent.current
    player = state.player.current
    opponent_gone = opponent.fainted or state.outcome in (Outcome.CAPTURED, Outcome.OPPONENT_FLED)
    player_gone = player.fainted or state.outcome is Outcome.ESCAPED
    return SceneModel(
        width=width,
        height=height,
        opponent_sprite=lookup_sprite(
            sprites,
            opponent.species,
            opponent.form,
            opponent.shiny,
            plan.opponent_area,
            mirror=False,
        ),
        player_sprite=lookup_sprite(
            sprites, player.species, player.form, player.shiny, plan.player_area, mirror=True
        ),
        opponent_hud=None if opponent_gone else hud_for(opponent, player=False),
        player_hud=None
        if player_gone
        else hud_for(player, player=True, show_experience=state.award_experience),
        opponent_visible=not (opponent_gone or opponent.volatile.semi_invulnerable),
        player_visible=not (player_gone or player.volatile.semi_invulnerable),
    )
