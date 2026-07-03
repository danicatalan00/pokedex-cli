import time

from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.text import Text

from pokedex_cli import krabby_bridge

CANVAS_WIDTH = 21
CANVAS_HEIGHT = 5


def _canvas(lines: list[str]) -> Text:
    padded = [line.ljust(CANVAS_WIDTH) for line in lines]
    while len(padded) < CANVAS_HEIGHT:
        padded.append(" " * CANVAS_WIDTH)
    return Text("\n".join(padded[:CANVAS_HEIGHT]))


def _throw_frame(t: float) -> Text:
    col = min(CANVAS_WIDTH - 1, int(t * (CANVAS_WIDTH - 1)))
    row = round(4 - 4 * (4 * t * (1 - t)))
    lines = [""] * CANVAS_HEIGHT
    lines[row] = " " * col + "●"
    return _canvas(lines)


def _impact_frame() -> Text:
    return _canvas(["", "", "   ✺ ¡PLAF! ✺", "", ""])


def _wobble_frame(tilt: str) -> Text:
    ball = " ◐●" if tilt == "left" else " ●◑"
    return _canvas(["", "", f"      {ball}", "     ‖‖‖‖‖", ""])


def _pause_frame() -> Text:
    return _canvas(["", "", "       ●", "     ‖‖‖‖‖", ""])


def _success_frame() -> Text:
    return _canvas(["", "  ✨      ✨", "     * click *", "  ✨      ✨", ""])


def _frames() -> list[tuple[Text, float]]:
    return [
        (_throw_frame(0.0), 0.12),
        (_throw_frame(0.25), 0.12),
        (_throw_frame(0.5), 0.12),
        (_throw_frame(0.75), 0.12),
        (_throw_frame(1.0), 0.12),
        (_impact_frame(), 0.3),
        (_wobble_frame("left"), 0.35),
        (_pause_frame(), 0.2),
        (_wobble_frame("right"), 0.35),
        (_pause_frame(), 0.2),
        (_wobble_frame("left"), 0.35),
        (_pause_frame(), 0.2),
        (_success_frame(), 0.6),
    ]


def play_capture_animation(console: Console, species: str, form: str, shiny: bool) -> None:
    """Plays the pokeball throw/wobble/success animation, then reveals the
    real krabby sprite. Decoration only: any failure here is swallowed so it
    never blocks a capture that's already been saved to the database."""
    try:
        with Live(console=console, refresh_per_second=12, transient=True) as live:
            for renderable, delay in _frames():
                live.update(Align.center(renderable))
                time.sleep(delay)
        krabby_bridge.render_sprite(species, form, shiny, show_title=True, info=False)
    except Exception:
        pass
