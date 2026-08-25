import argparse
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from pokedex_cli import cli, inventory, storage
from pokedex_cli.application.encounter import EncounterStatus


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


def _flat(rendered: str) -> str:
    """Aplana el salto de línea que Rich mete al ajustar al ancho del terminal."""
    return " ".join(rendered.split())


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


def _stub_status(monkeypatch, status):
    use_case = MagicMock()
    use_case.execute.return_value = status
    monkeypatch.setattr(cli.composition, "describe_encounter", lambda: use_case)
    renderer = MagicMock()
    renderer.capture_sprite.return_value = None
    monkeypatch.setattr(cli, "_sprite_renderer", lambda: renderer)
    return use_case


@pytest.mark.parametrize(
    ("encounter", "status", "expected"),
    [
        # Plain species already in the Pokédex: captured, regardless of the
        # individual waiting in this terminal.
        (
            seen(captured=False),
            EncounterStatus(captured=True, special=False),
            "ya capturado",
        ),
        # Plain species never registered: plainly missing, no fuss.
        (
            seen(captured=False),
            EncounterStatus(captured=False, special=False),
            "sin capturar",
        ),
        # Special variant we do not own: flagged.
        (
            seen(shiny=True),
            EncounterStatus(captured=False, special=True),
            "¡sin capturar! (variante especial)",
        ),
        # Special variant we do own: captured, no flag.
        (
            seen(shiny=True),
            EncounterStatus(captured=True, special=True),
            "ya capturado",
        ),
    ],
)
def test_ver_reports_pokedex_state_not_the_individual(
    monkeypatch, capsys, encounter, status, expected
):
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: encounter)
    _stub_status(monkeypatch, status)

    assert cli.cmd_ver(args()) == 0
    out = capsys.readouterr().out
    assert expected in out


def test_ver_renders_the_current_encounter_sprite(monkeypatch):
    encounter = seen(species="raichu", form="alola", shiny=True)
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: encounter)
    _stub_status(monkeypatch, EncounterStatus(captured=False, special=True))
    renderer = MagicMock()
    renderer.capture_sprite.return_value = "SPRITE"
    monkeypatch.setattr(cli, "_sprite_renderer", lambda: renderer)

    with cli.console.capture() as output:
        assert cli.cmd_ver(args()) == 0

    renderer.capture_sprite.assert_called_once_with("raichu", "alola", True)
    assert "SPRITE" in output.get()


def test_ver_tells_the_mood_in_words_and_never_the_numbers(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.composition,
        "read_encounter",
        lambda: seen(catch_stage=2, escape_after_attempts=4, item_turns=2),
    )
    _stub_status(monkeypatch, EncounterStatus(captured=False, special=False))

    assert cli.cmd_ver(args()) == 0
    out = capsys.readouterr().out
    assert "está enfadado" in out
    assert "paciencia" not in out
    assert "×4" not in out


def test_ver_debug_is_the_only_place_the_numbers_show_up(monkeypatch):
    monkeypatch.setattr(
        cli.composition,
        "read_encounter",
        lambda: seen(catch_stage=-1, escape_after_attempts=6, item_turns=1),
    )
    _stub_status(monkeypatch, EncounterStatus(captured=False, special=False))

    with cli.console.capture() as output:
        assert cli.cmd_ver(args(debug=True)) == 0

    assert "captura ÷2 · paciencia 5/6" in _flat(output.get())


def test_ver_says_nothing_about_the_safari_before_anyone_touches_the_encounter(monkeypatch, capsys):
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: seen())
    _stub_status(monkeypatch, EncounterStatus(captured=False, special=False))

    assert cli.cmd_ver(args()) == 0
    assert "paciencia" not in capsys.readouterr().out


def _stub_safari(monkeypatch, result, *, cache=None):
    use_case = MagicMock()
    use_case.execute.return_value = result
    monkeypatch.setattr(cli.composition, "throw_safari_item", lambda: use_case)
    species_data = MagicMock()
    species_data.execute.return_value = cache
    monkeypatch.setattr(cli, "_species_data_use_case", lambda: species_data)
    monkeypatch.setattr(cli.animation, "play_safari_item_animation", MagicMock())
    return use_case, species_data


def safari_result(status, **overrides):
    values = {
        "action": cli.safari_rules.SafariAction.ROCK,
        "catch_stage": 1,
        "patience": 3,
        "remaining_turns": 2,
        "stage_delta": 1,
        "patience_gained": 0,
        "mood": cli.safari_rules.SafariMood.ANGRY,
    }
    values.update(overrides)
    return cli.safari_application.SafariResult(status, **values)


