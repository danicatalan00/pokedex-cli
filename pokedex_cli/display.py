import json

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

MEDALS = ["🥇", "🥈", "🥉"]

TYPE_COLORS = {
    "normal": "grey70",
    "fire": "red3",
    "water": "dodger_blue1",
    "electric": "yellow1",
    "grass": "green3",
    "ice": "cyan1",
    "fighting": "dark_orange3",
    "poison": "purple",
    "ground": "gold3",
    "flying": "sky_blue2",
    "psychic": "magenta",
    "bug": "chartreuse3",
    "rock": "wheat4",
    "ghost": "dark_violet",
    "dragon": "blue_violet",
    "dark": "grey23",
    "steel": "grey58",
    "fairy": "pink1",
}


def display_name(species: str, form: str) -> str:
    label = species.replace("-", " ").title()
    if form != "regular":
        label += f" ({form})"
    return label


def type_badges(types: list[str]) -> str:
    return " ".join(f"[{TYPE_COLORS.get(t, 'white')}]{t}[/]" for t in types)


def hall_of_fame_panel(title: str, table: Table) -> Panel:
    return Panel(table, title=title, box=box.DOUBLE_EDGE, border_style="gold3", expand=False)


def render_list_table(console: Console, rows: list[dict]) -> None:
    if not rows:
        console.print("Aún no has capturado ningún Pokémon. Prueba `pokedex capturar`.")
        return
    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("#", justify="right")
    table.add_column("Nº Pokédex", justify="right")
    table.add_column("Pokémon")
    table.add_column("Tipos")
    table.add_column("Shiny", justify="center")
    table.add_column("Equipo", justify="center")
    table.add_column("Capturado")
    for row in rows:
        name = display_name(row["species"], row["form"])
        types = type_badges(row["types"]) if row["types"] else "?"
        dex = f"#{row['pokedex_id']:03d}" if row.get("pokedex_id") else "—"
        table.add_row(
            str(row["id"]),
            dex,
            name,
            types,
            "✨" if row["shiny"] else "",
            "⭐" if row["in_team"] else "",
            row["caught_at"][:10],
        )
    console.print(table)


def render_search_card(console: Console, species: str, form: str, cache) -> None:
    name = display_name(species, form)
    if cache is None:
        console.print(
            f"No se pudo obtener info de '{name}' "
            "(¿nombre incorrecto? ¿sin conexión? prueba `krabby list` para ver nombres válidos)."
        )
        return

    types = json.loads(cache["types"]) if cache["types"] else []
    lines = [type_badges(types)]
    if not cache["form_data_exact"]:
        lines.append("[dim](stats de la forma base; no hay datos exactos de esta variante)[/]")
    stats = Table.grid(padding=(0, 2))
    stats.add_column(justify="right")
    stats.add_column()
    for label, key in [
        ("HP", "hp"),
        ("Ataque", "atk"),
        ("Defensa", "def"),
        ("At. Esp.", "spa"),
        ("Def. Esp.", "spd"),
        ("Velocidad", "spe"),
    ]:
        stats.add_row(label, str(cache[key]))
    badges = []
    if cache["is_legendary"]:
        badges.append("[gold3]★ Legendario[/]")
    if cache["is_mythical"]:
        badges.append("[magenta]★ Singular[/]")

    body = "\n".join(lines)
    if cache["flavor_text"]:
        body += f"\n\n[italic]{cache['flavor_text']}[/]"
    console.print(Panel.fit(body, title=name, subtitle=" ".join(badges) or None))
    console.print(stats)


STAT_LABELS = [
    ("HP", "hp"),
    ("Ataque", "atk"),
    ("Defensa", "def"),
    ("At. Esp.", "spa"),
    ("Def. Esp.", "spd"),
    ("Velocidad", "spe"),
]

GEN_ROMAN = {
    "generation-i": "I", "generation-ii": "II", "generation-iii": "III",
    "generation-iv": "IV", "generation-v": "V", "generation-vi": "VI",
    "generation-vii": "VII", "generation-viii": "VIII", "generation-ix": "IX",
}


def _stat_color(value: int) -> str:
    if value >= 130:
        return "green1"
    if value >= 100:
        return "green3"
    if value >= 80:
        return "chartreuse3"
    if value >= 60:
        return "yellow3"
    if value >= 40:
        return "orange3"
    return "red3"


def _stat_bar(value: int, width: int = 16) -> str:
    v = value or 0
    filled = min(width, max(1, round(v / 200 * width))) if v else 0
    color = _stat_color(v)
    return f"[{color}]{'█' * filled}[/][grey30]{'━' * (width - filled)}[/]"


