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


def detail_lines(entry: CatalogEntry) -> list[str]:
    """Lines of markup text for the ficha (bottom-right panel)."""
    if entry.status == UNSEEN:
        return [
            f"[dim]{HIDDEN_NAME}[/]",
            f"Gen {entry.gen} · sin datos. Sigue abriendo terminales…",
        ]
    lines = [f"[bold]{visible_name(entry)}[/]"]
    lines.append(f"Gen {entry.gen} · avistamientos: {entry.times_seen}")
    if entry.types:
        lines.append(" · ".join(entry.types))
    if entry.description:
        lines.append(f"[italic]{entry.description}[/]")
    if entry.status == CAPTURED:
        shiny_note = " · ✨ shiny" if entry.any_shiny else ""
        lines.append(f"Capturas: {entry.captures_count} · nivel máx. {entry.max_level}{shiny_note}")
    return lines