def bait_result(status, **overrides):
    values = {
        "action": cli.safari_rules.SafariAction.BAIT,
        "catch_stage": -1,
        "patience": 6,
        "remaining_turns": 5,
        "stage_delta": -1,
        "patience_gained": 2,
        "mood": cli.safari_rules.SafariMood.EATING,
    }
    values.update(overrides)
    return safari_result(status, **values)


@pytest.mark.parametrize(
    ("encounter", "expected_code", "text"),
    [
        (None, 1, "No hay ningún Pokémon"),
        (seen(captured=True), 0, "Ya capturaste"),
    ],
)
def test_safari_items_short_circuit_without_spending_a_turn(
    monkeypatch, capsys, encounter, expected_code, text
):
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: encounter)
    use_case, _ = _stub_safari(monkeypatch, safari_result(cli.safari_application.SafariStatus.FLED))

    assert cli.cmd_roca(args()) == expected_code
    assert text in capsys.readouterr().out
    use_case.execute.assert_not_called()


def test_rock_says_it_is_angry_without_leaking_any_number(monkeypatch):
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: seen(escape_after_attempts=4))
    _stub_safari(monkeypatch, safari_result(cli.safari_application.SafariStatus.APPLIED))

    with cli.console.capture() as output:
        assert cli.cmd_roca(args()) == 0

    rendered = _flat(output.get())
    assert "está enfadado" in rendered
    assert "paciencia" not in rendered
    assert "×2" not in rendered


def test_bait_says_it_is_eating_without_leaking_any_number(monkeypatch):
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: seen(escape_after_attempts=4))
    _stub_safari(monkeypatch, bait_result(cli.safari_application.SafariStatus.APPLIED))

    with cli.console.capture() as output:
        assert cli.cmd_cebo(args()) == 0

    rendered = _flat(output.get())
    assert "está comiendo" in rendered
    assert "paciencia" not in rendered


def test_the_numbers_are_available_behind_debug(monkeypatch):
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: seen(escape_after_attempts=4))
    _stub_safari(monkeypatch, bait_result(cli.safari_application.SafariStatus.APPLIED))

    with cli.console.capture() as output:
        assert cli.cmd_cebo(args(debug=True)) == 0

    assert "captura ÷2 · paciencia 5/6" in _flat(output.get())


def test_an_item_plays_its_own_animation(monkeypatch):
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: seen(escape_after_attempts=4))
    _stub_safari(monkeypatch, safari_result(cli.safari_application.SafariStatus.FLED))
    played = MagicMock()
    monkeypatch.setattr(cli.animation, "play_safari_item_animation", played)

    with cli.console.capture():
        assert cli.cmd_roca(args()) == 0

    assert played.call_args.kwargs["action"] == "rock"
    assert played.call_args.kwargs["fled"] is True


@pytest.mark.parametrize(
    ("command", "expected"),
    [("roca", "se ha enfadado y ha huido"), ("cebo", "se ha hartado del cebo y ha huido")],
)
def test_an_item_can_end_the_encounter(monkeypatch, command, expected):
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: seen(escape_after_attempts=2))
    builder = safari_result if command == "roca" else bait_result
    _stub_safari(monkeypatch, builder(cli.safari_application.SafariStatus.FLED))

    with cli.console.capture() as output:
        run = cli.cmd_roca if command == "roca" else cli.cmd_cebo
        assert run(args()) == 0

    assert expected in _flat(output.get())


def test_a_saturated_item_says_it_is_being_ignored(monkeypatch):
    monkeypatch.setattr(
        cli.composition, "read_encounter", lambda: seen(escape_after_attempts=9, catch_stage=3)
    )
    _stub_safari(
        monkeypatch,
        safari_result(
            cli.safari_application.SafariStatus.APPLIED,
            catch_stage=3,
            patience=9,
            remaining_turns=4,
            stage_delta=0,
            patience_gained=0,
        ),
    )

    with cli.console.capture() as output:
        assert cli.cmd_roca(args()) == 0

    assert "ya no aparta la vista de ti" in _flat(output.get())


def test_an_item_only_looks_up_species_data_when_the_patience_is_not_set_yet(monkeypatch):
    cache = {"capture_rate": 45, "spe": 100, "is_legendary": 0, "is_mythical": 0}
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: seen())
    use_case, species_data = _stub_safari(
        monkeypatch,
        safari_result(cli.safari_application.SafariStatus.APPLIED),
        cache=cache,
    )

    with cli.console.capture():
        assert cli.cmd_roca(args()) == 0

    species_data.execute.assert_called_once_with("pikachu", "regular")
    command = use_case.execute.call_args.args[0]
    assert (command.capture_rate, command.speed) == (45, 100)
    assert command.action is cli.safari_rules.SafariAction.ROCK

    # Con la paciencia ya fijada no hace falta consultar nada.
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: seen(escape_after_attempts=4))
    species_data.execute.reset_mock()
    with cli.console.capture():
        assert cli.cmd_roca(args()) == 0
    species_data.execute.assert_not_called()


