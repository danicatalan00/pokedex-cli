"""Pure presentation logic for the Pokédex TUI: filtering, list rows and the
ficha's markup text. No `textual` import here on purpose — this module stays
trivially unit-testable without spinning up an App.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from pokedex_cli.application.pokedex_catalog import CAPTURED, SEEN, UNSEEN, CatalogEntry

HIDDEN_NAME = "??????"

# status -> (marker glyph, rich colour)
STATUS_MARKERS: dict[str, tuple[str, str]] = {
    CAPTURED: ("●", "red3"),
    SEEN: ("◐", "yellow3"),
    UNSEEN: ("·", "grey42"),
}

# Cycle order for the `f` binding: Todos -> Capturados -> Vistos -> Pendientes.
STATUS_FILTERS: tuple[str | None, ...] = (None, CAPTURED, SEEN, UNSEEN)
STATUS_FILTER_LABELS: dict[str | None, str] = {
    None: "Todos",
    CAPTURED: "Capturados",
    SEEN: "Vistos",
    UNSEEN: "Pendientes",
}
MAX_GENERATION = 9

STAT_SHORT_LABELS = {
    "hp": "PS",
    "atk": "Ata",
    "def": "Def",
    "spa": "AtEs",
    "spd": "DfEs",
    "spe": "Vel",
}

HABITAT_LABELS = {
    "cave": "Cueva",
    "forest": "Bosque",
    "grassland": "Pradera",
    "mountain": "Montaña",
    "rare": "Raro",
    "rough-terrain": "Terreno abrupto",
    "sea": "Mar",
    "urban": "Urbano",
    "waters-edge": "Orilla",
}
COLOR_LABELS = {
    "black": "Negro",
    "blue": "Azul",
    "brown": "Marrón",
    "gray": "Gris",
    "green": "Verde",
    "pink": "Rosa",
    "purple": "Morado",
    "red": "Rojo",
    "white": "Blanco",
    "yellow": "Amarillo",
}
EGG_GROUP_LABELS = {
    "bug": "Bicho",
    "ditto": "Ditto",
    "dragon": "Dragón",
    "fairy": "Hada",
    "field": "Campo",
    "flying": "Volador",
    "grass": "Planta",
    "human-like": "Humanoide",
    "mineral": "Mineral",
    "monster": "Monstruo",
    "water1": "Agua 1",
    "water2": "Agua 2",
    "water3": "Agua 3",
    "no-eggs": "No cría",
}
GROWTH_LABELS = {
    "erratic": "Errático",
    "fast": "Rápido",
    "fluctuating": "Fluctuante",
    "medium": "Medio",
    "medium-slow": "Medio-lento",
    "slow": "Lento",
}


def _human_label(value: str) -> str:
    return value.replace("-", " ").title()


def _fold(text: str) -> str:
    """Case/accent-insensitive fold, e.g. 'Nidoran♀' ~ 'nidoran'."""
    normalised = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalised if not unicodedata.combining(ch)).lower()


def visible_name(entry: CatalogEntry) -> str:
    """The name shown to the player: hidden unless the species has been
    seen or captured at least once."""
    return entry.name if entry.status != UNSEEN else HIDDEN_NAME


def filter_entries(
    entries: Sequence[CatalogEntry],
    query: str,
    status_filter: str | None,
    gen_filter: int | None,
) -> list[CatalogEntry]:
    """Substring name match (case/accent-insensitive) or exact dex number,
    combined with an optional status and generation filter."""
    query = query.strip()
    is_dex_number = query.isdigit()
    folded_query = _fold(query)
    result: list[CatalogEntry] = []
    for entry in entries:
        if status_filter is not None and entry.status != status_filter:
            continue
        if gen_filter is not None and entry.gen != gen_filter:
            continue
        if query:
            if is_dex_number:
                if entry.idx != int(query):
                    continue
            elif folded_query not in _fold(entry.name):
                continue
        result.append(entry)
    return result


def next_status_filter(current: str | None) -> str | None:
    index = STATUS_FILTERS.index(current)
    return STATUS_FILTERS[(index + 1) % len(STATUS_FILTERS)]


def next_gen_filter(current: int | None, max_generation: int = MAX_GENERATION) -> int | None:
    """None -> 1 -> 2 -> ... -> max_generation -> None."""
    if current is None:
        return 1
    if current >= max_generation:
        return None
    return current + 1


def list_row_markup(entry: CatalogEntry) -> str:
    """One line of the national list: '#025 ● Pikachu ✨'."""
    marker, colour = STATUS_MARKERS[entry.status]
    name = visible_name(entry)
    name_style = "dim" if entry.status == UNSEEN else "bold"
    shiny = " ✨" if entry.any_shiny else ""
    return f"#{entry.idx:03d} [{colour}]{marker}[/] [{name_style}]{name}[/]{shiny}"


def progress_summary(entries: Sequence[CatalogEntry]) -> str:
    """'Capturados 12 · Vistos 48 / 1010' header line."""
    total = len(entries)
    captured = sum(1 for entry in entries if entry.status == CAPTURED)
    seen = captured + sum(1 for entry in entries if entry.status == SEEN)
    return f"Capturados {captured} · Vistos {seen} / {total}"


def detail_lines(entry: CatalogEntry, *, show_stat_bars: bool = False) -> list[str]:
    """Lines of markup text for the ficha (bottom-right panel).

    Semántica de Pokédex del juego: una especie solo vista enseña nombre,
    tipos y poco más; la entrada completa (stats base, descripción) se
    desbloquea al capturarla.
    """
    if entry.status == UNSEEN:
        return [
            f"[dim bold]{HIDDEN_NAME}[/]",
            "",
            f"[dim]Gen {entry.gen} · especie sin identificar.[/]",
            "[dim]Sigue abriendo terminales…[/]",
        ]

    from pokedex_cli.presentation import display

    lines = [f"[bold]#{entry.idx:03d} {visible_name(entry)}[/]"]
    if entry.types:
        lines.append(display.type_badges(list(entry.types)))
    lines.append(f"[grey62]Generación {entry.gen}[/]")

    if entry.status != CAPTURED:
        lines.append("")
        lines.append("[dim italic]Datos incompletos: captúralo para registrar su entrada.[/]")
        return lines

    if entry.base_stats:
        lines.append("")
        if show_stat_bars:
            for key, value in entry.base_stats:
                label = STAT_SHORT_LABELS.get(key, key.upper())
                bar = display.stat_bar_base(value, width=12)
                colour = display.stat_color_base(value)
                lines.append(f"[grey70]{label:>5}[/] {bar} [{colour}]{value:>3}[/]")
        else:
            stats = " · ".join(
                f"[grey70]{STAT_SHORT_LABELS.get(key, key.upper())}[/] [bold]{value}[/]"
                for key, value in entry.base_stats
            )
            lines.append(stats)
        total = sum(value for _, value in entry.base_stats)
        lines.append(f"[grey70]Total[/] [bold]{total}[/] [dim](stats base)[/]")
    if show_stat_bars:
        profile: list[str] = []
        if entry.genus:
            profile.append(f"[italic]{entry.genus}[/]")
        physical = []
        if entry.height_dm is not None:
            physical.append(f"Altura [bold]{entry.height_dm / 10:g} m[/]")
        if entry.weight_hg is not None:
            physical.append(f"Peso [bold]{entry.weight_hg / 10:g} kg[/]")
        if physical:
            profile.append(" · ".join(physical))
        taxonomy = []
        if entry.habitat:
            taxonomy.append(
                f"Hábitat [bold]{HABITAT_LABELS.get(entry.habitat, _human_label(entry.habitat))}[/]"
            )
        if entry.color:
            taxonomy.append(
                f"Color [bold]{COLOR_LABELS.get(entry.color, _human_label(entry.color))}[/]"
            )
        if entry.shape:
            taxonomy.append(f"Forma [bold]{_human_label(entry.shape)}[/]")
        if taxonomy:
            profile.append(" · ".join(taxonomy))
        if entry.egg_groups:
            groups = ", ".join(
                EGG_GROUP_LABELS.get(group, _human_label(group)) for group in entry.egg_groups
            )
            profile.append(f"Grupos huevo [bold]{groups}[/]")
        if profile:
            lines.extend(["", "[bold dark_orange]PERFIL[/]", *profile])

        training = []
        if entry.growth_rate:
            growth = GROWTH_LABELS.get(entry.growth_rate, _human_label(entry.growth_rate))
            training.append(f"Crecimiento [bold]{growth}[/]")
        if entry.base_experience is not None:
            training.append(f"Experiencia base [bold]{entry.base_experience}[/]")
        if entry.capture_rate is not None:
            training.append(f"Captura [bold]{entry.capture_rate}/255[/]")
        if entry.base_happiness is not None:
            training.append(f"Amistad base [bold]{entry.base_happiness}[/]")
        if entry.hatch_counter is not None:
            training.append(f"Eclosión [bold]{entry.hatch_counter} ciclos[/]")
        if entry.abilities:
            abilities = ", ".join(_human_label(ability) for ability in entry.abilities)
            training.append(f"Habilidades [bold]{abilities}[/]")
        if training:
            lines.extend(["", "[bold dark_orange]CRIANZA Y PROGRESIÓN[/]", *training])
    if entry.description:
        lines.append("")
        lines.append(f"[italic grey85]{entry.description}[/]")
    lines.append("")
    if entry.captures_count:
        if entry.any_shiny:
            lines.append("[bold yellow1]✨ Variante shiny registrada[/]")
    else:
        lines.append("[gold3]Registrado en tu Pokédex[/] [dim](la captura evolucionó)[/]")
    return lines
