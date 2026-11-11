"""Smoke tests de la Pokédex interactiva: la app monta con datos inyectados
(sin BD real, sin HOME, sin red, sin krabby) y responde a las teclas básicas."""

from __future__ import annotations

import asyncio
import threading

import pytest

textual = pytest.importorskip("textual")

from pokedex_cli.application.movesets import MovesetView  # noqa: E402
from pokedex_cli.application.pokedex_catalog import CatalogEntry  # noqa: E402
from pokedex_cli.domain.battle.model import Outcome  # noqa: E402
from pokedex_cli.domain.battle.moves import TACKLE  # noqa: E402
from pokedex_cli.presentation.tui.app import (  # noqa: E402
    SPRITE_DEBOUNCE,
    DetailScreen,
    PokedexApp,
    PokedexScreenWidget,
    static_noise,
)
from pokedex_cli.presentation.tui.battle import (  # noqa: E402
    PAUSED_NOTICE,
    BattleLauncher,
    BattleScreen,
    BattleServices,
    LauncherOptions,
    OpenedBattle,
    ResultPanel,
    WildPreview,
)
from tests.presentation.test_tui_battle import (  # noqa: E402
    EMBER,
    OPENING,
    FakeSession,
    FakeSprites,
    press_at_once,
    wait_for,
    wild_state,
    winning_turn,
)


def offline_services(**overrides: object) -> BattleServices:
    """Servicios de combate sin composition: ni BD, ni HOME, ni red."""
    values: dict[str, object] = {
        "options": lambda: LauncherOptions(),
        "open_session": lambda choice, progress: "Sin combate en los tests.",
        "bag": lambda: [],
        "record": lambda: {},
        "moveset": lambda capture_id: None,
        "report": lambda context, error: None,
        "speed": 1000.0,
    }
    values.update(overrides)
    return BattleServices(**values)  # type: ignore[arg-type]


SPRITE = "\x1b[38;2;255;0;0m▀▀\x1b[0m\n\x1b[38;2;0;255;0m▄▄\x1b[0m"


def _entry(
    idx: int,
    slug: str,
    status: str,
    gen: int = 1,
    evolution_targets: tuple[str, ...] = (),
    evolution_family: tuple[str, ...] = (),
) -> CatalogEntry:
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
        evolution_targets=evolution_targets,
        evolution_family=evolution_family,
    )


