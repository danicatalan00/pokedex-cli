import argparse
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from pokedex_cli import cli, inventory, storage


def state():
    return inventory._new_inventory(datetime(2026, 7, 15, 10, tzinfo=timezone.utc))


def args(**overrides):
    values = {"bola": "poke", "debug": False}
    values.update(overrides)
    return argparse.Namespace(**values)


def seen(**overrides):
    value = {
        "species": "pikachu",
        "form": "regular",
        "shiny": False,
        "captured": False,
    }
    value.update(overrides)
    return value


def sync_result():
    return inventory.SyncResult(state(), (), 0, 0)


@pytest.mark.parametrize(
    ("encounter", "expected_code", "text"),
    [
        (None, 1, "No hay ningún Pokémon"),
        (seen(captured=True), 0, "Ya capturaste"),
    ],
)
def test_capture_short_circuits_when_no_action_is_possible(
    monkeypatch, capsys, encounter, expected_code, text
):
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: encounter)
    sync = MagicMock()
    monkeypatch.setattr(cli, "_sync_training", sync)

    assert cli.cmd_capturar(args()) == expected_code
    assert text in capsys.readouterr().out
    sync.assert_not_called()


def test_capture_rejects_unknown_or_empty_ball_before_external_lookup(monkeypatch):
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: seen())
    result = sync_result()
    monkeypatch.setattr(cli, "_sync_training", lambda: (result, ()))
    species_data = MagicMock()
    monkeypatch.setattr(cli, "_species_data_use_case", species_data)

    assert cli.cmd_capturar(args(bola="missing")) == 2
    species_data.assert_not_called()

    result.inventory["balls"]["masterball"] = 0
    assert cli.cmd_capturar(args(bola="master")) == 2


@pytest.mark.parametrize(
    ("status", "expected_code", "expected_text", "animated"),
    [
        (cli.capture_application.CaptureStatus.NO_STOCK, 2, "No te queda", False),
        (cli.capture_application.CaptureStatus.NO_ENCOUNTER, 1, "No hay ningún", False),
        (cli.capture_application.CaptureStatus.ALREADY_CAPTURED, 0, "Ya capturaste", False),
        (cli.capture_application.CaptureStatus.FAILED, 0, "Se soltó", True),
        (cli.capture_application.CaptureStatus.FLED, 0, "ha huido", True),
    ],
)
def test_capture_maps_use_case_results_to_stable_cli_outcomes(
    monkeypatch, capsys, status, expected_code, expected_text, animated
):
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: seen())
    monkeypatch.setattr(cli, "_sync_training", lambda: (sync_result(), ()))
    species_data = MagicMock()
    species_data.execute.return_value = None
    monkeypatch.setattr(cli, "_species_data_use_case", lambda: species_data)
    use_case = MagicMock()
    use_case.execute.return_value = cli.capture_application.CaptureResult(status, chance=0.2)
    monkeypatch.setattr(cli, "_capture_encounter_use_case", lambda: use_case)
    animation = MagicMock()
    monkeypatch.setattr(cli.animation, "play_capture_animation", animation)
    monkeypatch.setattr(cli.capture, "breakout_message", lambda: "Se soltó")

    assert cli.cmd_capturar(args()) == expected_code
    assert expected_text in capsys.readouterr().out
    assert animation.called is animated


def test_bag_info_reports_stock_policy_and_activity(monkeypatch):
    result = sync_result()
    result.inventory["activity"]["work_commits"] = 9
    monkeypatch.setattr(cli, "_sync_training", lambda **unused: (result, ()))

    with cli.console.capture() as output:
        assert cli.cmd_bolsas(argparse.Namespace(info=True)) == 0

    rendered = output.get()
    assert "Bolsa" in rendered
    assert "Información" in rendered
    assert "9 commits laborales" in rendered
    assert "faltan 1 commit" in rendered


@pytest.fixture
def connection(tmp_path, monkeypatch):
    database_path = tmp_path / "team.db"
    monkeypatch.setattr(cli.composition.paths, "DB_PATH", database_path)
    connection = cli.composition.database.connect(database_path)
    yield connection
    connection.close()


def add_capture(connection, *, in_team=False):
    capture_id = storage.insert_capture(
        connection, "pikachu", "regular", False, "2026-07-15T10:00:00+00:00"
    )
    storage.set_team(connection, capture_id, in_team)
    return capture_id


