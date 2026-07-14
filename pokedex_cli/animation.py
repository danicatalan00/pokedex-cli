import random
import re
import subprocess
import time
from dataclasses import dataclass

from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.text import Text

from pokedex_cli import krabby_bridge

# --- Escena ---------------------------------------------------------------
# La animación compone, celda a celda, el sprite real del Pokémon (capturado
# de krabby con sus colores ANSI) con una pokeball que se lanza *encima* de él.
# Al trabajar sobre una rejilla de celdas podemos dibujar la pokeball delante
# del Pokémon sin pelearnos con los códigos ANSI, y "absorberlo" recoloreando
# sus celdas. Cualquier fallo se traga: la animación es decoración y nunca
# debe bloquear una captura ya guardada.

_ANSI_CODE = re.compile(r"\x1b\[[0-9;]*m")
_RESET = "\x1b[0m"

# Colores de la pokeball (truecolor RGB).
_BALL_RED = (237, 28, 36)
_BALL_WHITE = (240, 240, 240)
_BALL_DARK = (40, 40, 40)
_FLASH = (255, 255, 210)
_ABSORB = (150, 90, 210)  # haz de energía violeta al absorber
_BALL_BLUE = (38, 104, 230)
_BALL_YELLOW = (250, 197, 35)
_BALL_PURPLE = (137, 73, 196)
_BALL_PINK = (245, 111, 188)


@dataclass(frozen=True)
class BallAnimationStyle:
    slug: str
    name: str
    top: tuple[int, int, int]
    bottom: tuple[int, int, int]
    accent: tuple[int, int, int]
    flash: tuple[int, int, int]
    absorb: tuple[int, int, int]
    pattern: str
    tier: int
    lock_glyph: str

    @property
    def trail_length(self) -> int:
        return self.tier + 1

    @property
    def impact_pulses(self) -> int:
        return self.tier + 1

    @property
    def energy_pulses(self) -> int:
        return self.tier


_BALL_STYLES = {
    "pokeball": BallAnimationStyle(
        "pokeball", "Pokeball", _BALL_RED, _BALL_WHITE, _BALL_DARK,
        _FLASH, _ABSORB, "·", 0, "✓",
    ),
    "superball": BallAnimationStyle(
        "superball", "Superball", _BALL_BLUE, _BALL_WHITE, _BALL_RED,
        (170, 225, 255), (65, 150, 255), "◆", 1, "◆",
    ),
    "ultraball": BallAnimationStyle(
        "ultraball", "Ultraball", _BALL_DARK, _BALL_WHITE, _BALL_YELLOW,
        (255, 238, 125), (255, 190, 35), "╳", 2, "★",
    ),
    "masterball": BallAnimationStyle(
        "masterball", "Masterball", _BALL_PURPLE, _BALL_WHITE, _BALL_PINK,
        (255, 205, 255), (190, 90, 255), "M", 3, "✦",
    ),
}


def _ball_style(slug: str) -> BallAnimationStyle:
    return _BALL_STYLES.get(slug, _BALL_STYLES["pokeball"])


class Cell:
    __slots__ = ("ch", "fg", "bg")

    def __init__(self, ch=" ", fg=None, bg=None):
        self.ch = ch
        self.fg = fg  # (r,g,b) o None
        self.bg = bg  # (r,g,b) o None

    def copy(self):
        return Cell(self.ch, self.fg, self.bg)

    def ansi(self) -> str:
        codes = ""
        if self.fg is not None:
            codes += f"\x1b[38;2;{self.fg[0]};{self.fg[1]};{self.fg[2]}m"
        if self.bg is not None:
            codes += f"\x1b[48;2;{self.bg[0]};{self.bg[1]};{self.bg[2]}m"
        if codes:
            return f"{codes}{self.ch}{_RESET}"
        return self.ch


def _parse_rgb(seq: str):
    # seq p.ej. "38;2;247;132;90" -> ('fg',(247,132,90)) | ("reset",None)
    parts = seq.split(";")
    if parts == ["0"] or parts == [""]:
        return ("reset", None)
    if len(parts) == 5 and parts[1] == "2":
        rgb = (int(parts[2]), int(parts[3]), int(parts[4]))
        return ("fg", rgb) if parts[0] == "38" else ("bg", rgb)
    return (None, None)


