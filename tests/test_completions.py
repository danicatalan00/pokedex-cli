"""Los completados que se instalan son contrato del CLI: se ejecutan de verdad."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import pytest

from pokedex_cli.infrastructure import paths
from pokedex_cli.presentation import cli

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMPLETIONS_DIR = PROJECT_ROOT / "completions"
BASH_COMPLETION = COMPLETIONS_DIR / "pokedex.bash"
ZSH_COMPLETION = COMPLETIONS_DIR / "_pokedex.zsh"
BASH = shutil.which("bash")
ZSH = shutil.which("zsh")

# Reproduce el troceo que readline entrega a la función de completado: palabras
# sueltas y, como hace bash, "--bola=poke" partido en tres por COMP_WORDBREAKS.
DRIVER = r"""
set -u
source "$1"
input=$2
COMP_LINE=$input
COMP_POINT=${#input}
read -r -a typed <<< "$input"
words=()
for token in ${typed+"${typed[@]}"}; do
    if [[ $token == -*=* ]]; then
        words+=( "${token%%=*}" "=" )
        value=${token#*=}
        [[ -n $value ]] && words+=( "$value" )
    else
        words+=( "$token" )
    fi
done
[[ $input == *" " ]] && words+=( "" )
COMP_WORDS=( ${words+"${words[@]}"} )
COMP_CWORD=$(( ${#COMP_WORDS[@]} - 1 ))
COMPREPLY=()
_pokedex
printf '%s\n' ${COMPREPLY+"${COMPREPLY[@]}"}
"""

STUB_KRABBY = "#!/bin/sh\nprintf '%s\\n' bulbasaur charizard pikachu\n"
STUB_POKEDEX = "#!/bin/sh\nprintf '%s\\n' 'ID Pokemon' '1 Pikachu' '7 Charizard'\n"


def cli_surface() -> dict[str, list[str]]:
    """Subcomandos y opciones reales del parser, para exigir paridad al completado.

    Toca la API privada de argparse a propósito: es la única forma de comparar
    el completado con la superficie real del CLI sin lanzar 18 procesos.
    """
    parser = cli.build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    surface: dict[str, list[str]] = {}
    for name, subparser in subparsers.choices.items():
        surface[name] = [
            option
            for action in subparser._actions
            for option in action.option_strings
            if option not in {"-h", "--help"}
        ]
    return surface


@pytest.fixture(scope="module")
def complete(tmp_path_factory: pytest.TempPathFactory):
    """Devuelve las candidatas que propone el completado bash para una línea."""
    stub_bin = tmp_path_factory.mktemp("stub-bin")
    (stub_bin / "krabby").write_text(STUB_KRABBY)
    (stub_bin / "pokedex").write_text(STUB_POKEDEX)
    for stub in stub_bin.iterdir():
        stub.chmod(0o755)
    driver = tmp_path_factory.mktemp("driver") / "drive.bash"
    driver.write_text(DRIVER)
    home = tmp_path_factory.mktemp("stub-home")

    def run(line: str) -> set[str]:
        assert BASH is not None
        result = subprocess.run(
            [BASH, str(driver), str(BASH_COMPLETION), line],
            env={"PATH": f"{stub_bin}:/usr/bin:/bin", "HOME": str(home)},
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        return set(result.stdout.split())

    return run


def test_bash_completion_is_valid_and_registers_the_command() -> None:
    assert BASH is not None
    subprocess.run([BASH, "-n", str(BASH_COMPLETION)], check=True, timeout=15)
    registration = subprocess.run(
        [BASH, "--norc", "-c", f'source "{BASH_COMPLETION}"; complete -p pokedex'],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert "-F _pokedex pokedex" in registration.stdout


@pytest.mark.skipif(ZSH is None, reason="zsh no está instalado")
def test_zsh_completion_is_syntactically_valid() -> None:
    assert ZSH is not None
    subprocess.run([ZSH, "-n", str(ZSH_COMPLETION)], check=True, timeout=15)


@pytest.mark.parametrize("completion", [BASH_COMPLETION, ZSH_COMPLETION], ids=["bash", "zsh"])
def test_completions_cover_every_subcommand_and_option(completion: Path) -> None:
    script = completion.read_text()
    for command, options in cli_surface().items():
        assert command in script, f"falta el subcomando {command}"
        for option in options:
            assert option in script, f"falta {command} {option}"


def test_bash_completion_proposes_every_subcommand(complete) -> None:
    proposed = complete("pokedex ")
    assert set(cli_surface()) - {"piedra", "caramelo"} <= proposed


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("pokedex ca", {"capturar"}),
        ("pokedex capturar --", {"--debug", "--bola", "--help"}),
        ("pokedex capturar -b ", {"poke", "super", "ultra", "master"}),
        ("pokedex capturar --bola=u", {"ultra"}),
        ("pokedex bolsas --", {"--info", "--help"}),
        ("pokedex ranking --", {"--equipo", "--help"}),
        ("pokedex piedra --", {"--debug", "--help"}),
        ("pokedex caramelo --", {"--debug", "--help"}),
        ("pokedex demo -a ", {"bola", "roca", "cebo"}),
        ("pokedex demo -r ", {"random", "catch", "escape"}),
        ("pokedex demo-evolucion --speed ", {"0.7", "1.0", "1.4"}),
        ("pokedex completion ", {"bash", "zsh"}),
        ("pokedex equipo ", {"add", "remove"}),
        ("pokedex list ", set()),
        ("pokedex hook ", set()),
        ("pokedex demo bulbasaur ", set()),
    ],
)
def test_bash_completion_offers_the_expected_words(complete, line: str, expected: set[str]) -> None:
    assert complete(line) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("pokedex search char", {"charizard"}),
        ("pokedex demo pika", {"pikachu"}),
        ("pokedex demo-vision bulba", {"bulbasaur"}),
        ("pokedex demo-evolucion bulbasaur char", {"charizard"}),
        # El valor pegado con '=' no debe contarse como argumento posicional.
        ("pokedex demo --bola=poke pika", {"pikachu"}),
        ("pokedex demo -f mega-x char", {"charizard"}),
        ("pokedex vision ", {"1", "7"}),
        ("pokedex equipo add ", {"1", "7", "bulbasaur", "charizard", "pikachu"}),
    ],
)
def test_bash_completion_asks_the_environment(complete, line: str, expected: set[str]) -> None:
    assert complete(line) == expected


@pytest.mark.parametrize(
    ("shell", "filename"),
    [("bash", "pokedex.bash"), ("zsh", "_pokedex.zsh")],
)
def test_completion_file_prefers_the_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shell: str, filename: str
) -> None:
    shipped = tmp_path / "completions" / filename
    shipped.parent.mkdir()
    shipped.write_text("# completion")
    monkeypatch.setattr(paths, "PROJECT_DIR", tmp_path)

    assert paths.completion_file(shell) == shipped


@pytest.mark.parametrize(
    ("shell", "relative"),
    [
        ("bash", "data/bash-completion/completions/pokedex"),
        ("zsh", "home/.zfunc/_pokedex"),
    ],
)
def test_completion_file_falls_back_to_the_installed_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shell: str, relative: str
) -> None:
    monkeypatch.setattr(paths, "PROJECT_DIR", tmp_path / "sin-checkout")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    assert paths.completion_file(shell) == tmp_path / relative