def test_team_validates_remove_and_add_identifiers(connection, monkeypatch, capsys):
    use_case = MagicMock()
    use_case.execute.return_value = cli.team_application.TeamResult(
        cli.team_application.TeamStatus.NOT_FOUND, 999
    )
    monkeypatch.setattr(cli, "_manage_team_use_case", lambda: use_case)

    assert cli.cmd_equipo(argparse.Namespace(accion="add", id=999)) == 1
    output = capsys.readouterr().out
    assert "No existe" in output

    capture_id = add_capture(connection, in_team=True)
    use_case.execute.return_value = cli.team_application.TeamResult(
        cli.team_application.TeamStatus.ALREADY_MEMBER, capture_id
    )
    assert cli.cmd_equipo(argparse.Namespace(accion="add", id=capture_id)) == 0
    assert use_case.execute.call_args_list[-1].args == (
        cli.team_application.TeamAction.ADD,
        capture_id,
    )


@pytest.mark.parametrize("action", ["add", "remove"])
def test_team_name_delegates_resolution_to_scoped_selector(monkeypatch, action):
    selected = MagicMock(return_value=7)
    monkeypatch.setattr(cli, "_select_capture_for_team", selected)
    use_case = MagicMock()
    use_case.execute.return_value = cli.team_application.TeamResult(
        cli.team_application.TeamStatus.ADDED
        if action == "add"
        else cli.team_application.TeamStatus.REMOVED,
        7,
    )
    monkeypatch.setattr(cli, "_manage_team_use_case", lambda: use_case)

    assert cli.cmd_equipo(argparse.Namespace(accion=action, id="eevee")) == 0
    expected_action = cli.team_application.TeamAction(action)
    selected.assert_called_once_with(expected_action, "eevee")
    use_case.execute.assert_called_once_with(expected_action, 7)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (cli.team_application.TeamStatus.ADDED, 0),
        (cli.team_application.TeamStatus.FULL, 1),
    ],
)
def test_team_add_maps_atomic_use_case_result(connection, monkeypatch, status, expected):
    capture_id = add_capture(connection)
    use_case = MagicMock()
    use_case.execute.return_value = cli.team_application.TeamResult(status, capture_id)
    monkeypatch.setattr(cli, "_manage_team_use_case", lambda: use_case)

    assert cli.cmd_equipo(argparse.Namespace(accion="add", id=capture_id)) == expected
    use_case.execute.assert_called_once_with(cli.team_application.TeamAction.ADD, capture_id)


def test_team_remove_and_show_delegate_to_use_case_and_presentation(connection, monkeypatch):
    capture_id = add_capture(connection, in_team=True)
    use_case = MagicMock()
    monkeypatch.setattr(cli, "_manage_team_use_case", lambda: use_case)
    render = MagicMock()
    monkeypatch.setattr(cli.display, "render_team_panel", render)

    assert cli.cmd_equipo(argparse.Namespace(accion="remove", id=capture_id)) == 0
    use_case.execute.assert_called_once_with(cli.team_application.TeamAction.REMOVE, capture_id)
    assert cli.cmd_equipo(argparse.Namespace(accion=None, id=None)) == 0
    assert render.call_args.args[1][0]["id"] == capture_id


@pytest.mark.parametrize(
    ("namespace", "species", "caught"),
    [
        (
            argparse.Namespace(
                nombre="eevee",
                legendary=False,
                form="regular",
                shiny=True,
                generations="1-9",
                bola="poke",
                result="catch",
            ),
            "eevee",
            True,
        ),
        (
            argparse.Namespace(
                nombre=None,
                legendary=True,
                form="regular",
                shiny=False,
                generations="1-9",
                bola="ultra",
                result="escape",
            ),
            "mewtwo",
            False,
        ),
    ],
)
def test_capture_demo_is_pure_presentation(monkeypatch, namespace, species, caught):
    monkeypatch.setattr(cli.capture, "random_legendary", lambda: "mewtwo")
    animation = MagicMock()
    monkeypatch.setattr(cli.animation, "play_capture_animation", animation)

    assert cli.cmd_demo(namespace) == 0
    assert animation.call_args.args[1] == species
    assert animation.call_args.args[4] is caught


def test_evolution_demo_delegates_without_persistence(monkeypatch):
    animation = MagicMock()
    monkeypatch.setattr(cli.animation, "play_evolution_animation", animation)
    namespace = argparse.Namespace(
        origen="bulbasaur",
        destino="ivysaur",
        form_origen="regular",
        form_destino="regular",
        shiny=True,
        speed=0.7,
    )

    assert cli.cmd_demo_evolucion(namespace) == 0
    assert animation.call_args.kwargs["speed"] == 0.7