def _parse_sprite(ansi_text: str) -> list[list[Cell]]:
    """Convierte la salida ANSI de `krabby name` en una rejilla de celdas."""
    grid: list[list[Cell]] = []
    for raw_line in ansi_text.split("\n"):
        row: list[Cell] = []
        fg = bg = None
        i = 0
        for m in _ANSI_CODE.finditer(raw_line):
            # texto plano antes de este código -> celdas con el estilo actual
            for ch in raw_line[i:m.start()]:
                row.append(Cell(ch, fg, bg))
            kind, rgb = _parse_rgb(m.group()[2:-1])
            if kind == "reset":
                fg = bg = None
            elif kind == "fg":
                fg = rgb
            elif kind == "bg":
                bg = rgb
            i = m.end()
        for ch in raw_line[i:]:
            row.append(Cell(ch, fg, bg))
        grid.append(row)
    # normaliza anchura
    width = max((len(r) for r in grid), default=0)
    for r in grid:
        r.extend(Cell() for _ in range(width - len(r)))
    # recorta líneas totalmente vacías al principio/final
    def _blank(r):
        return all(c.ch == " " and c.bg is None for c in r)
    while grid and _blank(grid[0]):
        grid.pop(0)
    while grid and _blank(grid[-1]):
        grid.pop()
    return grid


def _clone(grid: list[list[Cell]]) -> list[list[Cell]]:
    return [[c.copy() for c in row] for row in grid]


def _grid_to_text(grid: list[list[Cell]]) -> Text:
    return Text.from_ansi("\n".join("".join(c.ansi() for c in row) for row in grid))


def _put(grid, r, c, ch, fg=None, bg=None):
    if 0 <= r < len(grid) and 0 <= c < len(grid[r]):
        grid[r][c] = Cell(ch, fg, bg)


def _ball_cell(style: BallAnimationStyle) -> Cell:
    return Cell("◉", style.bottom, style.top)


def _draw_pokeball(grid, r, c, style: BallAnimationStyle, tilt=0):
    """Dibuja una bola pequeña de 3×2 con bloques sólidos.

    La banda se concentra en el alojamiento central del botón: a este tamaño,
    extenderla a los lados ensancha visualmente la bola. ``(r, c)`` señala el
    centro superior.
    """
    c += tilt
    top, band, bot = style.top, style.accent, style.bottom
    # Cúpula rellena, con las esquinas exteriores recortadas.
    _put(grid, r, c - 1, "▟", top)
    _put(grid, r, c, " ", None, top)
    _put(grid, r, c + 1, "▙", top)

    # Base blanca. Los medios bloques forman un ecuador fino y el centro aloja
    # el botón blanco sobre su aro oscuro.
    _put(grid, r + 1, c - 1, "▜", bot)
    _put(grid, r + 1, c, "●", _BALL_WHITE, band)
    _put(grid, r + 1, c + 1, "▛", bot)


def _bezier_arc(steps: int, h: int, w: int, tr: int, tc: int):
    """Puntos (row,col) de un arco desde la esquina inferior izquierda hasta
    el objetivo (tr,tc), con altura para que se vea como un lanzamiento."""
    pts = []
    for k in range(steps):
        t = k / (steps - 1) if steps > 1 else 1.0
        col = int(round(t * tc))
        # parábola: empieza abajo, sube, y cae al objetivo
        base = (h - 1) + t * (tr - (h - 1))
        col_h = 4 * t * (1 - t)  # 0..1..0
        row = int(round(base - col_h * (h * 0.9)))
        pts.append((max(0, min(h - 1, row)), max(0, min(w - 1, col))))
    return pts