def render_vision_card(console: Console, row: dict, sprite: str | None) -> None:
    """Vista de detalle: sprite grande + ficha enriquecida, estilo pantalla
    de Pokédex."""
    species, form = row["species"], row["form"]
    name = display_name(species, form)
    types = row["types"] or []
    primary = types[0] if types else "normal"
    accent = TYPE_COLORS.get(primary, "cyan")

    # --- Cabecera -----------------------------------------------------------
    dex = f"#{row['pokedex_id']:03d}" if row.get("pokedex_id") else "#???"
    header = Text()
    header.append(f"N.º {dex}  ", style="bold grey62")
    header.append(name, style=f"bold {accent}")
    if row["shiny"]:
        header.append("  ✨ shiny", style="bold yellow1")

    blocks: list = [header]
    if types:
        blocks.append(Text.from_markup("  " + type_badges(types)))

    # --- Stats con barras ---------------------------------------------------
    if row["types"] is not None:
        stats = Table.grid(padding=(0, 1))
        stats.add_column(justify="right", style="bold grey70", min_width=9)
        stats.add_column()
        stats.add_column(justify="right", min_width=3)
        total = 0
        for label, key in STAT_LABELS:
            v = row[key] or 0
            total += v
            stats.add_row(label, _stat_bar(v), f"[{_stat_color(v)}]{v}[/]")
        stats.add_row("", "", "")
        stats.add_row("[bold]Total[/]", "", f"[bold {accent}]{total}[/]")
        blocks.append(stats)
        if not row.get("form_data_exact", 1):
            blocks.append(Text("stats de la forma base (sin datos exactos de la variante)",
                               style="dim italic"))
    else:
        blocks.append(Text("Sin datos enriquecidos todavía (se capturó sin conexión).",
                           style="dim italic"))

    # --- Rareza -------------------------------------------------------------
    badges = []
    if row["is_legendary"]:
        badges.append("[gold3]★ Legendario[/]")
    if row["is_mythical"]:
        badges.append("[magenta]✦ Singular[/]")
    if badges:
        blocks.append(Text.from_markup("  ".join(badges)))

    # --- Descripción --------------------------------------------------------
    if row.get("flavor_text"):
        blocks.append(Panel(
            Text(row["flavor_text"], style="italic grey85"),
            box=box.ROUNDED, border_style="grey37", padding=(0, 1),
        ))

    # --- Pie: datos de la captura ------------------------------------------
    gen = GEN_ROMAN.get(row.get("generation") or "", None)
    foot = Text()
    foot.append("Capturado: ", style="grey54")
    foot.append(row["caught_at"][:10], style="grey85")
    foot.append(f"   ·   captura #{row['id']}", style="grey54")
    if gen:
        foot.append(f"   ·   Gen {gen}", style="grey54")
    if row["in_team"]:
        foot.append("   ·   ", style="grey54")
        foot.append("⭐ en tu equipo", style="gold3")
    blocks.append(foot)

    info = Group(*_interleave(blocks))

    # --- Composición sprite | ficha ----------------------------------------
    if sprite:
        layout = Table.grid(padding=(0, 4))
        layout.add_column(vertical="middle")
        layout.add_column(vertical="middle")
        layout.add_row(Text.from_ansi(sprite), info)
        body: object = layout
    else:
        body = Group(
            Text("(instala krabby para ver el sprite)", style="dim italic"),
            Text(""),
            info,
        )

    console.print(Panel(
        body,
        title=f"[bold {accent}]◓ POKÉDEX[/]",
        title_align="left",
        border_style=accent,
        box=box.DOUBLE_EDGE,
        padding=(1, 2),
        expand=False,
    ))


def _interleave(blocks: list) -> list:
    """Mete una línea en blanco entre bloques para respirar."""
    out: list = []
    for i, b in enumerate(blocks):
        out.append(b)
        if i < len(blocks) - 1:
            out.append(Text(""))
    return out


def render_team_panel(console: Console, rows: list[dict]) -> None:
    table = Table(box=box.SIMPLE)
    table.add_column("ID", justify="right")
    table.add_column("Pokémon")
    table.add_column("Tipos")
    if not rows:
        console.print("Tu equipo está vacío. Añade con `pokedex equipo add <id>` (máx. 6).")
        return
    for row in rows:
        name = display_name(row["species"], row["form"])
        types = type_badges(row["types"]) if row["types"] else "?"
        table.add_row(str(row["id"]), name, types)
    console.print(hall_of_fame_panel("Tu equipo", table))


def render_tipos_breakdown(console: Console, counts: dict[str, int]) -> None:
    if not counts:
        console.print("Aún no hay datos de tipos (captura algún Pokémon con conexión).")
        return
    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("Tipo")
    table.add_column("Cantidad", justify="right")
    for tname, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        table.add_row(type_badges([tname]), str(count))
    console.print(table)


def render_ranking_table(console: Console, rows: list[dict], missing: int) -> None:
    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("")
    table.add_column("Pokémon")
    table.add_column("Tipos")
    table.add_column("Total stats", justify="right")
    for i, row in enumerate(rows):
        medal = MEDALS[i] if i < len(MEDALS) else str(i + 1)
        name = display_name(row["species"], row["form"])
        table.add_row(medal, name, type_badges(row["types"]), str(row["total"]))
    console.print(hall_of_fame_panel("Ranking (suma de stats base)", table))
    if missing:
        console.print(
            f"[dim]{missing} captura(s) sin datos enriquecidos (sin conexión al capturarlas), "
            "excluidas del ranking.[/]"
        )


def render_legendarios_panel(console: Console, rows: list[dict]) -> None:
    if not rows:
        console.print("Aún no has capturado ningún legendario o singular.")
        return
    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("")
    table.add_column("Pokémon")
    table.add_column("Tipos")
    table.add_column("Categoría")
    for i, row in enumerate(rows):
        medal = MEDALS[i] if i < len(MEDALS) else "•"
        categoria = "Singular" if row["is_mythical"] else "Legendario"
        table.add_row(medal, display_name(row["species"], row["form"]), type_badges(row["types"]), categoria)
    console.print(hall_of_fame_panel("Salón de la fama — Legendarios", table))