def test_completion_reads_project_file_then_reports_missing(monkeypatch, tmp_path, capsys):
    completion = tmp_path / "completions" / "_pokedex.zsh"
    completion.parent.mkdir()
    completion.write_text("# completion")
    monkeypatch.setattr(cli.composition.paths, "PROJECT_DIR", tmp_path)

    assert cli.cmd_completion(argparse.Namespace(shell="zsh")) == 0
    assert capsys.readouterr().out == "# completion"

    completion.unlink()
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path / "missing-home")
    assert cli.cmd_completion(argparse.Namespace(shell="zsh")) == 1
    assert "No hay autocompletado" in capsys.readouterr().err


def test_refresh_reports_successes_and_failures(monkeypatch, capsys):
    use_case = MagicMock()
    use_case.execute.return_value = cli.species_application.RefreshResult(
        total=2,
        refreshed=1,
        failed=(cli.species_application.SpeciesIdentity("slowking", "galar"),),
    )
    monkeypatch.setattr(cli.composition, "refresh_species_data", lambda: use_case)

    assert cli.cmd_refresh(argparse.Namespace()) == 1
    output = capsys.readouterr().out
    assert "1 de 2" in output
    assert "slowking (galar)" in output


def _demo_vision_cache(**overrides):
    cache = {
        "pokedex_id": 6,
        "types": '["fire", "flying"]',
        "hp": 78,
        "atk": 84,
        "def": 78,
        "spa": 109,
        "spd": 85,
        "spe": 100,
        "is_legendary": 0,
        "is_mythical": 0,
        "generation": "generation-i",
        "flavor_text": "Escupe fuego.",
        "form_data_exact": 1,
        "gender_rate": 1,
        "abilities": '["blaze"]',
        "growth_rate": "medium-slow",
        "capture_rate": 45,
    }
    cache.update(overrides)
    return cache


def test_demo_vision_renders_synthetic_individual_at_level(monkeypatch, capsys):
    species_use_case = MagicMock()
    species_use_case.execute.return_value = _demo_vision_cache()
    monkeypatch.setattr(cli, "_species_data_use_case", lambda: species_use_case)
    sprites = MagicMock()
    sprites.capture_sprite.return_value = None
    monkeypatch.setattr(cli, "_sprite_renderer", lambda: sprites)

    namespace = argparse.Namespace(
        nombre="charizard", nivel=80, form="regular", shiny=False, seed="1"
    )
    assert cli.cmd_demo_vision(namespace) == 0

    output = capsys.readouterr().out
    assert "Nv. 80" in output
    assert "demo" in output
    assert "nada se guarda" in output
    assert "Naturaleza" in output
    assert "Habilidad Blaze" in output
    # stats actuales, no las base: a nivel 80 el HP supera con mucho la base 78
    assert "base 78" in output
    species_use_case.execute.assert_called_once_with("charizard", "regular")


def test_demo_vision_reports_missing_species(monkeypatch, capsys):
    species_use_case = MagicMock()
    species_use_case.execute.return_value = None
    monkeypatch.setattr(cli, "_species_data_use_case", lambda: species_use_case)

    namespace = argparse.Namespace(
        nombre="noexiste", nivel=80, form="regular", shiny=False, seed="1"
    )
    assert cli.cmd_demo_vision(namespace) == 1
    assert "No se pudo obtener" in capsys.readouterr().out


def test_demo_vision_clamps_level_and_is_deterministic(monkeypatch, capsys):
    species_use_case = MagicMock()
    species_use_case.execute.return_value = _demo_vision_cache()
    monkeypatch.setattr(cli, "_species_data_use_case", lambda: species_use_case)
    sprites = MagicMock()
    sprites.capture_sprite.return_value = None
    monkeypatch.setattr(cli, "_sprite_renderer", lambda: sprites)

    namespace = argparse.Namespace(
        nombre="charizard", nivel=999, form="regular", shiny=False, seed="1"
    )
    assert cli.cmd_demo_vision(namespace) == 0
    first = capsys.readouterr().out
    assert "Nv. 100" in first
    assert "EXP MAX" in first

    assert cli.cmd_demo_vision(namespace) == 0
    assert capsys.readouterr().out == first