def test_ver_reports_when_nothing_is_waiting(monkeypatch, capsys):
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: None)
    assert cli.cmd_ver(args()) == 1
    assert "No hay ningún Pokémon" in capsys.readouterr().out


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


def _stub_capture(monkeypatch, result, *, encounter=None):
    monkeypatch.setattr(cli.composition, "read_encounter", lambda: encounter or seen())
    monkeypatch.setattr(cli, "_sync_training", lambda: (sync_result(), ()))
    species_data = MagicMock()
    species_data.execute.return_value = None
    monkeypatch.setattr(cli, "_species_data_use_case", lambda: species_data)
    use_case = MagicMock()
    use_case.execute.return_value = result
    monkeypatch.setattr(cli, "_capture_encounter_use_case", lambda: use_case)
    monkeypatch.setattr(cli.animation, "play_capture_animation", MagicMock())


def test_a_failed_ball_says_only_that_it_broke_out(monkeypatch):
    """El fallo se cuenta como en el juego: ni cifras ni consejos de comandos."""
    _stub_capture(
        monkeypatch,
        cli.capture_application.CaptureResult(
            cli.capture_application.CaptureStatus.FAILED,
            chance=0.2,
            remaining_turns=2,
            escape_after=4,
        ),
    )
    monkeypatch.setattr(cli.capture, "breakout_message", lambda: "Se soltó")

    with cli.console.capture() as output:
        assert cli.cmd_capturar(args()) == 0

    rendered = _flat(output.get())
    assert rendered.strip() == "Se soltó"


def test_debug_gathers_probability_mood_and_turns(monkeypatch):
    _stub_capture(
        monkeypatch,
        cli.capture_application.CaptureResult(
            cli.capture_application.CaptureStatus.FAILED,
            chance=0.05,
            remaining_turns=2,
            escape_after=4,
        ),
        encounter=seen(catch_stage=-2),
    )

    with cli.console.capture() as output:
        assert cli.cmd_capturar(args(debug=True)) == 0

    rendered = _flat(output.get())
    assert "probabilidad de captura: 5.0%" in rendered
    assert "ánimo ÷4" in rendered
    assert "turnos 2/4" in rendered


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
    # La Zona Safari se explica donde el jugador consulta sus recursos.
    assert "Zona Safari" in rendered
    assert "pokedex roca" in rendered
    assert "pokedex cebo" in rendered


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
                accion="bola",
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
                accion="bola",
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


@pytest.mark.parametrize(
    ("accion", "result", "expected_action", "expected_fled", "expected_text"),
    [
        ("roca", "random", "rock", False, "está enfadado"),
        ("cebo", "random", "bait", False, "está comiendo"),
        ("roca", "escape", "rock", True, "se ha enfadado y ha huido"),
    ],
)
def test_safari_demo_plays_the_item_animation_without_touching_state(
    monkeypatch, accion, result, expected_action, expected_fled, expected_text
):
    played = MagicMock()
    monkeypatch.setattr(cli.animation, "play_safari_item_animation", played)
    capture_animation = MagicMock()
    monkeypatch.setattr(cli.animation, "play_capture_animation", capture_animation)
    namespace = argparse.Namespace(
        nombre="eevee",
        legendary=False,
        form="regular",
        shiny=False,
        generations="1-9",
        bola="poke",
        result=result,
        accion=accion,
    )

    with cli.console.capture() as output:
        assert cli.cmd_demo(namespace) == 0

    assert played.call_args.kwargs["action"] == expected_action
    assert played.call_args.kwargs["fled"] is expected_fled
    capture_animation.assert_not_called()
    rendered = _flat(output.get())
    assert expected_text in rendered
    assert "no se guarda nada" in rendered


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


@pytest.mark.parametrize(("shell", "filename"), [("bash", "pokedex.bash"), ("zsh", "_pokedex.zsh")])
def test_completion_reads_project_file_then_reports_missing(
    monkeypatch, tmp_path, capsys, shell, filename
):
    completion = tmp_path / "completions" / filename
    completion.parent.mkdir()
    completion.write_text("# completion")
    monkeypatch.setattr(cli.composition.paths, "PROJECT_DIR", tmp_path)

    assert cli.cmd_completion(argparse.Namespace(shell=shell)) == 0
    assert capsys.readouterr().out == "# completion"

    completion.unlink()
    monkeypatch.setenv("HOME", str(tmp_path / "missing-home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "missing-data"))
    assert cli.cmd_completion(argparse.Namespace(shell=shell)) == 1
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
