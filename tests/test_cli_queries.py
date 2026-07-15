import argparse
from unittest.mock import MagicMock

import pytest

from pokedex_cli import cli, storage


def species_data(**overrides):
    data = {
        "pokedex_id": 25,
        "capture_rate": 190,
        "types": ["electric"],
        "hp": 35,
        "atk": 55,
        "def": 40,
        "spa": 50,
        "spd": 50,
        "spe": 90,
        "is_legendary": False,
        "is_mythical": False,
        "generation": "generation-i",
        "flavor_text": "Un ratón eléctrico.",
        "form_data_exact": True,
        "growth_rate": "medium",
        "base_experience": 112,
        "level_evolutions": [],
    }
    data.update(overrides)
    return data


@pytest.fixture
def connection(tmp_path, monkeypatch):
    database_path = tmp_path / "pokedex.db"
    monkeypatch.setattr(cli.composition.paths, "DB_PATH", database_path)
    connection = cli.composition.database.connect(database_path)
    yield connection
    connection.close()


def add_capture(connection, species="pikachu", *, form="regular", shiny=False, caught_at="now"):
    return storage.insert_capture(connection, species, form, shiny, caught_at)


def cache(connection, species="pikachu", form="regular", **overrides):
    storage.upsert_species_cache(
        connection,
        species,
        form,
        species_data(**overrides),
        "2026-07-15T10:00:00+00:00",
    )


def test_list_passes_all_cached_capture_rows_to_presentation(connection, monkeypatch):
    add_capture(connection, "bulbasaur", caught_at="2026-07-14")
    add_capture(connection, "pikachu", caught_at="2026-07-15")
    cache(connection)
    render = MagicMock()
    monkeypatch.setattr(cli.display, "render_list_table", render)

    assert cli.cmd_list(argparse.Namespace()) == 0

    rows = render.call_args.args[1]
    assert [row["species"] for row in rows] == ["pikachu", "bulbasaur"]
    assert rows[0]["types"] == ["electric"]
    assert rows[1]["types"] is None


def test_search_uses_cache_without_network_and_renders_result(connection, monkeypatch):
    cache(connection)
    use_case = MagicMock()
    use_case.execute.return_value = dict(
        storage.get_species_cache(connection, "pikachu", "regular")
    )
    render = MagicMock()
    monkeypatch.setattr(cli, "_species_data_use_case", lambda: use_case)
    monkeypatch.setattr(cli.display, "render_search_card", render)

    assert cli.cmd_search(argparse.Namespace(nombre="pikachu", form="regular")) == 0

    use_case.execute.assert_called_once_with("pikachu", "regular")
    cached = render.call_args.args[3]
    assert cached["pokedex_id"] == 25


@pytest.mark.parametrize("api_data", [species_data(), None])
def test_search_handles_online_enrichment_and_offline_fallback(connection, monkeypatch, api_data):
    use_case = MagicMock()
    if api_data is not None:
        cache(connection)
        use_case.execute.return_value = dict(
            storage.get_species_cache(connection, "pikachu", "regular")
        )
    else:
        use_case.execute.return_value = None
    monkeypatch.setattr(cli, "_species_data_use_case", lambda: use_case)
    render = MagicMock()
    monkeypatch.setattr(cli.display, "render_search_card", render)

    assert cli.cmd_search(argparse.Namespace(nombre="pikachu", form="regular")) == 0

    rendered_cache = render.call_args.args[3]
    if api_data is None:
        assert rendered_cache is None
    else:
        assert rendered_cache["types"] == '["electric"]'


def test_vision_reports_missing_capture_without_calling_external_adapters(connection, monkeypatch):
    renderer = MagicMock()
    sprite = MagicMock()
    sprite.capture_sprite = MagicMock()
    monkeypatch.setattr(cli.display, "render_vision_card", renderer)
    monkeypatch.setattr(cli, "_sprite_renderer", lambda: sprite)

    assert cli.cmd_vision(argparse.Namespace(pokemon="missing", form=None)) == 1
    renderer.assert_not_called()
    sprite.capture_sprite.assert_not_called()


def test_vision_enriches_offline_capture_then_renders_sprite(connection, monkeypatch):
    capture_id = add_capture(connection)
    use_case = MagicMock()

    def enrich(species, form):
        cache(connection, species, form)
        return dict(storage.get_species_cache(connection, species, form))

    use_case.execute.side_effect = enrich
    monkeypatch.setattr(cli, "_species_data_use_case", lambda: use_case)
    renderer = MagicMock()
    renderer.capture_sprite.return_value = "SPRITE"
    monkeypatch.setattr(cli, "_sprite_renderer", lambda: renderer)
    render = MagicMock()
    monkeypatch.setattr(cli.display, "render_vision_card", render)

    assert cli.cmd_vision(argparse.Namespace(pokemon=str(capture_id), form=None)) == 0

    row, sprite = render.call_args.args[1:]
    assert row["types"] == ["electric"]
    assert sprite == "SPRITE"


def test_type_ranking_and_rare_queries_prepare_expected_rows(connection, monkeypatch):
    normal = add_capture(connection, "pikachu", caught_at="2026-07-15")
    rare = add_capture(connection, "mew", caught_at="2026-07-14")
    cache(
        connection,
        "pikachu",
        **{"types": ["electric"], "hp": 1, "atk": 2, "def": 3, "spa": 4, "spd": 5, "spe": 6},
    )
    cache(
        connection,
        "mew",
        **{
            "types": ["psychic"],
            "hp": 100,
            "atk": 100,
            "def": 100,
            "spa": 100,
            "spd": 100,
            "spe": 100,
            "is_mythical": True,
        },
    )
    assert normal != rare
    type_render = MagicMock()
    ranking_render = MagicMock()
    rare_render = MagicMock()
    monkeypatch.setattr(cli.display, "render_tipos_breakdown", type_render)
    monkeypatch.setattr(cli.display, "render_ranking_table", ranking_render)
    monkeypatch.setattr(cli.display, "render_legendarios_panel", rare_render)

    assert cli.cmd_tipos(argparse.Namespace()) == 0
    assert cli.cmd_ranking(argparse.Namespace()) == 0
    assert cli.cmd_legendarios(argparse.Namespace()) == 0

    assert type_render.call_args.args[1] == {"electric": 1, "psychic": 1}
    ranked, missing = ranking_render.call_args.args[1:]
    assert [row["species"] for row in ranked] == ["mew", "pikachu"]
    assert [row["total"] for row in ranked] == [600, 21]
    assert missing == 0
    assert [row["species"] for row in rare_render.call_args.args[1]] == ["mew"]
