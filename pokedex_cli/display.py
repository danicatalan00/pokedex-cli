import json

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

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
    table.add_column("ID", justify="right")
    table.add_column("Pokémon")
    table.add_column("Tipos")
    table.add_column("Shiny", justify="center")
    table.add_column("Equipo", justify="center")
    table.add_column("Capturado")
    for row in rows:
        name = display_name(row["species"], row["form"])
        types = type_badges(row["types"]) if row["types"] else "?"
        table.add_row(
            str(row["id"]),
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
