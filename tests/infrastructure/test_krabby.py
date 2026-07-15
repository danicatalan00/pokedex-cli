import subprocess
from pathlib import Path

from pokedex_cli.infrastructure.krabby import KrabbyClient


class Result:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout


def test_available_renderer_returns_valid_ansi_sprite(tmp_path: Path) -> None:
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return Result("\x1b[31m██\x1b[0m\n")

    client = KrabbyClient(runner=runner, pokemon_json_path=tmp_path / "pokemon.json")
    assert client.capture_sprite("charizard", "mega-x", shiny=True) == "\x1b[31m██\x1b[0m"
    assert calls[0][0] == [
        "krabby",
        "name",
        "charizard",
        "-f",
        "mega-x",
        "-s",
        "--no-title",
    ]


def test_render_sprite_builds_the_requested_external_command(tmp_path: Path) -> None:
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return Result()

    client = KrabbyClient(runner=runner, pokemon_json_path=tmp_path / "pokemon.json")
    client.render_sprite("charizard", "mega-x", True, show_title=False, info=True)

    assert calls == [
        (
            ["krabby", "name", "charizard", "-f", "mega-x", "-i", "-s", "--no-title"],
            {"check": True, "timeout": 5},
        )
    ]


def test_absent_binary_falls_back_to_none(tmp_path: Path) -> None:
    def runner(args, **kwargs):
        raise FileNotFoundError("krabby")

    client = KrabbyClient(runner=runner, pokemon_json_path=tmp_path / "pokemon.json")
    assert client.capture_sprite("pikachu", "regular", shiny=False) is None


def test_unknown_form_falls_back_to_none(tmp_path: Path) -> None:
    def runner(args, **kwargs):
        raise subprocess.CalledProcessError(1, args)

    client = KrabbyClient(runner=runner, pokemon_json_path=tmp_path / "pokemon.json")
    assert client.capture_sprite("tauros", "unknown", shiny=False) is None


def test_invalid_ansi_is_rejected(tmp_path: Path) -> None:
    client = KrabbyClient(
        runner=lambda args, **kwargs: Result("sprite\x1bBROKEN"),
        pokemon_json_path=tmp_path / "pokemon.json",
    )
    assert client.capture_sprite("pikachu", "regular", shiny=False) is None


def test_missing_or_invalid_cache_returns_none(tmp_path: Path) -> None:
    cache = tmp_path / "pokemon.json"
    client = KrabbyClient(runner=lambda *args, **kwargs: Result(), pokemon_json_path=cache)
    assert client.load_database() is None
    cache.write_text("{")
    assert client.load_database() is None
    cache.write_text("{}")
    assert client.load_database() is None
