from unittest.mock import MagicMock

from pokedex_cli.infrastructure import wild_encounters


def test_generation_specs_support_ranges_and_lists() -> None:
    assert wild_encounters.parse_generations("1-3") == {1, 2, 3}
    assert wild_encounters.parse_generations("1,3,6") == {1, 3, 6}


def test_shiny_rate_reads_config_and_falls_back_safely(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    monkeypatch.setattr(wild_encounters, "KRABBY_CONFIG_PATH", config)
    assert wild_encounters.get_shiny_rate() == wild_encounters.DEFAULT_SHINY_RATE
    config.write_text("shiny_rate = 0.25\n")
    assert wild_encounters.get_shiny_rate() == 0.25
    config.write_text("shiny_rate = 'invalid'\n")
    assert wild_encounters.get_shiny_rate() == wild_encounters.DEFAULT_SHINY_RATE


def test_local_database_is_validated_and_can_be_seeded_from_cargo_cache(
    tmp_path, monkeypatch
) -> None:
    local = tmp_path / "data" / "pokemon.json"
    source = tmp_path / "cargo" / "pokemon.json"
    source.parent.mkdir()
    source.write_text('[{"slug":"pikachu","gen":1,"forms":[]}]')
    monkeypatch.setattr(wild_encounters, "KRABBY_POKEMON_JSON", local)
    monkeypatch.setattr(wild_encounters.glob, "glob", lambda unused: [str(source)])
    monkeypatch.setattr(wild_encounters, "ensure_dirs", lambda: local.parent.mkdir())

    assert wild_encounters._ensure_pokemon_db()[0]["slug"] == "pikachu"
    local.write_text("{}")
    assert wild_encounters._ensure_pokemon_db() is None
    local.write_text("{")
    assert wild_encounters._ensure_pokemon_db() is None


def test_species_picker_uses_forms_and_falls_back_to_list(monkeypatch) -> None:
    monkeypatch.setattr(wild_encounters, "get_shiny_rate", lambda: 0.5)
    monkeypatch.setattr(wild_encounters.random, "random", lambda: 0.1)
    choices = iter(({"slug": "raichu", "gen": 1, "forms": ["alola"]}, "alola"))
    monkeypatch.setattr(wild_encounters.random, "choice", lambda unused: next(choices))
    monkeypatch.setattr(
        wild_encounters,
        "_ensure_pokemon_db",
        lambda: [
            {"slug": "raichu", "gen": 1, "forms": ["alola"]},
            {"slug": "charizard", "gen": 6, "forms": ["mega-x"]},
        ],
    )
    assert wild_encounters.pick_species_form_shiny("1") == ("raichu", "alola", True)

    monkeypatch.setattr(wild_encounters, "_ensure_pokemon_db", lambda: None)
    monkeypatch.setattr(wild_encounters, "list_pool", lambda generations: ["pikachu"])
    monkeypatch.setattr(wild_encounters.random, "choice", lambda values: values[0])
    assert wild_encounters.pick_species_form_shiny("1") == ("pikachu", "regular", True)


def test_list_and_render_commands_are_bounded(monkeypatch) -> None:
    runner = MagicMock()
    runner.return_value.stdout = "pikachu\nbulbasaur\n"
    monkeypatch.setattr(wild_encounters.subprocess, "run", runner)
    assert wild_encounters.list_pool("1-3") == ["pikachu", "bulbasaur"]
    wild_encounters.render_sprite("raichu", "alola", True, show_title=False, info=True)
    assert runner.call_args.args[0] == [
        "krabby",
        "name",
        "raichu",
        "-f",
        "alola",
        "-i",
        "-s",
        "--no-title",
    ]


def test_hook_writes_encounter_or_runs_best_effort_fallback(monkeypatch) -> None:
    write = MagicMock()
    render = MagicMock()
    monkeypatch.setattr(
        wild_encounters, "pick_species_form_shiny", lambda unused: ("pikachu", "regular", False)
    )
    monkeypatch.setattr(wild_encounters, "render_sprite", render)
    wild_encounters.run_hook("1", write)
    write.assert_called_once_with("pikachu", "regular", False)
    render.assert_called_once()

    fallback = MagicMock()
    monkeypatch.setattr(
        wild_encounters,
        "pick_species_form_shiny",
        MagicMock(side_effect=RuntimeError("broken")),
    )
    monkeypatch.setattr(wild_encounters, "_best_effort_fallback", fallback)
    wild_encounters.run_hook("1", write)
    fallback.assert_called_once_with("1")


def test_best_effort_fallback_never_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        wild_encounters.subprocess,
        "run",
        MagicMock(side_effect=FileNotFoundError("krabby")),
    )
    assert wild_encounters._best_effort_fallback("1") is None