def _frames_composited(grid: list[list[Cell]], caught: bool, rng,
                       style: BallAnimationStyle):
    """Genera (renderable, delay) usando el sprite real como escenario."""
    h = len(grid)
    w = len(grid[0]) if grid else 0
    tr, tc = h // 2, w // 2  # objetivo del lanzamiento = centro del Pokémon

    # 1) Lanzamiento: la pokeball vuela por delante del Pokémon hasta él.
    arc = _bezier_arc(7 + style.tier, h, w, tr, tc)
    for index, (r, c) in enumerate(arc):
        f = _clone(grid)
        for tail in range(1, style.trail_length + 1):
            if index - tail < 0:
                continue
            tail_r, tail_c = arc[index - tail]
            glyph = "✦" if style.tier >= 2 and tail == 1 else "·"
            _put(f, tail_r, tail_c, glyph, style.accent)
        f[r][c] = _ball_cell(style)
        yield _grid_to_text(f), 0.05

    # 2) Impacto: las bolas mejores encadenan ondas de energía más amplias.
    for pulse in range(style.impact_pulses):
        flash = _clone(grid)
        row_radius = 1 + pulse
        col_radius = 2 + pulse * 2
        for dr in range(-row_radius, row_radius + 1):
            for dc in range(-col_radius, col_radius + 1):
                if abs(dr) == row_radius or abs(dc) == col_radius:
                    _put(flash, tr + dr, tc + dc, "░", style.flash)
        _put(flash, tr, tc, "✦", style.flash)
        yield _grid_to_text(flash), max(0.06, 0.12 - pulse * 0.015)

    # 3) Absorción: el Pokémon se vuelve energía y desaparece dentro de la bola.
    beam = _clone(grid)
    for row in beam:
        for cell in row:
            if cell.bg is not None or cell.ch != " ":
                cell.ch = "▓"
                cell.fg = style.absorb
                cell.bg = None
    _draw_pokeball(beam, tr, tc, style)
    yield _grid_to_text(beam), 0.14

    empty = [[Cell() for _ in range(w)] for _ in range(h)]
    _draw_pokeball(empty, tr, tc, style)
    yield _grid_to_text(empty), 0.12

    # Super/Ultra/Master comprimen la energía en anillos sucesivos.
    orbit = [(-2, 0), (-1, 3), (1, 3), (2, 0), (1, -3), (-1, -3)]
    for pulse in range(style.energy_pulses):
        charged = _clone(empty)
        radius = pulse + 1
        for dr, dc in orbit:
            _put(charged, tr + dr * radius, tc + dc * radius, "✧", style.absorb)
        _draw_pokeball(charged, tr, tc, style)
        yield _grid_to_text(charged), 0.1

    # 4) La bola cae al suelo y se bambolea. Cada bamboleo es una "comprobación":
    # si captura, aguanta las tres; si no, se abre a mitad de camino.
    ground = h - 2
    base = [[Cell() for _ in range(w)] for _ in range(h)]
    for c in range(w):  # línea de suelo
        _put(base, h - 1, c, "▁", (90, 90, 90))

    for r in range(tr, ground + 1):  # caída
        f = _clone(base)
        for trail in range(1, min(style.trail_length, r - tr + 1)):
            _put(f, r - trail, tc, "·", style.accent)
        _draw_pokeball(f, r, tc, style)
        yield _grid_to_text(f), 0.04

    n_wobbles = 3 if caught else rng.randint(1, 2)
    tilts = [-1, 1, -1]
    for i in range(n_wobbles):
        f = _clone(base)
        _draw_pokeball(f, ground, tc, style, tilt=tilts[i % 3])
        yield _grid_to_text(f), 0.3
        f = _clone(base)
        _draw_pokeball(f, ground, tc, style)
        yield _grid_to_text(f), 0.16

    if caught:
        # 5a) ¡Click! y chispas de captura.
        win = _clone(base)
        _draw_pokeball(win, ground, tc, style)
        sparks = [
            (-3, -6), (-4, 0), (-3, 6), (-1, -8), (-1, 8),
            (-5, -4), (-5, 4), (-2, -10), (-2, 10),
            (-6, 0), (-4, -9), (-4, 9),
            (-6, -7), (-6, 7), (-3, -12), (-3, 12),
        ]
        spark_count = 5 + style.tier * 3
        for dr, dc in sparks[:spark_count]:
            _put(win, ground + dr, tc + dc, "✧", style.flash)
        _put(win, ground - 2, tc, style.lock_glyph,
             (120, 240, 140) if style.tier == 0 else style.accent)
        yield _grid_to_text(win), 0.7
        if style.tier == 3:
            master = _clone(win)
            for dr, dc in [(-5, -5), (-5, 5), (-2, -7), (-2, 7)]:
                _put(master, ground + dr, tc + dc, "✦", style.absorb)
            _put(master, ground - 4, tc, "M", style.flash)
            yield _grid_to_text(master), 0.35
    else:
        # 5b) La bola se abre de golpe y el Pokémon vuelve a materializarse.
        burst = _clone(base)
        for (dr, dc) in [(-1, -2), (-2, 0), (-1, 2), (0, -3), (0, 3)]:
            _put(burst, ground + dr, tc + dc, "✦", style.accent)
        _put(burst, ground, tc, "✺", style.flash)
        yield _grid_to_text(burst), 0.25
        for _ in range(2):  # sacudida del Pokémon que se escapa
            shake = _clone(grid)
            yield _grid_to_text(shake), 0.12
            pad = [[Cell()] + row for row in grid]  # 1 col a la derecha
            yield _grid_to_text(pad), 0.12


