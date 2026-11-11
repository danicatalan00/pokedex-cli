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

# El completado de zsh también se ejecuta de verdad: un zsh interactivo dentro
# de un pseudoterminal (zpty) carga _pokedex con compinit y recibe la línea con
# un Tab. compadd se sustituye para imprimir las candidatas en vez de pintarlas
# y, al acabar de completar, ese zsh sale.
ZSH_SETUP = r"""
PROMPT=
fpath=($POKEDEX_FPATH $fpath)
autoload -Uz compinit
compinit -u -D
bindkey '^M' undefined
bindkey '^J' undefined
bindkey '^I' complete-word
null-line () { echo -E - $'\0' }
compprefuncs=( null-line )
comppostfuncs=( null-line exit )
zstyle ':completion:*' list-grouped false
zstyle ':completion:*' insert-tab false
compadd () {
    if [[ ${@[1,(i)(-|--)]} == *-(O|A|D)\ * ]]; then
        builtin compadd "$@"
        return $?
    fi
    local -a hits
    builtin compadd -A hits "$@"
    local hit
    for hit in $hits; do
        echo -E - "$IPREFIX$hit"
    done
}
echo ok
"""
ZSH_DRIVER = r"""
zmodload zsh/zpty || exit 3
zpty z zsh -f -i
zpty -w z "source ${(q)POKEDEX_SETUP}"
repeat 8; do
    zpty -r z line
    [[ $line == ok* ]] && break
done
zpty -w z "$1"$'\t'
integer marks=0
while zpty -r z; do :; done | while IFS= read -r line; do
    if [[ $line == *$'\0\r' ]]; then
        (( marks++ )) && break || continue
    fi
    (( marks )) && echo -E - ${line%$'\r'}
done
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


@pytest.fixture(scope="module")
def complete_zsh(tmp_path_factory: pytest.TempPathFactory):
    """Devuelve las candidatas que propone el completado real de zsh para una línea."""
    if ZSH is None:
        pytest.skip("zsh no está instalado")
    probe = subprocess.run(
        [ZSH, "-f", "-c", "zmodload zsh/zpty"], capture_output=True, timeout=15, check=False
    )
    if probe.returncode != 0:
        pytest.skip("este zsh no tiene el módulo zsh/zpty")
    stub_bin = tmp_path_factory.mktemp("zsh-stub-bin")
    (stub_bin / "krabby").write_text(STUB_KRABBY)
    (stub_bin / "pokedex").write_text(STUB_POKEDEX)
    for stub in stub_bin.iterdir():
        stub.chmod(0o755)
    functions = tmp_path_factory.mktemp("zsh-fpath")
    (functions / "_pokedex").write_text(ZSH_COMPLETION.read_text())
    work = tmp_path_factory.mktemp("zsh-driver")
    (work / "setup.zsh").write_text(ZSH_SETUP)
    driver = work / "drive.zsh"
    driver.write_text(ZSH_DRIVER)
    home = tmp_path_factory.mktemp("zsh-stub-home")

    def run(line: str) -> set[str]:
        assert ZSH is not None
        result = subprocess.run(
            [ZSH, "-f", str(driver), line],
            env={
                "PATH": f"{stub_bin}:/usr/bin:/bin",
                "HOME": str(home),
                "POKEDEX_FPATH": str(functions),
                "POKEDEX_SETUP": str(work / "setup.zsh"),
            },
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
    # Los alias funcionan pero no se proponen: la lista se queda limpia.
    assert set(cli_surface()) - {"piedra", "caramelo", "batalla"} <= proposed


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
        ("pokedex com", {"combate", "completion"}),
        ("pokedex combate --", {"--entrenador", "--auto", "--velocidad", "--debug", "--help"}),
        (
            "pokedex batalla -",
            {"-e", "--entrenador", "--auto", "--velocidad", "--debug", "-h", "--help"},
        ),
        ("pokedex combate --velocidad ", {"0", "0.5", "1", "2"}),
        ("pokedex demo-combate --velocidad=", {"0", "0.5", "1", "2"}),
        ("pokedex demo-combate --", {"--nivel", "--auto", "--velocidad", "--debug", "--help"}),
        ("pokedex demo-combate -n ", set()),
        ("pokedex movimientos --", {"--set", "--auto", "--help"}),
        ("pokedex movimientos 7 --set ", set()),
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
        ("pokedex movimientos ", {"1", "7"}),
        ("pokedex movimientos 1 ", set()),
        ("pokedex demo-combate ", {"1", "7", "bulbasaur", "charizard", "pikachu"}),
        ("pokedex demo-combate 1 char", {"charizard"}),
        ("pokedex demo-combate -n 30 1 ", {"1", "7", "bulbasaur", "charizard", "pikachu"}),
        ("pokedex demo-combate 1 7 ", set()),
        ("pokedex combate ", set()),
    ],
)
def test_bash_completion_asks_the_environment(complete, line: str, expected: set[str]) -> None:
    assert complete(line) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("pokedex ca", {"capturar"}),
        ("pokedex com", {"combate", "completion"}),
        ("pokedex combate --", {"--entrenador", "--auto", "--velocidad", "--debug"}),
        ("pokedex batalla --velocidad ", {"0", "0.5", "1", "2"}),
        ("pokedex demo-combate --", {"--nivel", "--auto", "--velocidad", "--debug"}),
        ("pokedex demo-combate ", {"1", "7", "bulbasaur", "charizard", "pikachu"}),
        ("pokedex demo-combate 1 char", {"charizard"}),
        ("pokedex demo-combate -n 30 1 ", {"1", "7", "bulbasaur", "charizard", "pikachu"}),
        ("pokedex movimientos ", {"1", "7"}),
        ("pokedex movimientos --", {"--set", "--auto"}),
        ("pokedex movimientos 7 --set ", set()),
        ("pokedex movimientos --set ascuas,placaje ", {"1", "7"}),
    ],
)
def test_zsh_completion_offers_the_expected_words(
    complete_zsh, line: str, expected: set[str]
) -> None:
    assert complete_zsh(line) == expected


# Líneas con las opciones delante: lo que propone el completado detrás de ellas
# tiene que ser una orden que el parser acepte (paridad real, no solo nombres).
COMPLETED_BATTLE_LINES = [
    "pokedex movimientos --set ascuas,placaje ",
    "pokedex demo-combate -n 30 ",
    "pokedex demo-combate --velocidad 2 1 ",
    "pokedex combate --velocidad ",
]


@pytest.mark.parametrize("line", COMPLETED_BATTLE_LINES)
@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_the_parser_accepts_every_completed_battle_line(
    request: pytest.FixtureRequest, capsys: pytest.CaptureFixture[str], shell: str, line: str
) -> None:
    complete = request.getfixturevalue("complete" if shell == "bash" else "complete_zsh")
    proposed = complete(line)
    assert proposed
    parser = cli.build_parser()
    for word in sorted(proposed):
        argv = [*line.split()[1:], word]
        try:
            parser.parse_args(argv)
        except SystemExit:
            pytest.fail(f"{shell} propone «{' '.join(argv)}», que el parser rechaza")
    capsys.readouterr()


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
