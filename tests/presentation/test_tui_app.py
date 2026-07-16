"""Smoke tests de la Pokédex interactiva: la app monta con datos inyectados
(sin BD real, sin HOME, sin red, sin krabby) y responde a las teclas básicas."""

from __future__ import annotations

import asyncio

import pytest

textual = pytest.importorskip("textual")

from pokedex_cli.application.pokedex_catalog import CatalogEntry  # noqa: E402
from pokedex_cli.presentation.tui.app import PokedexApp, static_noise  # noqa: E402

SPRITE = "\x1b[38;2;255;0;0m▀▀\x1b[0m\n\x1b[38;2;0;255;0m▄▄\x1b[0m"


def _entry(idx: int, slug: str, status: str, gen: int = 1) -> CatalogEntry:
    return CatalogEntry(
        idx=idx,
        slug=slug,
        name=slug.title(),
        gen=gen,
        status=status,
        types=("water",) if status == "captured" else None,
        captures_count=2 if status == "captured" else 0,
        max_level=20 if status == "captured" else None,
        any_shiny=False,
        times_seen=3 if status != "unseen" else 0,
        description="Una descripción." if status != "unseen" else None,
    )


ENTRIES = [
    _entry(1, "bulbasaur", "captured"),
    _entry(2, "ivysaur", "seen"),
    _entry(3, "venusaur", "unseen"),
    _entry(25, "pikachu", "captured"),
    _entry(152, "chikorita", "unseen", gen=2),
]

CAPTURE_ROW = {
    "id": 7,
    "species": "bulbasaur",
    "form": "regular",
    "shiny": 0,
    "level": 12,
    "gender": "male",
    "ability": "overgrow",
    "gender_rate": 1,
    "nature": None,
    "ivs": {k: 10 for k in ("hp", "atk", "def", "spa", "spd", "spe")},
    "stats": {"hp": 33, "atk": 18, "def": 18, "spa": 22, "spd": 22, "spe": 17},
    "hp": 45,
    "atk": 49,
    "def": 49,
    "spa": 65,
    "spd": 65,
    "spe": 45,
}


def _app() -> PokedexApp:
    return PokedexApp(
        catalog_loader=lambda: list(ENTRIES),
        captures_loader=lambda: [dict(CAPTURE_ROW)],
        sprite_fetcher=lambda species, form, shiny: SPRITE,
        skip_boot=True,
    )


def test_static_noise_es_determinista_y_braille() -> None:
    a = static_noise(25)
    b = static_noise(25)
    assert a == b
    assert a != static_noise(26)
    assert all(0x2800 <= ord(ch) <= 0x28FF for row in a for ch in row)


def test_la_app_monta_lista_completa_y_navega() -> None:
    async def scenario() -> None:
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            lista = app.query_one("#lista")
            assert lista.option_count == len(ENTRIES)
            assert app._selected_entry().slug == "bulbasaur"
            await pilot.press("down")
            assert app._selected_entry().slug == "ivysaur"
            await pilot.press("q")

    asyncio.run(scenario())


def test_filtro_de_estado_y_generacion() -> None:
    async def scenario() -> None:
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("f")  # Todos -> Capturados
            slugs = [entry.slug for entry in app._filtered]
            assert slugs == ["bulbasaur", "pikachu"]
            await pilot.press("f", "f", "f")  # -> Vistos -> Pendientes -> Todos
            assert len(app._filtered) == len(ENTRIES)
            await pilot.press("g", "g")  # Gen 1 -> Gen 2
            assert [entry.slug for entry in app._filtered] == ["chikorita"]
            await pilot.press("q")

    asyncio.run(scenario())


def test_busqueda_en_vivo_por_nombre_y_numero() -> None:
    async def scenario() -> None:
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("slash")
            await pilot.press(*"pika")
            assert [entry.slug for entry in app._filtered] == ["pikachu"]
            busqueda = app.query_one("#busqueda")
            busqueda.value = "25"
            await pilot.pause()
            assert [entry.slug for entry in app._filtered] == ["pikachu"]
            await pilot.press("escape")
            assert app.focused is app.query_one("#lista")
            await pilot.press("q")

    asyncio.run(scenario())


def test_detalle_solo_para_capturados() -> None:
    async def scenario() -> None:
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("enter")  # bulbasaur: capturado -> abre detalle
            await pilot.pause()
            assert len(app.screen_stack) == 2
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            await pilot.press("down", "enter")  # ivysaur: solo visto -> nada
            await pilot.pause()
            assert len(app.screen_stack) == 1
            await pilot.press("q")

    asyncio.run(scenario())