def _frames_fallback(caught: bool, rng, style: BallAnimationStyle):
    """Animación mínima sin sprite (por si falla la captura del sprite)."""
    def canvas(lines):
        return Text("\n".join(l.ljust(21) for l in (lines + [""] * 5)[:5]))
    seq = []
    for c in range(0, 18, max(1, 3 - min(style.tier, 2))):
        trail = "✦" * style.tier
        seq.append((canvas(["", "", " " * c + trail + "◉"]), 0.06))
    for pulse in range(style.impact_pulses):
        seq.append((canvas(["", "", " " * pulse + "✺ ¡PLAF! ✺"]), 0.12))
    n_wobbles = 3 if caught else rng.randint(1, 2)
    for i in range(n_wobbles):
        tilt = "◐●" if i % 2 == 0 else "●◑"
        seq.append((canvas(["", "", f"      {tilt}", "     ‖‖‖‖‖"]), 0.35))
        seq.append((canvas(["", "", "       ●", "     ‖‖‖‖‖"]), 0.2))
    if caught:
        seq.append((canvas([
            "", "  ✨      ✨", f"  {style.lock_glyph} {style.name}: click",
            "  ✨      ✨",
        ]), 0.6))
    else:
        seq.append((canvas(["", "   ✦     ✦", "   ✺ ¡se soltó! ✺", "   ✦     ✦"]), 0.6))
    return seq


def _capture_grid(species: str, form: str, shiny: bool):
    args = ["krabby", "name", species, "--no-title"]
    if form != "regular":
        args += ["-f", form]
    if shiny:
        args.append("-s")
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    grid = _parse_sprite(out)
    return grid if grid and grid[0] else None


def play_capture_animation(console: Console, species: str, form: str, shiny: bool,
                           caught: bool, rng: random.Random | None = None,
                           ball_slug: str = "pokeball") -> None:
    """Lanza la bola elegida sobre el sprite real, lo absorbe y bambolea.

    Cada modelo aporta patrón, color y efectos crecientes. Si ``caught``, hace
    click y revela la captura; si no, se abre y el Pokémon sigue esperando.
    La animación es decorativa: cualquier fallo se ignora.
    """
    rng = rng or random
    style = _ball_style(ball_slug)
    try:
        try:
            grid = _capture_grid(species, form, shiny)
        except Exception:
            grid = None
        frames = (_frames_composited(grid, caught, rng, style) if grid
                  else _frames_fallback(caught, rng, style))
        with Live(console=console, refresh_per_second=30, transient=True) as live:
            for renderable, delay in frames:
                live.update(Align.center(renderable))
                time.sleep(delay)
        # Al capturar revelamos el nombre; si se escapó sigue siendo un misterio.
        krabby_bridge.render_sprite(species, form, shiny, show_title=caught, info=False)
    except Exception:
        pass


def _fit_evolution_grids(old: list[list[Cell]], new: list[list[Cell]]):
    """Centra ambas siluetas en un lienzo comun para evitar saltos visuales."""
    height = max(len(old), len(new)) + 4
    width = max(len(old[0]) if old else 0, len(new[0]) if new else 0) + 12

    def fit(source):
        canvas = [[Cell() for _ in range(width)] for _ in range(height)]
        top = (height - len(source)) // 2
        left = (width - (len(source[0]) if source else 0)) // 2
        for r, row in enumerate(source):
            for c, cell in enumerate(row):
                canvas[top + r][left + c] = cell.copy()
        return canvas

    return fit(old), fit(new)