ENTRIES = [
    _entry(
        1,
        "bulbasaur",
        "captured",
        evolution_targets=("ivysaur",),
        evolution_family=("bulbasaur", "ivysaur", "venusaur"),
    ),
    _entry(2, "ivysaur", "seen", evolution_targets=("venusaur",)),
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
        battle_services=offline_services(),
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


def test_escape_limpia_los_filtros_de_lista() -> None:
    async def scenario() -> None:
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("f", "g")
            assert app._status_filter == "captured"
            assert app._gen_filter == 1

            await pilot.press("escape")

            assert app._status_filter is None
            assert app._gen_filter is None
            assert app._filtered == ENTRIES
            await pilot.press("q")

    asyncio.run(scenario())


def test_busqueda_en_vivo_por_nombre_y_numero() -> None:
    async def scenario() -> None:
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("f")
            await pilot.press("slash")
            await pilot.press(*"pika")
            assert [entry.slug for entry in app._filtered] == ["pikachu"]
            busqueda = app.query_one("#busqueda")
            busqueda.value = "25"
            await pilot.pause()
            assert [entry.slug for entry in app._filtered] == ["pikachu"]
            await pilot.press("escape")
            assert app.focused is app.query_one("#lista")
            assert busqueda.value == "25"
            assert app._status_filter == "captured"
            await pilot.press("q")

    asyncio.run(scenario())


def test_divide_del_teclado_numerico_abre_la_busqueda() -> None:
    async def scenario() -> None:
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("divide")
            assert app.focused is app.query_one("#busqueda")
            await pilot.press("q")

    asyncio.run(scenario())


def test_la_pantalla_de_arranque_se_cierra_sola_sin_error() -> None:
    """Regresión: dismiss() desde el callback del timer lanzaba ScreenError
    ('Can't await screen.dismiss() from the screen's message handler')."""

    async def scenario() -> None:
        app = PokedexApp(
            catalog_loader=lambda: list(ENTRIES),
            captures_loader=lambda: [dict(CAPTURE_ROW)],
            sprite_fetcher=lambda species, form, shiny: SPRITE,
            skip_boot=False,
            battle_services=offline_services(),
        )
        async with app.run_test(size=(120, 40)) as pilot:
            assert len(app.screen_stack) == 2  # arranque encima
            await pilot.pause(1.6)
            assert len(app.screen_stack) == 1  # se cerró solo, sin crash
            await pilot.press("q")

    asyncio.run(scenario())


def test_detalle_para_vistos_y_capturados_no_para_pendientes() -> None:
    async def scenario() -> None:
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("enter")  # bulbasaur: capturado -> abre detalle
            await pilot.pause()
            assert len(app.screen_stack) == 2
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            await pilot.press("down", "enter")  # ivysaur: solo visto -> abre detalle
            await pilot.pause()
            assert len(app.screen_stack) == 2
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            await pilot.press("down", "enter")  # venusaur: pendiente -> nada
            await pilot.pause()
            assert len(app.screen_stack) == 1
            await pilot.press("q")

    asyncio.run(scenario())


def test_scroll_rapido_no_lanza_un_sprite_por_fila() -> None:
    # krabby es un subproceso: recorrer la lista deprisa con las flechas no debe
    # encadenar una carga por cada fila intermedia, solo la de destino.
    calls: list[str] = []

    def counting_fetcher(species: str, form: str, shiny: bool) -> str:
        calls.append(species)
        return SPRITE

    entries = [_entry(i, f"mon{i:03d}", "captured") for i in range(1, 31)]
    app = PokedexApp(
        catalog_loader=lambda: entries,
        captures_loader=lambda: [],
        sprite_fetcher=counting_fetcher,
        skip_boot=True,
        battle_services=offline_services(),
    )

    async def scenario() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            calls.clear()
            for _ in range(10):
                await pilot.press("down")
            # El cursor se asienta: solo el destino final trae su sprite.
            await asyncio.sleep(SPRITE_DEBOUNCE + 0.1)
            await pilot.pause()
            destino = app._selected_entry()
            assert destino is not None
            assert len(calls) <= 3, calls
            assert destino.slug in calls

    asyncio.run(scenario())


def test_pantalla_principal_conserva_un_sprite_grande_que_cabe() -> None:
    async def scenario() -> None:
        red_row = "\x1b[38;2;255;0;0m" + "█" * 20 + "\x1b[0m"
        large_sprite = "\n".join([red_row] * 20)
        app = PokedexApp(
            catalog_loader=lambda: list(ENTRIES),
            captures_loader=lambda: [dict(CAPTURE_ROW)],
            sprite_fetcher=lambda species, form, shiny: large_sprite,
            skip_boot=True,
            battle_services=offline_services(),
        )
        async with app.run_test(size=(120, 40)) as pilot:
            screen = app.query_one(PokedexScreenWidget)
            # El sprite llega tras el antirrebote y un hilo: se espera a él en
            # vez de suponer que un solo pause() basta (dependía del reloj).
            await wait_for(pilot, lambda: screen._content is not None and screen._content.ansi)
            assert len(screen._content.lines) == 20

            await pilot.resize_terminal(80, 22)
            await pilot.pause()
            assert screen._content is not None
            assert len(screen._content.lines) < 20
            assert len(screen._content.lines) <= screen.content_size.height

    asyncio.run(scenario())


def test_flechas_del_detalle_recorren_evoluciones_con_visibilidad_de_pokedex() -> None:
    async def scenario() -> None:
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("enter")
            await pilot.pause()
            detail = app.screen
            assert isinstance(detail, DetailScreen)
            assert detail._entry.slug == "bulbasaur"

            await pilot.press("right")
            assert detail._entry.slug == "ivysaur"
            ficha = str(detail.query_one("#detalle-ficha").render())
            assert "Ivysaur" in ficha
            assert "Datos incompletos" in ficha

            await pilot.press("right")
            assert detail._entry.slug == "venusaur"
            ficha = str(detail.query_one("#detalle-ficha").render())
            assert "??????" in ficha
            assert "Venusaur" not in ficha
            assert "\n?" not in str(detail.query_one("#detalle-sprite").render())

    asyncio.run(scenario())


def test_familia_evolutiva_respeta_la_cadena_y_no_el_numero_de_pokedex() -> None:
    async def scenario() -> None:
        family = ("pichu", "pikachu", "raichu")
        entries = [
            _entry(25, "pikachu", "captured", evolution_family=family),
            _entry(26, "raichu", "seen"),
            _entry(172, "pichu", "captured"),
        ]
        app = PokedexApp(
            catalog_loader=lambda: entries,
            captures_loader=lambda: [],
            sprite_fetcher=lambda species, form, shiny: SPRITE,
            skip_boot=True,
            battle_services=offline_services(),
        )
        async with app.run_test(size=(120, 40)):
            ordered = app.evolution_family(entries[0])
            assert [entry.slug for entry in ordered] == ["pichu", "pikachu", "raichu"]

    asyncio.run(scenario())


def test_arriba_y_abajo_del_detalle_recorren_la_lista_actual() -> None:
    async def scenario() -> None:
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("enter")
            await pilot.pause()
            detail = app.screen
            assert isinstance(detail, DetailScreen)

            await pilot.press("down")
            assert detail._entry.slug == "ivysaur"
            await pilot.press("up")
            assert detail._entry.slug == "bulbasaur"

    asyncio.run(scenario())


def test_especie_no_vista_no_añade_interrogacion_bajo_la_silueta() -> None:
    async def scenario() -> None:
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("down", "down")
            await pilot.pause()
            content = app.query_one(PokedexScreenWidget)._content
            assert content is not None
            assert "?" not in content.lines

    asyncio.run(scenario())


def test_sprite_del_detalle_se_conserva_y_se_reescala_al_encoger() -> None:
    async def scenario() -> None:
        red_row = "\x1b[38;2;255;0;0m" + "█" * 20 + "\x1b[0m"
        large_sprite = "\n".join([red_row] * 20)
        app = PokedexApp(
            catalog_loader=lambda: list(ENTRIES),
            captures_loader=lambda: [dict(CAPTURE_ROW)],
            sprite_fetcher=lambda species, form, shiny: large_sprite,
            skip_boot=True,
            battle_services=offline_services(),
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("enter")
            await pilot.pause()
            widget = app.screen.query_one("#detalle-sprite")
            assert len(str(widget.render()).splitlines()) == 20

            await pilot.resize_terminal(80, 22)
            await pilot.pause()
            resized_lines = str(widget.render()).splitlines()
            assert len(resized_lines) < 20
            assert len(resized_lines) <= widget.content_size.height

    asyncio.run(scenario())


def test_legendarios_combina_filtros_y_escape_limpia():
    from dataclasses import replace

    async def scenario():
        entries = [
            *ENTRIES,
            replace(_entry(150, "mewtwo", "captured"), is_rare=True),
            replace(_entry(151, "mew", "unseen"), is_rare=True),
        ]
        app = PokedexApp(
            catalog_loader=lambda: entries,
            captures_loader=lambda: [],
            sprite_fetcher=lambda *args: SPRITE,
            skip_boot=True,
            battle_services=offline_services(),
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("l")
            assert [e.slug for e in app._filtered] == ["mewtwo", "mew"]
            await pilot.press("f", "g")
            assert [e.slug for e in app._filtered] == ["mewtwo"]
            await pilot.press("escape")
            assert app._filtered == entries
            await pilot.press("l", "l")
            assert app._filtered == entries

    asyncio.run(scenario())


# --- Combate desde la Pokédex ------------------------------------------------------


def _battle_app(session, *, catalogs=None, captures=None, moveset=None, notices=None):
    """Pokédex con un combate guionizado detrás de la tecla c."""
    loads = {"catalog": 0, "captures": 0}
    catalogs = catalogs or [list(ENTRIES)]

    def catalog_loader():
        loads["catalog"] += 1
        return list(catalogs[min(loads["catalog"], len(catalogs)) - 1])

    def captures_loader():
        loads["captures"] += 1
        return [dict(row) for row in (captures or [CAPTURE_ROW])]

    services = offline_services(
        options=lambda: LauncherOptions(
            wild=WildPreview("Pidgey", 12), trainer=True, exhibition=True
        ),
        open_session=lambda choice, progress: OpenedBattle(session),
        moveset=moveset or (lambda capture_id: None),
    )
    app = PokedexApp(
        catalog_loader=catalog_loader,
        captures_loader=captures_loader,
        sprite_fetcher=FakeSprites(),
        skip_boot=True,
        battle_services=services,
    )
    if notices is not None:
        app.notify = lambda message, **options: notices.append(message)
    return app, loads


def test_c_abre_el_lanzador_solo_con_las_opciones_posibles() -> None:
    async def scenario() -> None:
        app, _ = _battle_app(FakeSession(wild_state()))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("c")
            await wait_for(pilot, lambda: isinstance(app.screen, BattleLauncher))
            launcher = app.screen
            await wait_for(pilot, lambda: launcher.options is not None)
            labels = [option.prompt for option in launcher.query_one("#lanzador-lista").options]
            assert labels[0] == "Pokémon salvaje: Pidgey Nv. 12"
            assert len(labels) == 3 and not any("Reanudar" in label for label in labels)
            await pilot.press("escape")
            await wait_for(pilot, lambda: len(app.screen_stack) == 1)

    asyncio.run(scenario())


def test_combate_completo_y_al_volver_la_pokedex_recarga_catalogo_y_capturas() -> None:
    from dataclasses import replace

    pidgey = replace(_entry(16, "pidgey", "captured"), name="Pidgey")
    session = FakeSession(wild_state(), [winning_turn], opening=OPENING)

    async def scenario() -> None:
        app, loads = _battle_app(session, catalogs=[list(ENTRIES), [*ENTRIES, pidgey]])
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("c")
            await wait_for(pilot, lambda: getattr(app.screen, "options", None) is not None)
            await pilot.press("enter")  # Pokémon salvaje
            await wait_for(
                pilot, lambda: isinstance(app.screen, BattleScreen) and app.screen.mode == "main"
            )
            await pilot.press("1", "1")  # LUCHAR → Ascuas
            await wait_for(pilot, lambda: isinstance(app.screen, ResultPanel))
            assert app.screen.summary.title == "¡VICTORIA!"
            await pilot.press("escape")
            await wait_for(pilot, lambda: len(app.screen_stack) == 1 and not app._reloading)
            assert loads == {"catalog": 2, "captures": 1}
            assert app.query_one("#lista").option_count == len(ENTRIES) + 1
            assert app.last_battle is not None and app.last_battle.outcome is Outcome.WON

    asyncio.run(scenario())


def test_dos_enter_seguidos_en_el_lanzador_abren_un_solo_combate_sin_romper_la_app() -> None:
    # Con el segundo Enter el lanzador ya está cerrado: antes, Textual sacaba
    # también la pantalla de debajo y la Pokédex caía con ScreenStackError.
    async def scenario() -> None:
        app, _ = _battle_app(FakeSession(wild_state()))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("c")
            await wait_for(pilot, lambda: getattr(app.screen, "options", None) is not None)
            await press_at_once(pilot, "enter", "enter")
            await wait_for(
                pilot, lambda: isinstance(app.screen, BattleScreen) and app.screen.mode == "main"
            )
            assert [type(screen) for screen in app.screen_stack[1:]] == [BattleScreen]

    asyncio.run(scenario())


def test_dejar_un_combate_a_medias_avisa_de_que_queda_en_pausa() -> None:
    notices: list[str] = []

    async def scenario() -> None:
        app, _ = _battle_app(FakeSession(wild_state()), notices=notices)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("c")
            await wait_for(pilot, lambda: getattr(app.screen, "options", None) is not None)
            await pilot.press("enter")
            await wait_for(
                pilot, lambda: isinstance(app.screen, BattleScreen) and app.screen.mode == "main"
            )
            await pilot.press("escape")
            await wait_for(pilot, lambda: len(app.screen_stack) == 1 and not app._reloading)
            assert notices == [PAUSED_NOTICE]
            assert app.last_battle is not None and not app.last_battle.finished

    asyncio.run(scenario())


def test_c_no_abre_el_combate_desde_la_ficha() -> None:
    async def scenario() -> None:
        app, _ = _battle_app(FakeSession(wild_state()))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, DetailScreen)
            await pilot.press("c")
            await pilot.pause()
            assert isinstance(app.screen, DetailScreen)
            assert len(app.screen_stack) == 2

    asyncio.run(scenario())


def test_la_ficha_del_individuo_carga_sus_movimientos_sin_bloquear() -> None:
    gate = threading.Event()
    calls: list[tuple[int, str]] = []

    def slow_moveset(capture_id: int) -> MovesetView:
        calls.append((capture_id, threading.current_thread().name))
        assert gate.wait(10)
        return MovesetView(
            capture_id=capture_id,
            name="Bulbasaur",
            species="bulbasaur",
            form="regular",
            level=12,
            types=("grass", "poison"),
            custom=True,
            moves=(EMBER, TACKLE),
            learnable=(),
            upcoming=(),
        )

    async def scenario() -> None:
        app, _ = _battle_app(FakeSession(wild_state()), moveset=slow_moveset)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("enter")
            await pilot.pause()
            detail = app.screen
            assert isinstance(detail, DetailScreen)
            ficha = detail.query_one("#detalle-ficha")
            assert "MOVIMIENTOS cargando…" in str(ficha.render())
            # La ficha sigue respondiendo mientras tanto.
            await pilot.press("full_stop")
            gate.set()
            await wait_for(pilot, lambda: "Ascuas" in str(ficha.render()))
            text = str(ficha.render())
            assert "MOVIMIENTOS elegidos por ti" in text
            assert "Pot Prec  PP" in text and "Placaje" in text
            assert calls == [(7, calls[0][1])]
            assert calls[0][1] != threading.main_thread().name

    try:
        asyncio.run(scenario())
    finally:
        gate.set()
