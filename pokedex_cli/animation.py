import random
import re
import subprocess
import time

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


def _ball_cell() -> Cell:
    # Pokeball compacta en vuelo: círculo blanco con reborde rojo.
    return Cell("◉", _BALL_WHITE, _BALL_RED)


def _draw_pokeball(grid, r, c, tilt=0):
    """Dibuja una pokeball de 3x2 centrada en (r, c-columna central)."""
    top, bot = _BALL_RED, _BALL_WHITE
    _put(grid, r, c - 1, "▟", top)
    _put(grid, r, c, "█", top)
    _put(grid, r, c + 1, "▙", top)
    _put(grid, r + 1, c - 1, "▜", bot)
    _put(grid, r + 1, c, "◉", _BALL_DARK, bot)
    _put(grid, r + 1, c + 1, "▛", bot)
    if tilt:  # ligera inclinación al bambolearse
        _put(grid, r, c + tilt, "●", _BALL_RED)


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


def _frames_composited(grid: list[list[Cell]], caught: bool, rng):
    """Genera (renderable, delay) usando el sprite real como escenario."""
    h = len(grid)
    w = len(grid[0]) if grid else 0
    tr, tc = h // 2, w // 2  # objetivo del lanzamiento = centro del Pokémon

    # 1) Lanzamiento: la pokeball vuela por delante del Pokémon hasta él.
    for (r, c) in _bezier_arc(7, h, w, tr, tc):
        f = _clone(grid)
        f[r][c] = _ball_cell()
        yield _grid_to_text(f), 0.05

    # 2) Impacto: destello sobre el centro.
    flash = _clone(grid)
    for dr in (-1, 0, 1):
        for dc in (-2, -1, 0, 1, 2):
            _put(flash, tr + dr, tc + dc, "░", _FLASH)
    _put(flash, tr, tc, "✦", _FLASH)
    yield _grid_to_text(flash), 0.12

    # 3) Absorción: el Pokémon se vuelve energía y desaparece dentro de la bola.
    beam = _clone(grid)
    for row in beam:
        for cell in row:
            if cell.bg is not None or cell.ch != " ":
                cell.ch = "▓"
                cell.fg = _ABSORB
                cell.bg = None
    _draw_pokeball(beam, tr, tc)
    yield _grid_to_text(beam), 0.14

    empty = [[Cell() for _ in range(w)] for _ in range(h)]
    _draw_pokeball(empty, tr, tc)
    yield _grid_to_text(empty), 0.12

    # 4) La bola cae al suelo y se bambolea. Cada bamboleo es una "comprobación":
    # si captura, aguanta las tres; si no, se abre a mitad de camino.
    ground = h - 2
    base = [[Cell() for _ in range(w)] for _ in range(h)]
    for c in range(w):  # línea de suelo
        _put(base, h - 1, c, "▁", (90, 90, 90))

    for r in range(tr, ground + 1):  # caída
        f = _clone(base)
        _draw_pokeball(f, r, tc)
        yield _grid_to_text(f), 0.04

    n_wobbles = 3 if caught else rng.randint(1, 2)
    tilts = [-1, 1, -1]
    for i in range(n_wobbles):
        f = _clone(base)
        _draw_pokeball(f, ground, tc, tilt=tilts[i % 3])
        yield _grid_to_text(f), 0.3
        f = _clone(base)
        _draw_pokeball(f, ground, tc)
        yield _grid_to_text(f), 0.16

    if caught:
        # 5a) ¡Click! y chispas de captura.
        win = _clone(base)
        _draw_pokeball(win, ground, tc)
        for (dr, dc) in [(-3, -6), (-4, 0), (-3, 6), (-1, -8), (-1, 8)]:
            _put(win, ground + dr, tc + dc, "✧", _FLASH)
        _put(win, ground - 2, tc, "✓", (120, 240, 140))
        yield _grid_to_text(win), 0.7
    else:
        # 5b) La bola se abre de golpe y el Pokémon vuelve a materializarse.
        burst = _clone(base)
        for (dr, dc) in [(-1, -2), (-2, 0), (-1, 2), (0, -3), (0, 3)]:
            _put(burst, ground + dr, tc + dc, "✦", _BALL_RED)
        _put(burst, ground, tc, "✺", _FLASH)
        yield _grid_to_text(burst), 0.25
        for _ in range(2):  # sacudida del Pokémon que se escapa
            shake = _clone(grid)
            yield _grid_to_text(shake), 0.12
            pad = [[Cell()] + row for row in grid]  # 1 col a la derecha
            yield _grid_to_text(pad), 0.12


def _frames_fallback(caught: bool, rng):
    """Animación mínima sin sprite (por si falla la captura del sprite)."""
    def canvas(lines):
        return Text("\n".join(l.ljust(21) for l in (lines + [""] * 5)[:5]))
    seq = []
    for c in range(0, 18, 3):
        seq.append((canvas(["", "", " " * c + "◉"]), 0.06))
    seq.append((canvas(["", "", "   ✺ ¡PLAF! ✺"]), 0.3))
    n_wobbles = 3 if caught else rng.randint(1, 2)
    for i in range(n_wobbles):
        tilt = "◐●" if i % 2 == 0 else "●◑"
        seq.append((canvas(["", "", f"      {tilt}", "     ‖‖‖‖‖"]), 0.35))
        seq.append((canvas(["", "", "       ●", "     ‖‖‖‖‖"]), 0.2))
    if caught:
        seq.append((canvas(["", "  ✨      ✨", "     * click *", "  ✨      ✨"]), 0.6))
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
                           caught: bool, rng: random.Random | None = None) -> None:
    """Lanza la pokeball sobre el sprite real del Pokémon, lo absorbe y bambolea.
    Si `caught`, hace click y revela la captura con su nombre; si no, la bola se
    abre y el Pokémon (aún anónimo) se queda esperando otro intento. Decoración:
    cualquier fallo se traga."""
    rng = rng or random
    try:
        try:
            grid = _capture_grid(species, form, shiny)
        except Exception:
            grid = None
        frames = (_frames_composited(grid, caught, rng) if grid
                  else _frames_fallback(caught, rng))
        with Live(console=console, refresh_per_second=30, transient=True) as live:
            for renderable, delay in frames:
                live.update(Align.center(renderable))
                time.sleep(delay)
        # Al capturar revelamos el nombre; si se escapó sigue siendo un misterio.
        krabby_bridge.render_sprite(species, form, shiny, show_title=caught, info=False)
    except Exception:
        pass