def _silhouette(grid: list[list[Cell]], color=(248, 248, 230)):
    result = _clone(grid)
    for row in result:
        for cell in row:
            if cell.bg is not None or cell.ch != " ":
                cell.ch = "█" if cell.bg is not None else cell.ch
                cell.fg = color
                cell.bg = color if cell.bg is not None else None
    return result


def _evolution_frames(old_grid, new_grid, speed: float = 1.0):
    """Siluetas alternas cada vez mas deprisa, destello y artificio final."""
    old, new = _fit_evolution_grids(old_grid, new_grid)
    old_sil = _silhouette(old)
    new_sil = _silhouette(new)
    factor = max(0.1, float(speed))
    yield _grid_to_text(old), 0.9 * factor

    # La alternancia replica el pulso de los juegos clasicos: el Pokemon nuevo
    # ocupa exactamente el mismo escenario y va ganando presencia/frecuencia.
    delays = [0.42, 0.34, 0.27, 0.21, 0.16, 0.12, 0.09, 0.065]
    for index, delay in enumerate(delays):
        frame = _clone(new_sil if index % 2 == 0 else old_sil)
        center_r, center_c = len(frame) // 2, len(frame[0]) // 2
        radius = 2 + index
        for dr, dc in ((-radius, 0), (radius, 0), (0, -radius * 2), (0, radius * 2)):
            _put(frame, center_r + dr, center_c + dc, "✦", (255, 225, 90))
        yield _grid_to_text(frame), delay * factor

    # Flash blanco creciente antes de revelar el color de la evolucion.
    for glyph, delay in (("░", 0.10), ("▒", 0.10), ("▓", 0.12), ("█", 0.16)):
        flash = _clone(new_sil)
        for row in flash:
            for cell in row:
                if cell.bg is not None or cell.ch != " ":
                    cell.ch = glyph
                    cell.fg = (255, 255, 235)
                    cell.bg = None
        yield _grid_to_text(flash), delay * factor

    final = _clone(new)
    h, w = len(final), len(final[0])
    sparks = [
        (1, w // 2), (h - 2, w // 2), (h // 2, 2), (h // 2, w - 3),
        (2, 4), (2, w - 5), (h - 3, 5), (h - 3, w - 6),
    ]
    for r, c in sparks:
        _put(final, r, c, "✦", (255, 205, 60))
    yield _grid_to_text(final), 1.1 * factor


def play_evolution_animation(console: Console, old_species: str, old_form: str,
                             new_species: str, new_form: str, shiny: bool,
                             speed: float = 1.0) -> None:
    """Reproduce una evolucion completa; no modifica ningun dato guardado."""
    old_name = old_species.replace("-", " ").title()
    new_name = new_species.replace("-", " ").title()
    console.print(f"\n[bold yellow]¿Qué? ¡{old_name} está evolucionando![/]")
    try:
        old_grid = _capture_grid(old_species, old_form, shiny)
        new_grid = _capture_grid(new_species, new_form, shiny)
        if not old_grid or not new_grid:
            raise RuntimeError("sprite no disponible")
        with Live(console=console, refresh_per_second=30, transient=True) as live:
            for renderable, delay in _evolution_frames(old_grid, new_grid, speed):
                live.update(Align.center(renderable))
                time.sleep(delay)
        krabby_bridge.render_sprite(
            new_species, new_form, shiny, show_title=True, info=True
        )
    except Exception:
        # Incluso sin krabby se conserva la cadencia y el mensaje de revelacion.
        factor = max(0.1, float(speed))
        with Live(console=console, refresh_per_second=20, transient=True) as live:
            for index, delay in enumerate((0.5, 0.35, 0.22, 0.14, 0.09)):
                who = new_name if index % 2 else old_name
                live.update(Align.center(Text(f"✦  {who}  ✦", style="bold white")))
                time.sleep(delay * factor)
    console.print(f"[bold green]¡Enhorabuena! ¡{old_name} ha evolucionado a {new_name}![/]")
