"""La Pokédex interactiva: `pokedex` sin argumentos en un TTY.

Carcasa de Pokédex clásica (plástico rojo biselado, lente azul, leds), lista
nacional navegable con búsqueda y filtros, y una "pantallita" LCD verde donde
cada especie se renderiza con efecto de carga retro: sprite a color si está
capturada, silueta braille si solo está vista, estática si aún no.

Todo el estado que necesita la app llega por inyección (catálogo, queries de
colección y renderer de sprites), de modo que los tests montan la app con
dobles sin tocar la base de datos real, el HOME ni la red.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from pokedex_cli.application.pokedex_catalog import CAPTURED, SEEN, CatalogEntry
from pokedex_cli.presentation import display
from pokedex_cli.presentation.tui import graphics, presenter
from pokedex_cli.presentation.tui.assets import POKEBALL_BRAILLE

SCREEN_WIDTH = 44
SCREEN_HEIGHT = 20
LCD_INK = "#0f380f"
REVEAL_INTERVAL = 0.02

SpriteFetcher = Callable[[str, str, bool], str | None]
CatalogLoader = Callable[[], list[CatalogEntry]]
CapturesLoader = Callable[[], list[dict[str, Any]]]


def _default_catalog_loader() -> list[CatalogEntry]:
    from pokedex_cli import composition

    return composition.pokedex_catalog().execute()


def _default_captures_loader() -> list[dict[str, Any]]:
    from pokedex_cli import composition

    composition.backfill_individuality().execute()
    return composition.collection_queries().captures()


def _default_sprite_fetcher(species: str, form: str, shiny: bool) -> str | None:
    from pokedex_cli import composition

    return composition.sprite_renderer().capture_sprite(species, form, shiny)


def static_noise(idx: int, width: int = 30, height: int = 9) -> list[str]:
    """Estática retro determinista para especies no vistas: siempre el mismo
    "ruido" para el mismo número de Pokédex."""
    rng = random.Random(idx)
    rows = []
    for _ in range(height):
        rows.append("".join(chr(0x2800 + rng.randint(1, 255)) for _ in range(width)))
    return rows


@dataclass
class _ScreenContent:
    """Líneas pendientes de revelar en la pantallita y cómo pintarlas."""

    lines: list[str]
    ansi: bool  # True: líneas ANSI de krabby; False: braille/texto plano
    style: str = ""


class PokedexScreenWidget(Static):
    """La pantallita LCD: revela su contenido línea a línea, como un CRT."""

    def __init__(self) -> None:
        super().__init__("", id="pantallita")
        self._content: _ScreenContent | None = None
        self._revealed = 0
        self._timer: Timer | None = None

    def show(self, content: _ScreenContent, *, instant: bool = False) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._content = content
        self._revealed = len(content.lines) if instant else 0
        self._render_revealed()
        if not instant and content.lines:
            self._timer = self.set_interval(REVEAL_INTERVAL, self._reveal_step)

    def _reveal_step(self) -> None:
        if self._content is None:
            return
        self._revealed += 1
        self._render_revealed()
        if self._revealed >= len(self._content.lines) and self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _render_revealed(self) -> None:
        if self._content is None:
            self.update("")
            return
        visible = self._content.lines[: self._revealed]
        scanline = self._revealed < len(self._content.lines)
        if self._content.ansi:
            text = Text.from_ansi("\n".join(visible))
        else:
            text = Text("\n".join(visible), style=self._content.style)
        if scanline and visible:
            text.append("\n")
            text.append("▔" * min(SCREEN_WIDTH - 4, 24), style="dim")
        self.update(text)


class SearchInput(Input):
    """El buscador: Escape devuelve el foco a la lista."""

    def on_key(self, event: Any) -> None:
        if getattr(event, "key", None) == "escape":
            event.stop()
            self.app.query_one("#lista", OptionList).focus()


class BootScreen(Screen[None]):
    """Arranque retro: Pokébola braille + título, ~1.2s."""

    DEFAULT_CSS = """
    BootScreen { background: #dc0a2d; align: center middle; }
    BootScreen #boot { content-align: center middle; width: auto; }
    """

    def compose(self) -> ComposeResult:
        lines = list(POKEBALL_BRAILLE)
        lines += ["", "[bold white]POKÉDEX[/]", "[grey93 blink]iniciando…[/]"]
        yield Static(Text.from_markup("\n".join(lines), justify="center"), id="boot")

    def on_mount(self) -> None:
        self.set_timer(1.2, self.dismiss)


class DetailScreen(Screen[None]):
    """Detalle de una especie capturada: sprite grande + ficha individual;
    ←/→ recorre tus capturas de esa especie."""

    BINDINGS = [
        Binding("escape,q", "dismiss", "Volver"),
        Binding("left", "previous_capture", "◀ captura"),
        Binding("right", "next_capture", "captura ▶"),
    ]

    DEFAULT_CSS = """
    DetailScreen { background: #1b1b1b; }
    DetailScreen #detalle-cuerpo { padding: 1 2; }
    DetailScreen #detalle-sprite { width: 46%; content-align: center middle; }
    DetailScreen #detalle-ficha { width: 54%; padding: 0 2; }
    """

    def __init__(
        self, app_ref: PokedexApp, entry: CatalogEntry, rows: list[dict[str, Any]]
    ) -> None:
        super().__init__()
        self._app_ref = app_ref
        self._entry = entry
        self._rows = rows
        self._index = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="detalle-cuerpo"):
            yield Static("", id="detalle-sprite")
            yield VerticalScroll(Static("", id="detalle-ficha"))
        yield Static(
            Text.from_markup("[dim]←/→ cambiar de captura · Escape volver[/]", justify="center"),
            id="detalle-pie",
        )

    def on_mount(self) -> None:
        self._show_current()

    def action_previous_capture(self) -> None:
        if self._rows:
            self._index = (self._index - 1) % len(self._rows)
            self._show_current()

    def action_next_capture(self) -> None:
        if self._rows:
            self._index = (self._index + 1) % len(self._rows)
            self._show_current()

    def _show_current(self) -> None:
        if not self._rows:
            self.query_one("#detalle-ficha", Static).update(
                Text.from_markup("[dim]Sin capturas de esta especie.[/]")
            )
            return
        row = self._rows[self._index]
        sprite = self._app_ref.sprite_cached(
            str(row["species"]), str(row["form"]), bool(row["shiny"])
        )
        sprite_widget = self.query_one("#detalle-sprite", Static)
        if sprite:
            sprite_widget.update(Text.from_ansi(sprite))
        else:
            sprite_widget.update(Text("(sin sprite)", style="dim"))
        self.query_one("#detalle-ficha", Static).update(self._ficha(row))

    def _ficha(self, row: dict[str, Any]) -> Text:
        name = display.display_name(str(row["species"]), str(row["form"]))
        lines: list[str] = []
        header = f"[bold]{name}[/]{display.gender_suffix(row.get('gender'))}"
        if row.get("shiny"):
            header += " [bold yellow1]✨[/]"
        counter = f"[dim]captura {self._index + 1}/{len(self._rows)} · id #{row['id']}[/]"
        lines.append(f"{header}   {counter}")
        lines.append(f"Nv. [bold]{row['level']}[/]")
        lines.append("")
        stats = row.get("stats") or {}
        ivs = row.get("ivs") or {}
        nature = row.get("nature")
        for label, key in display.STAT_LABELS:
            value = stats.get(key)
            if value is None:
                lines.append(f"{label:>9}  [dim]sin datos[/]")
                continue
            mark = "  "
            if nature is not None:
                if nature.plus == key:
                    mark = " [green3]▲[/]"
                elif nature.minus == key:
                    mark = " [red3]▼[/]"
            bar = display.stat_bar_current(int(value))
            colour = display.stat_color_current(int(value))
            base = row.get(key) or 0
            lines.append(
                f"{label:>9}{mark} {bar} [bold {colour}]{value:>3}[/]"
                f" [dim]base {base} · IV {ivs.get(key, 0)}[/]"
            )
        lines.append("")
        lines.append(display.nature_ability_markup(row))
        return Text.from_markup("\n".join(lines))


class PokedexApp(App[None]):
    """La Pokédex de sobremesa."""

    TITLE = "POKÉDEX"

    BINDINGS = [
        Binding("q", "quit", "Salir"),
        Binding("slash", "focus_search", "Buscar", key_display="/"),
        Binding("f", "cycle_status", "Filtro"),
        Binding("g", "cycle_gen", "Gen"),
        Binding("enter", "open_detail", "Detalle", show=True, priority=False),
    ]

    CSS = f"""
    Screen {{
        background: #dc0a2d;
    }}
    #carcasa {{
        border-top: thick #ff5d67;
        border-left: thick #ff5d67;
        border-bottom: thick #8b0000;
        border-right: thick #8b0000;
        background: #dc0a2d;
    }}
    #cabecera {{
        height: 3;
        padding: 0 2;
        content-align: left middle;
        background: #dc0a2d;
        color: #ffffff;
    }}
    #cuerpo {{ background: #dc0a2d; padding: 0 1; }}
    #columna-lista {{
        width: 42%;
        background: #2b0000;
        border: round #8b0000;
        margin: 0 1 0 0;
    }}
    #busqueda {{
        background: #1b0000;
        color: #f8d030;
        border: tall #8b0000;
    }}
    #lista {{
        background: #2b0000;
        color: #dedede;
        scrollbar-color: #8b0000;
    }}
    #lista > .option-list--option-highlighted {{
        background: #f8d030;
        color: #1b0000;
        text-style: bold;
    }}
    #columna-pantallas {{ width: 58%; }}
    #marco-pantallita {{
        border: tall #dedede;
        background: #9bbc0f;
        height: {SCREEN_HEIGHT};
        margin: 0 0 1 0;
    }}
    #pantallita {{
        background: #9bbc0f;
        color: {LCD_INK};
        content-align: center middle;
        height: 100%;
    }}
    #ficha {{
        border: round #8b0000;
        background: #1b1b1b;
        color: #dedede;
        padding: 0 1;
    }}
    Footer {{ background: #8b0000; color: #ffffff; }}
    """

    def __init__(
        self,
        *,
        catalog_loader: CatalogLoader = _default_catalog_loader,
        captures_loader: CapturesLoader = _default_captures_loader,
        sprite_fetcher: SpriteFetcher = _default_sprite_fetcher,
        skip_boot: bool = False,
    ) -> None:
        super().__init__()
        self._catalog_loader = catalog_loader
        self._captures_loader = captures_loader
        self._sprite_fetcher = sprite_fetcher
        self._skip_boot = skip_boot
        self._entries: list[CatalogEntry] = []
        self._filtered: list[CatalogEntry] = []
        self._status_filter: str | None = None
        self._gen_filter: int | None = None
        self._sprite_cache: dict[tuple[str, str, bool], str | None] = {}
        self._captures_rows: list[dict[str, Any]] | None = None

    # --- composición ------------------------------------------------------

    def compose(self) -> ComposeResult:
        from textual.widgets import Footer

        with Vertical(id="carcasa"):
            yield Static("", id="cabecera")
            with Horizontal(id="cuerpo"):
                with Vertical(id="columna-lista"):
                    yield SearchInput(placeholder="buscar nombre o número…", id="busqueda")
                    yield OptionList(id="lista")
                with Vertical(id="columna-pantallas"):
                    with Vertical(id="marco-pantallita"):
                        yield PokedexScreenWidget()
                    yield VerticalScroll(Static("", id="ficha-texto"), id="ficha")
            yield Footer()

    def on_mount(self) -> None:
        self._entries = self._catalog_loader()
        self._apply_filters(preserve_selection=False)
        self.query_one("#lista", OptionList).focus()
        if not self._skip_boot:
            self.push_screen(BootScreen())

    # --- estado / helpers ---------------------------------------------------

    def sprite_cached(self, species: str, form: str, shiny: bool) -> str | None:
        key = (species, form, shiny)
        if key not in self._sprite_cache:
            self._sprite_cache[key] = self._sprite_fetcher(species, form, shiny)
        return self._sprite_cache[key]

    def _captures(self) -> list[dict[str, Any]]:
        if self._captures_rows is None:
            self._captures_rows = self._captures_loader()
        return self._captures_rows

    def _selected_entry(self) -> CatalogEntry | None:
        option_list = self.query_one("#lista", OptionList)
        index = option_list.highlighted
        if index is None or not (0 <= index < len(self._filtered)):
            return None
        return self._filtered[index]

    def _apply_filters(self, *, preserve_selection: bool = True) -> None:
        option_list = self.query_one("#lista", OptionList)
        selected = self._selected_entry() if preserve_selection else None
        query = self.query_one("#busqueda", Input).value
        self._filtered = presenter.filter_entries(
            self._entries, query, self._status_filter, self._gen_filter
        )
        option_list.clear_options()
        option_list.add_options(
            [
                Option(Text.from_markup(presenter.list_row_markup(entry)), id=entry.slug)
                for entry in self._filtered
            ]
        )
        if self._filtered:
            index = 0
            if selected is not None:
                index = next(
                    (i for i, e in enumerate(self._filtered) if e.slug == selected.slug), 0
                )
            option_list.highlighted = index
        else:
            self._show_entry(None)
        self._update_header()

    def _update_header(self) -> None:
        progress = presenter.progress_summary(self._entries)
        status_label = presenter.STATUS_FILTER_LABELS[self._status_filter]
        gen_label = "Todas" if self._gen_filter is None else f"Gen {self._gen_filter}"
        header = (
            "[#85ddff]◉[/][#28aafd]◉[/]  [red1]●[/][yellow1]●[/][green1]●[/]"
            f"   [bold white]POKÉDEX[/]   [grey93]{progress}[/]"
            f"   [dim]f: {status_label} · g: {gen_label}[/]"
        )
        self.query_one("#cabecera", Static).update(Text.from_markup(header))

    # --- pantallita y ficha ---------------------------------------------------

    def _show_entry(self, entry: CatalogEntry | None) -> None:
        screen_widget = self.query_one(PokedexScreenWidget)
        ficha = self.query_one("#ficha-texto", Static)
        if entry is None:
            screen_widget.show(_ScreenContent([], ansi=False))
            ficha.update(Text.from_markup("[dim]Sin resultados.[/]"))
            return
        ficha.update(Text.from_markup("\n".join(presenter.detail_lines(entry))))
        if entry.status == CAPTURED or entry.status == SEEN:
            noise = _ScreenContent(static_noise(entry.idx, height=4), ansi=False, style=LCD_INK)
            screen_widget.show(noise, instant=True)
            self._load_sprite(entry)
        else:
            content = static_noise(entry.idx)
            content.insert(len(content) // 2, "")
            content.insert(len(content) // 2, "?")
            content.insert(len(content) // 2, "")
            screen_widget.show(_ScreenContent(content, ansi=False, style=LCD_INK))

    @work(thread=True, exclusive=True)
    def _load_sprite(self, entry: CatalogEntry) -> None:
        """Trae el sprite en un hilo (krabby es un subproceso) y lo pinta si
        la selección no ha cambiado mientras tanto."""
        sprite = self.sprite_cached(entry.slug, "regular", False)
        self.call_from_thread(self._sprite_ready, entry, sprite)

    def _sprite_ready(self, entry: CatalogEntry, sprite: str | None) -> None:
        current = self._selected_entry()
        if current is None or current.slug != entry.slug:
            return
        screen_widget = self.query_one(PokedexScreenWidget)
        if sprite is None:
            screen_widget.show(
                _ScreenContent(["(sin sprite: instala krabby)"], ansi=False, style=LCD_INK),
                instant=True,
            )
            return
        if entry.status == CAPTURED:
            screen_widget.show(_ScreenContent(sprite.split("\n"), ansi=True))
        else:
            silhouette = graphics.to_braille_silhouette(graphics.parse_ansi_sprite(sprite))
            screen_widget.show(_ScreenContent(silhouette, ansi=False, style=LCD_INK))

    # --- eventos ------------------------------------------------------------

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        index = event.option_index
        if 0 <= index < len(self._filtered):
            self._show_entry(self._filtered[index])

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "busqueda":
            self._apply_filters()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "busqueda":
            self.query_one("#lista", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_open_detail()

    # --- acciones -------------------------------------------------------------

    def action_focus_search(self) -> None:
        self.query_one("#busqueda", Input).focus()

    def action_cycle_status(self) -> None:
        self._status_filter = presenter.next_status_filter(self._status_filter)
        self._apply_filters()

    def action_cycle_gen(self) -> None:
        self._gen_filter = presenter.next_gen_filter(self._gen_filter)
        self._apply_filters()

    def action_open_detail(self) -> None:
        entry = self._selected_entry()
        if entry is None or entry.status != CAPTURED:
            return
        rows = [row for row in self._captures() if row["species"] == entry.slug]
        rows.sort(key=lambda row: -int(row["level"]))
        self.push_screen(DetailScreen(self, entry, rows))


def run_tui(
    *,
    catalog_loader: CatalogLoader = _default_catalog_loader,
    captures_loader: CapturesLoader = _default_captures_loader,
    sprite_fetcher: SpriteFetcher = _default_sprite_fetcher,
) -> int:
    app = PokedexApp(
        catalog_loader=catalog_loader,
        captures_loader=captures_loader,
        sprite_fetcher=sprite_fetcher,
    )
    app.run()
    return 0
