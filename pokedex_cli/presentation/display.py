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

BALL_LABELS = {
    "pokeball": ("Pokeball", "red3"),
    "superball": ("Superball", "dodger_blue1"),
    "ultraball": ("Ultraball", "gold1"),
    "masterball": ("Masterball", "magenta"),
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
    table.add_column("Nivel", justify="right")
    table.add_column("Tipos")
    table.add_column("Shiny", justify="center")
    table.add_column("Equipo", justify="center")
    table.add_column("Capturado")
    for row in rows:
        name = display_name(row["species"], row["form"]) + _gender_suffix(row.get("gender"))
        types = type_badges(row["types"]) if row["types"] else "?"
        dex = f"#{row['pokedex_id']:03d}" if row.get("pokedex_id") else "—"
        table.add_row(
            str(row["id"]),
            dex,
            name,
            str(row["level"]),
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
    "generation-i": "I",
    "generation-ii": "II",
    "generation-iii": "III",
    "generation-iv": "IV",
    "generation-v": "V",
    "generation-vi": "VI",
    "generation-vii": "VII",
    "generation-viii": "VIII",
    "generation-ix": "IX",
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


STAT_LABEL_BY_KEY = {key: label for label, key in STAT_LABELS}

GENDER_SYMBOLS = {
    "male": ("♂", "dodger_blue1"),
    "female": ("♀", "pink1"),
}


def _gender_suffix(gender: str | None) -> str:
    """Markup suffix for a gender symbol, or '' (genderless/unknown)."""
    symbol = GENDER_SYMBOLS.get(gender or "")
    if symbol is None:
        return ""
    glyph, color = symbol
    return f" [{color}]{glyph}[/]"


def _stat_color_current(value: int) -> str:
    """Colour scale for CURRENT (at-level) stats: 400 is roughly the
    practical ceiling for a Gen 3 stat at level 100."""
    if value >= 350:
        return "green1"
    if value >= 250:
        return "green3"
    if value >= 180:
        return "chartreuse3"
    if value >= 120:
        return "yellow3"
    if value >= 70:
        return "orange3"
    return "red3"


def _stat_bar_current(value: int, width: int = 16) -> str:
    v = value or 0
    filled = min(width, max(1, round(v / 400 * width))) if v else 0
    color = _stat_color_current(v)
    return f"[{color}]{'█' * filled}[/][grey30]{'━' * (width - filled)}[/]"


def _nature_ability_markup(row: dict) -> str:
    """`Naturaleza Firme (+Ataque −At. Esp.) · Habilidad Torrent`, with a
    hint to run `pokedex refresh` while gender/ability are still unknown."""
    nature = row.get("nature")
    if nature is None:
        nature_text = ""
    elif nature.plus is None:
        nature_text = f"Naturaleza {nature.name_es} (neutra)"
    else:
        plus_label = STAT_LABEL_BY_KEY.get(nature.plus, nature.plus)
        minus_label = STAT_LABEL_BY_KEY.get(nature.minus, nature.minus)
        nature_text = f"Naturaleza {nature.name_es} (+{plus_label} −{minus_label})"

    ability = row.get("ability")
    if ability:
        ability_text = f"Habilidad {ability.replace('-', ' ').title()}"
    else:
        ability_text = "Habilidad —"

    parts = [part for part in (nature_text, ability_text) if part]
    line = " · ".join(parts)
    if row.get("gender_rate") is None:
        line += "  [dim](datos pendientes: pokedex refresh)[/]"
    return line


def _nature_ability_line(row: dict) -> Text:
    return Text.from_markup(_nature_ability_markup(row))


# Public aliases kept deliberately thin: the TUI detail screen (Phase B)
# reuses this module's stat-bar/nature markup instead of duplicating it,
# without renaming or otherwise touching the CLI's existing call sites.
nature_ability_markup = _nature_ability_markup
stat_bar_current = _stat_bar_current
stat_color_current = _stat_color_current
gender_suffix = _gender_suffix


def render_vision_card(console: Console, row: dict, sprite: str | None) -> None:
    """Vista de detalle: sprite grande + ficha enriquecida, estilo pantalla
    de Pokédex. Las barras muestran las stats ACTUALES al nivel (fórmula
    Gen 3); la info base (stat base + IV) queda visible en sutil."""
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
    header.append(Text.from_markup(_gender_suffix(row.get("gender"))))
    if row["shiny"]:
        header.append("  ✨ shiny", style="bold yellow1")

    blocks: list = [header]
    if types:
        blocks.append(Text.from_markup("  " + type_badges(types)))

    # --- Stats con barras (actuales al nivel) --------------------------------
    if row["types"] is not None:
        nature = row.get("nature")
        ivs = row.get("ivs") or {}
        stats = row.get("stats") or {}
        stats_table = Table.grid(padding=(0, 1))
        stats_table.add_column(justify="right", style="bold grey70", min_width=9)
        stats_table.add_column()
        stats_table.add_column(justify="right", min_width=3)
        stats_table.add_column(justify="left")
        current_total = 0
        base_total = 0
        for label, key in STAT_LABELS:
            current = stats.get(key) or 0
            base = row.get(key) or 0
            current_total += current
            base_total += base
            mark = ""
            if nature is not None:
                if nature.plus == key:
                    mark = " [green3]▲[/]"
                elif nature.minus == key:
                    mark = " [red3]▼[/]"
            stats_table.add_row(
                f"{label}{mark}",
                _stat_bar_current(current),
                f"[bold {_stat_color_current(current)}]{current}[/]",
                f"[dim]base {base} · IV {ivs.get(key, 0)}[/]",
            )
        stats_table.add_row("", "", "", "")
        stats_table.add_row(
            "[bold]Total[/]",
            "",
            f"[bold {accent}]{current_total}[/]",
            f"[dim](base {base_total})[/]",
        )
        blocks.append(stats_table)
        if not row.get("form_data_exact", 1):
            blocks.append(
                Text(
                    "stats de la forma base (sin datos exactos de la variante)", style="dim italic"
                )
            )
        blocks.append(_nature_ability_line(row))
    else:
        blocks.append(
            Text("Sin datos enriquecidos todavía (se capturó sin conexión).", style="dim italic")
        )

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
        blocks.append(
            Panel(
                Text(row["flavor_text"], style="italic grey85"),
                box=box.ROUNDED,
                border_style="grey37",
                padding=(0, 1),
            )
        )

    # --- Pie: datos de la captura ------------------------------------------
    gen = GEN_ROMAN.get(row.get("generation") or "", None)
    foot = Text()
    level = int(row["level"])
    foot.append(f"Nv. {level}", style=f"bold {accent}")
    if not row["is_max_level"]:
        foot.append(
            f"  ·  EXP {row['experience_into_level']}/{row['experience_for_next_level']}   ·   ",
            style="grey54",
        )
    else:
        foot.append("  ·  EXP MAX   ·   ", style="gold3")
    foot.append("Capturado: ", style="grey54")
    foot.append(row["caught_at"][:10], style="grey85")
    foot.append(f"   ·   captura #{row['id']}", style="grey54")
    slug = (row.get("ball_slug") or "pokeball").replace("bola", "ball")
    ball_name, ball_color = BALL_LABELS.get(slug, BALL_LABELS["pokeball"])
    foot.append("   ·   ◉ ", style="grey54")
    foot.append(ball_name, style=f"bold {ball_color}")
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

    console.print(
        Panel(
            body,
            title=f"[bold {accent}]◓ POKÉDEX[/]",
            title_align="left",
            border_style=accent,
            box=box.DOUBLE_EDGE,
            padding=(1, 2),
            expand=False,
        )
    )


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
    table.add_column("Nivel", justify="right")
    table.add_column("Tipos")
    if not rows:
        console.print("Tu equipo está vacío. Añade con `pokedex equipo add <id>` (máx. 6).")
        return
    for row in rows:
        name = display_name(row["species"], row["form"])
        types = type_badges(row["types"]) if row["types"] else "?"
        table.add_row(str(row["id"]), name, str(row["level"]), types)
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
    table.add_column("Nivel", justify="right")
    table.add_column("Tipos")
    table.add_column("Total", justify="right")
    table.add_column("Base", justify="right")
    for i, row in enumerate(rows):
        medal = MEDALS[i] if i < len(MEDALS) else str(i + 1)
        name = display_name(row["species"], row["form"]) + _gender_suffix(row.get("gender"))
        table.add_row(
            medal,
            name,
            str(row["level"]),
            type_badges(row["types"]),
            str(row["total"]),
            f"[dim]{row['base_total']}[/]",
        )
    console.print(hall_of_fame_panel("Ranking (stats actuales)", table))
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
        table.add_row(
            medal, display_name(row["species"], row["form"]), type_badges(row["types"]), categoria
        )
    console.print(hall_of_fame_panel("Salón de la fama — Legendarios", table))
