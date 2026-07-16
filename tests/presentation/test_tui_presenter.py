import dataclasses

from pokedex_cli.application.pokedex_catalog import CatalogEntry
from pokedex_cli.presentation.tui import presenter


def entry(
    idx,
    slug,
    name,
    *,
    gen=1,
    status="unseen",
    types=None,
    captures_count=0,
    max_level=None,
    any_shiny=False,
    times_seen=0,
    description=None,
) -> CatalogEntry:
    return CatalogEntry(
        idx=idx,
        slug=slug,
        name=name,
        gen=gen,
        status=status,
        types=types,
        captures_count=captures_count,
        max_level=max_level,
        any_shiny=any_shiny,
        times_seen=times_seen,
        description=description,
    )


ENTRIES = [
    entry(1, "bulbasaur", "Bulbasaur", gen=1, status="captured", captures_count=1, max_level=10),
    entry(25, "pikachu", "Pikachu", gen=1, status="seen", times_seen=3),
    entry(150, "mewtwo", "Mewtwo", gen=1, status="unseen"),
    entry(155, "cyndaquil", "Cyndaquil", gen=2, status="seen", times_seen=1),
]


def test_filter_by_text_query_is_case_and_accent_insensitive_substring():
    assert [e.slug for e in presenter.filter_entries(ENTRIES, "pika", None, None)] == ["pikachu"]
    assert [e.slug for e in presenter.filter_entries(ENTRIES, "PIKA", None, None)] == ["pikachu"]
    assert [e.slug for e in presenter.filter_entries(ENTRIES, "cyndaquil", None, None)] == [
        "cyndaquil"
    ]


def test_filter_by_dex_number_matches_the_exact_idx():
    assert [e.slug for e in presenter.filter_entries(ENTRIES, "25", None, None)] == ["pikachu"]
    assert presenter.filter_entries(ENTRIES, "999", None, None) == []


def test_filter_by_status():
    assert [e.slug for e in presenter.filter_entries(ENTRIES, "", "captured", None)] == [
        "bulbasaur"
    ]
    assert {e.slug for e in presenter.filter_entries(ENTRIES, "", "seen", None)} == {
        "pikachu",
        "cyndaquil",
    }
    assert [e.slug for e in presenter.filter_entries(ENTRIES, "", "unseen", None)] == ["mewtwo"]


def test_filter_by_generation():
    assert {e.slug for e in presenter.filter_entries(ENTRIES, "", None, 1)} == {
        "bulbasaur",
        "pikachu",
        "mewtwo",
    }
    assert [e.slug for e in presenter.filter_entries(ENTRIES, "", None, 2)] == ["cyndaquil"]


def test_filters_combine_with_text_query():
    assert presenter.filter_entries(ENTRIES, "cyndaquil", "captured", None) == []
    assert [e.slug for e in presenter.filter_entries(ENTRIES, "cyn", "seen", 2)] == ["cyndaquil"]


def test_unseen_species_names_are_hidden_everywhere():
    mewtwo = ENTRIES[2]
    assert presenter.visible_name(mewtwo) == "??????"
    assert "??????" in presenter.list_row_markup(mewtwo)
    assert "Mewtwo" not in presenter.list_row_markup(mewtwo)
    lines = presenter.detail_lines(mewtwo)
    assert "Mewtwo" not in "".join(lines)
    assert any("??????" in line for line in lines)
    assert any("Gen 1" in line for line in lines)


def test_seen_and_captured_species_names_are_visible():
    pikachu = ENTRIES[1]
    assert presenter.visible_name(pikachu) == "Pikachu"
    assert "Pikachu" in presenter.list_row_markup(pikachu)


def test_status_filter_cycles_todos_capturados_vistos_pendientes():
    assert presenter.next_status_filter(None) == "captured"
    assert presenter.next_status_filter("captured") == "seen"
    assert presenter.next_status_filter("seen") == "unseen"
    assert presenter.next_status_filter("unseen") is None


def test_generation_filter_cycles_from_none_through_nine_and_back():
    current = None
    seen = [current]
    for _ in range(10):
        current = presenter.next_gen_filter(current)
        seen.append(current)
    assert seen == [None, 1, 2, 3, 4, 5, 6, 7, 8, 9, None]


def test_progress_summary_counts_captured_and_seen_out_of_the_total():
    assert presenter.progress_summary(ENTRIES) == "Capturados 1 · Vistos 3 / 4"


def test_detail_lines_for_a_captured_entry_omit_redundant_capture_metadata():
    bulbasaur = ENTRIES[0]
    lines = presenter.detail_lines(bulbasaur)
    assert lines[0] == "[bold]#001 Bulbasaur[/]"
    joined = "\n".join(lines)
    assert "Capturas:" not in joined
    assert "nivel máx." not in joined


def test_detail_lines_captured_show_base_stats_and_description():
    entry_full = entry(
        7,
        "squirtle",
        "Squirtle",
        status="captured",
        captures_count=2,
        max_level=15,
        types=("water",),
        description="Se esconde en su caparazón.",
    )
    entry_full = dataclasses.replace(
        entry_full,
        base_stats=tuple(zip(("hp", "atk", "def", "spa", "spd", "spe"), (44, 48, 65, 50, 64, 43))),
    )
    lines = presenter.detail_lines(entry_full)
    joined = "\n".join(lines)
    assert "PS" in joined and " 44" in joined
    assert "Total[/] [bold]314[/]" in joined
    assert "Se esconde en su caparazón." in joined
    assert "Enter: ficha del individuo" not in joined


def test_detail_lines_seen_hide_description_and_stats():
    seen_entry = entry(
        25,
        "pikachu",
        "Pikachu",
        status="seen",
        times_seen=3,
        types=("electric",),
        description="No debería verse aún.",
    )
    lines = presenter.detail_lines(seen_entry)
    joined = "\n".join(lines)
    assert "Pikachu" in joined and "electric" in joined
    assert "No debería verse aún." not in joined
    assert "captúralo" in joined.lower()


def test_detail_lines_dex_registered_without_living_captures():
    ghost = entry(
        172,
        "pichu",
        "Pichu",
        status="captured",
        captures_count=0,
        max_level=None,
        times_seen=1,
    )
    lines = presenter.detail_lines(ghost)
    joined = "\n".join(lines)
    assert "Registrado en tu Pokédex" in joined
    assert "Enter" not in joined
