"""La Pokédex interactiva: `pokedex` sin argumentos en un TTY.

Carcasa amarilla y naranja biselada, lente azul y leds; lista nacional
navegable con búsqueda y filtros; y una pantalla crema al estilo de Rojo Fuego donde cada
especie se renderiza con efecto de carga retro: sprite a color si está
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
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from pokedex_cli.application.pokedex_catalog import CAPTURED, UNSEEN, CatalogEntry
from pokedex_cli.presentation import display
from pokedex_cli.presentation.tui import graphics, presenter
from pokedex_cli.presentation.tui.assets import POKEBALL_BRAILLE

SCREEN_WIDTH = 44
LCD_INK = "#241d12"
LCD_PAPER = "#f8f8f8"
DEX_PAPER = "#e0d8c1"
DEX_OLIVE = "#a9c018"
DEX_RULE = "#6c552c"
REVEAL_INTERVAL = 0.02

# Colores de tipo (hex CSS para bordes de textual; display.TYPE_COLORS usa la
# paleta xterm de rich, que textual no entiende).
TYPE_HEX = {
    "normal": "#a8a878",
    "fire": "#f08030",
    "water": "#6890f0",
    "electric": "#f8d030",
    "grass": "#78c850",
    "ice": "#98d8d8",
    "fighting": "#c03028",
    "poison": "#a040a0",
    "ground": "#e0c068",
    "flying": "#a890f0",
    "psychic": "#f85888",
    "bug": "#a8b820",
    "rock": "#b8a038",
    "ghost": "#705898",
    "dragon": "#7038f8",
    "dark": "#705848",
    "steel": "#b8b8d0",
    "fairy": "#ee99ac",
}
DEFAULT_ACCENT = DEX_RULE

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


def _fitted_silhouette(sprite: str, max_rows: int, max_columns: int) -> list[str]:
    """Silueta tan grande como permita el espacio disponible."""
    grid = graphics.parse_ansi_sprite(sprite)
    for scale in (2, 1):
        lines = graphics.to_braille_silhouette(grid, scale=scale)
        if len(lines) <= max_rows and max((len(line) for line in lines), default=0) <= max_columns:
            return lines
    factor = 2
    while grid:
        lines = graphics.to_braille_silhouette(graphics.downscale(grid, factor), scale=1)
        if len(lines) <= max_rows and max((len(line) for line in lines), default=0) <= max_columns:
            return lines
        factor += 1
    return []


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

    def on_resize(self, event: events.Resize) -> None:
        app = self.app
        if isinstance(app, PokedexApp):
            self.call_after_refresh(app._rerender_selected_sprite)

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
        # OJO: el callback de un timer se `await`-ea si devuelve un awaitable,
        # y `dismiss()` devuelve uno; awaitearlo desde un handler de la propia
        # pantalla lanza ScreenError. El envoltorio descarta el awaitable.
        self.set_timer(1.2, self._finish_boot)

    def _finish_boot(self) -> None:
        if self.is_current:
            self.dismiss()


class DetailScreen(Screen[None]):
    """Ficha completa; ←/→ familia, ↑/↓ lista y ,/. individuos."""

    BINDINGS = [
        Binding("escape,q", "dismiss", "Volver"),
        Binding("left", "previous_evolution", "◀ evolución", priority=True),
        Binding("right", "next_evolution", "evolución ▶", priority=True),
        Binding("up", "previous_entry", "▲ Pokédex", priority=True),
        Binding("down", "next_entry", "Pokédex ▼", priority=True),
        Binding("comma", "previous_capture", "captura anterior"),
        Binding("full_stop", "next_capture", "captura siguiente"),
    ]

    def __init__(
        self, app_ref: PokedexApp, entry: CatalogEntry, rows: list[dict[str, Any]]
    ) -> None:
        super().__init__()
        self._app_ref = app_ref
        self._list = app_ref.detail_entries()
        self._list_index = self._list.index(entry)
        self._family = app_ref.evolution_family(entry)
        self._family_index = self._family.index(entry)
        self._entry = entry
        self._rows_by_species = {
            species: sorted(
                (row for row in rows if row["species"] == species),
                key=lambda row: -int(row["level"]),
            )
            for species in {str(row["species"]) for row in rows}
        }
        self._rows = self._rows_by_species.get(entry.slug, [])
        self._index = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="detalle-cuerpo"):
            with Vertical(id="detalle-visual"):
                with Vertical(id="detalle-pantalla"):
                    yield Static("", id="detalle-sprite")
                yield Static("", id="detalle-evoluciones")
            yield VerticalScroll(Static("", id="detalle-ficha"), id="detalle-datos")
        yield Static(
            Text.from_markup(
                f"[{presenter.TEXT_SECONDARY}]←/→ familia · ↑/↓ Pokédex · "
                ",/. individuo · Esc volver[/]",
                justify="center",
            ),
            id="detalle-pie",
        )

    def on_mount(self) -> None:
        # Panel al estilo de la vision card: marco con el color del tipo
        # primario y el nombre como título, sobre el papel crema de la Pokédex.
        self._show_current()

    def on_resize(self, event: events.Resize) -> None:
        self.call_after_refresh(self._render_sprite)

    def action_previous_evolution(self) -> None:
        if len(self._family) > 1:
            self._family_index = (self._family_index - 1) % len(self._family)
            self._select_family_member()

    def action_next_evolution(self) -> None:
        if len(self._family) > 1:
            self._family_index = (self._family_index + 1) % len(self._family)
            self._select_family_member()

    def _select_family_member(self) -> None:
        entry = self._family[self._family_index]
        if entry in self._list:
            self._list_index = self._list.index(entry)
        self._set_entry(entry)

    def action_previous_entry(self) -> None:
        if self._list:
            self._list_index = (self._list_index - 1) % len(self._list)
            self._set_entry(self._list[self._list_index])

    def action_next_entry(self) -> None:
        if self._list:
            self._list_index = (self._list_index + 1) % len(self._list)
            self._set_entry(self._list[self._list_index])

    def _set_entry(self, entry: CatalogEntry) -> None:
        self._entry = entry
        self._family = self._app_ref.evolution_family(entry)
        self._family_index = self._family.index(entry)
        self._rows = self._rows_by_species.get(entry.slug, [])
        self._index = 0
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
        primary = (
            self._entry.types[0] if self._entry.status != UNSEEN and self._entry.types else None
        )
        accent = TYPE_HEX.get(primary or "", DEFAULT_ACCENT)
        data_panel = self.query_one("#detalle-datos")
        data_panel.styles.border = ("double", accent)
        data_panel.border_title = f"◓ {presenter.visible_name(self._entry).upper()}"
        row = self._rows[self._index] if self._rows else None
        species_lines = presenter.detail_lines(self._entry, show_stat_bars=True)
        if row:
            species_lines.extend(
                [
                    "",
                    f"[bold {presenter.SECTION_TEXT}]INDIVIDUO[/]",
                    *self._individual_lines(row),
                ]
            )
        self.query_one("#detalle-ficha", Static).update(Text.from_markup("\n".join(species_lines)))
        self.query_one("#detalle-evoluciones", Static).update(self._evolution_strip())
        self.call_after_refresh(self._render_sprite)

    def _render_sprite(self) -> None:
        row = self._rows[self._index] if self._rows else None
        sprite = self._app_ref.sprite_cached(
            self._entry.slug,
            str(row["form"]) if row else "regular",
            bool(row["shiny"]) if row else False,
        )
        sprite_widget = self.query_one("#detalle-sprite", Static)
        if sprite:
            if self._entry.status == UNSEEN:
                silhouette = _fitted_silhouette(
                    sprite,
                    max(1, sprite_widget.content_size.height),
                    max(1, sprite_widget.content_size.width),
                )
                sprite_widget.update(Text("\n".join(silhouette), style=LCD_INK))
            else:
                fitted = graphics.fit_sprite(
                    sprite,
                    max(1, sprite_widget.content_size.height),
                    max(1, sprite_widget.content_size.width),
                )
                sprite_widget.update(Text.from_ansi(fitted))
        else:
            sprite_widget.update(Text("(sin sprite)", style="dim"))

    def _individual_lines(self, row: dict[str, Any]) -> list[str]:
        name = display.display_name(str(row["species"]), str(row["form"]))
        lines: list[str] = []
        header = f"[bold]{name}[/]{presenter.gender_suffix(row.get('gender'))}"
        if row.get("shiny"):
            header += f" [bold {presenter.DARK_AMBER}]✨[/]"
        counter = (
            f"[{presenter.TEXT_MUTED}]captura {self._index + 1}/{len(self._rows)} "
            f"· id #{row['id']}[/]"
        )
        lines.append(f"{header}   {counter}")
        lines.append(f"Nv. [bold]{row['level']}[/]")
        lines.append("")
        stats = row.get("stats") or {}
        ivs = row.get("ivs") or {}
        nature = row.get("nature")
        for label, key in display.STAT_LABELS:
            value = stats.get(key)
            if value is None:
                lines.append(f"{label:>9}  [{presenter.TEXT_MUTED}]sin datos[/]")
                continue
            mark = "  "
            if nature is not None:
                if nature.plus == key:
                    mark = f" [{presenter.DARK_GREEN}]▲[/]"
                elif nature.minus == key:
                    mark = f" [{presenter.DARK_RED}]▼[/]"
            bar = presenter.stat_bar_current(int(value))
            colour = presenter.stat_colour_current(int(value))
            base = row.get(key) or 0
            lines.append(
                f"{label:>9}{mark} {bar} [bold {colour}]{value:>3}[/]"
                f" [{presenter.TEXT_MUTED}]base {base} · IV {ivs.get(key, 0)}[/]"
            )
        lines.append("")
        lines.append(display.nature_ability_markup(row))
        return lines

    def _evolution_strip(self) -> Text:
        labels = []
        for index, entry in enumerate(self._family):
            marker, _ = presenter.STATUS_MARKERS[entry.status]
            name = presenter.visible_name(entry)
            selected = "bold reverse" if index == self._family_index else presenter.TEXT_PRIMARY
            labels.append(f"[{selected}]{marker} #{entry.idx:03d} {name}[/]")
        return Text.from_markup("  →  ".join(labels), justify="center")


class PokedexApp(App[None]):
    """La Pokédex de sobremesa."""

    TITLE = "POKÉDEX"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("q", "quit", "Salir"),
        Binding("slash,divide", "focus_search", "Buscar", key_display="/"),
        Binding("f", "cycle_status", "Filtro"),
        Binding("g", "cycle_gen", "Gen"),
        Binding("escape", "clear_filters", "Limpiar filtros", show=False),
        Binding("enter", "open_detail", "Detalle", show=True, priority=False),
    ]

    CSS = f"""
    Screen {{
        background: #17140c;
    }}
    #carcasa {{
        height: 100%;
        border-top: thick #f6a700;
        border-left: thick #f6a700;
        border-bottom: thick #9d4f00;
        border-right: thick #9d4f00;
        background: {DEX_OLIVE};
    }}
    #cabecera {{
        height: 3;
        padding: 0 2;
        content-align: left middle;
        background: {DEX_OLIVE};
        color: {LCD_INK};
        border-bottom: thick {DEX_RULE};
    }}
    #cuerpo {{ height: 1fr; min-height: 0; background: {DEX_PAPER}; padding: 0 1; }}
    #columna-lista {{
        width: 42%;
        height: 100%;
        min-height: 0;
        background: {DEX_PAPER};
        color: {LCD_INK};
        border: double {DEX_RULE};
        border-title-color: {DEX_RULE};
        margin: 0 1 0 0;
    }}
    #busqueda {{
        background: {LCD_PAPER};
        color: {LCD_INK};
        border: tall {DEX_OLIVE};
    }}
    #lista {{
        background: {DEX_PAPER};
        color: {LCD_INK};
        scrollbar-color: {DEX_OLIVE};
        scrollbar-background: #c4b99d;
    }}
    #lista > .option-list--option-highlighted {{
        background: {DEX_OLIVE};
        color: #111108;
        text-style: bold;
    }}
    #columna-pantallas {{ width: 58%; height: 100%; min-height: 0; }}
    #marco-pantallita {{
        border: double {DEX_RULE};
        border-title-color: {DEX_RULE};
        background: {LCD_PAPER};
        height: 75%;
        min-height: 0;
        margin: 0 0 1 0;
        padding: 0 1;
    }}
    #pantallita {{
        background: {LCD_PAPER};
        color: {LCD_INK};
        content-align: center middle;
        height: 100%;
    }}
    #ficha {{
        height: 25%;
        min-height: 0;
        border: double {DEX_RULE};
        border-title-color: {DEX_RULE};
        background: {DEX_PAPER};
        color: {LCD_INK};
        padding: 0 2;
    }}
    Footer {{ background: {DEX_RULE}; color: {LCD_PAPER}; }}
    Footer > .footer--key {{ background: {LCD_INK}; color: {LCD_PAPER}; text-style: bold; }}
    Footer > .footer--description {{ background: {DEX_PAPER}; color: {LCD_INK}; text-style: bold; }}
    DetailScreen {{ background: {DEX_OLIVE}; color: {LCD_INK}; }}
    DetailScreen #detalle-cuerpo {{
        margin: 1 2;
        background: {DEX_PAPER};
    }}
    DetailScreen #detalle-visual {{ width: 50%; margin: 0 1 0 0; }}
    DetailScreen #detalle-pantalla {{
        height: 1fr;
        border: double {DEX_RULE};
        background: {LCD_PAPER};
        padding: 1;
    }}
    DetailScreen #detalle-sprite {{
        height: 100%;
        background: {LCD_PAPER};
        content-align: center middle;
    }}
    DetailScreen #detalle-evoluciones {{
        height: 4;
        border: double {DEX_RULE};
        background: {DEX_OLIVE};
        color: {LCD_INK};
        content-align: center middle;
    }}
    DetailScreen #detalle-datos {{
        width: 50%;
        border: double {DEFAULT_ACCENT};
        border-title-color: {DEX_RULE};
        background: {DEX_PAPER};
        color: {LCD_INK};
        padding: 1 2;
    }}
    DetailScreen #detalle-pie {{
        height: 1;
        background: {DEX_OLIVE};
        color: {LCD_INK};
    }}
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
        self.query_one("#columna-lista").border_title = "LISTA NACIONAL"
        self.query_one("#marco-pantallita").border_title = "◓ PANTALLA"
        self.query_one("#ficha").border_title = "DATOS"
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

    def detail_entries(self) -> list[CatalogEntry]:
        """La ficha conserva exactamente el orden y filtros de la lista visible."""
        return list(self._filtered)

    def evolution_family(self, selected: CatalogEntry) -> list[CatalogEntry]:
        """Familia completa, incluso si las preevoluciones ya no son capturas vivas."""
        by_slug = {entry.slug: entry for entry in self._entries}
        neighbours: dict[str, set[str]] = {slug: set() for slug in by_slug}
        for entry in self._entries:
            family = [slug for slug in entry.evolution_family if slug in by_slug]
            for first, second in zip(family, family[1:]):
                neighbours[first].add(second)
                neighbours[second].add(first)
            for target in entry.evolution_targets:
                if target in by_slug:
                    neighbours[entry.slug].add(target)
                    neighbours[target].add(entry.slug)
        family_slugs: set[str] = set()
        pending = [selected.slug]
        while pending:
            slug = pending.pop()
            if slug in family_slugs:
                continue
            family_slugs.add(slug)
            pending.extend(neighbours.get(slug, ()))
        declared_orders = [
            entry.evolution_family
            for entry in self._entries
            if selected.slug in entry.evolution_family
        ]
        declared = max(declared_orders, key=len, default=())
        ordered_slugs = [slug for slug in declared if slug in family_slugs]
        ordered_slugs.extend(
            entry.slug
            for entry in sorted(self._entries, key=lambda item: item.idx)
            if entry.slug in family_slugs and entry.slug not in ordered_slugs
        )
        return [by_slug[slug] for slug in ordered_slugs]

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
            f"   [bold #241d12]POKÉDEX[/]   [#352a18]{progress}[/]"
            f"   [{presenter.TEXT_SECONDARY}]f: {status_label} · g: {gen_label}[/]"
        )
        self.query_one("#cabecera", Static).update(Text.from_markup(header))

    # --- pantallita y ficha ---------------------------------------------------

    def _show_entry(self, entry: CatalogEntry | None) -> None:
        screen_widget = self.query_one(PokedexScreenWidget)
        ficha = self.query_one("#ficha-texto", Static)
        if entry is None:
            screen_widget.show(_ScreenContent([], ansi=False))
            ficha.update(Text.from_markup(f"[{presenter.TEXT_MUTED}]Sin resultados.[/]"))
            return
        ficha.update(Text.from_markup("\n".join(presenter.detail_lines(entry))))
        # Estática breve mientras krabby responde; el render final depende del
        # estado: no vista → silueta braille, vista/capturada → sprite a color.
        noise = _ScreenContent(static_noise(entry.idx, height=4), ansi=False, style=LCD_INK)
        screen_widget.show(noise, instant=True)
        self._load_sprite(entry)

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
        if entry.status == UNSEEN:
            silhouette = _fitted_silhouette(
                sprite,
                max(1, screen_widget.content_size.height),
                max(1, screen_widget.content_size.width),
            )
            screen_widget.show(_ScreenContent(silhouette, ansi=False, style=LCD_INK))
        else:
            fitted = graphics.fit_sprite(
                sprite,
                max(1, screen_widget.content_size.height),
                max(1, screen_widget.content_size.width),
            )
            screen_widget.show(_ScreenContent(fitted.split("\n"), ansi=True))

    def on_resize(self, event: events.Resize) -> None:
        self.call_after_refresh(self._rerender_selected_sprite)

    def _rerender_selected_sprite(self) -> None:
        entry = self._selected_entry() if self._entries else None
        if entry is None:
            return
        key = (entry.slug, "regular", False)
        if key in self._sprite_cache:
            self._sprite_ready(entry, self._sprite_cache[key])

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

    def action_clear_filters(self) -> None:
        if self._status_filter is not None or self._gen_filter is not None:
            self._status_filter = None
            self._gen_filter = None
            self._apply_filters()

    def action_open_detail(self) -> None:
        entry = self._selected_entry()
        if entry is None or entry.status != CAPTURED:
            return
        self.push_screen(DetailScreen(self, entry, self._captures()))


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
