import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.install


def run_install(home: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update({"HOME": str(home), "XDG_DATA_HOME": str(home / "data")})
    return subprocess.run(
        ["bash", str(PROJECT_ROOT / "install.sh")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )


def bash_completion_path(home: Path) -> Path:
    return home / "data" / "bash-completion" / "completions" / "pokedex"


@pytest.fixture(scope="module")
def installed_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    home = tmp_path_factory.mktemp("installed-home") / "home"
    home.mkdir()
    (home / ".zshrc").write_text("# existing configuration\nexport EDITOR=vim\n")
    (home / ".bashrc").write_text("# existing bash configuration\nexport PAGER=less\n")
    first = run_install(home)
    (home / ".zcompdump-stale").write_text("old completion cache")
    second = run_install(home)
    assert first.returncode == second.returncode == 0
    assert not (home / ".zcompdump-stale").exists()
    return home


@pytest.fixture(scope="module")
def bare_home(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, subprocess.CompletedProcess[str]]:
    """Una máquina solo-bash: no hay ~/.zshrc ni ~/.bashrc que tocar."""
    home = tmp_path_factory.mktemp("bare-home") / "home"
    home.mkdir()
    return home, run_install(home)


def test_install_is_idempotent_and_preserves_existing_zshrc(
    installed_home: Path,
) -> None:
    home = installed_home
    zshrc = home / ".zshrc"
    contents = zshrc.read_text()
    assert "# existing configuration" in contents
    assert "export EDITOR=vim" in contents
    assert contents.count("# pokedex-cli: autocompletado") == 1
    assert contents.count("unfunction _pokedex 2>/dev/null") == 1
    shim = home / "bin" / "pokedex"
    assert shim.stat().st_mode & 0o111
    assert "PYTHONPATH" not in shim.read_text()
    assert (
        subprocess.run([shim, "--help"], capture_output=True, text=True, timeout=10).returncode == 0
    )
    assert (home / ".zfunc" / "_pokedex").read_text() == (
        PROJECT_ROOT / "completions" / "_pokedex.zsh"
    ).read_text()


def test_install_is_idempotent_and_preserves_existing_bashrc(installed_home: Path) -> None:
    home = installed_home
    contents = (home / ".bashrc").read_text()
    assert "# existing bash configuration" in contents
    assert "export PAGER=less" in contents
    assert contents.count("# pokedex-cli: autocompletado") == 1
    assert (
        bash_completion_path(home).read_text()
        == (PROJECT_ROOT / "completions" / "pokedex.bash").read_text()
    )


def test_installed_bashrc_registers_the_completion(installed_home: Path) -> None:
    home = installed_home
    result = subprocess.run(
        ["bash", "--norc", "-c", 'source "$HOME/.bashrc"; complete -p pokedex'],
        env={
            **os.environ,
            "HOME": str(home),
            "XDG_DATA_HOME": str(home / "data"),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "-F _pokedex pokedex" in result.stdout


def test_generated_shim_reports_missing_environment_cleanly(
    tmp_path: Path, installed_home: Path
) -> None:
    home = installed_home
    original = home / "bin" / "pokedex"
    isolated = tmp_path / "missing-env-shim"
    isolated.write_text(
        original.read_text().replace(
            str(home / "data" / "pokedex-cli" / "venv"),
            str(tmp_path / "missing-environment"),
        )
    )
    isolated.chmod(0o755)

    result = subprocess.run([isolated, "--help"], capture_output=True, text=True)
    assert result.returncode == 1
    assert "no se encontró el entorno instalado" in result.stderr
    assert "Traceback" not in result.stderr


def test_completion_loads_with_compinit(installed_home: Path) -> None:
    home = installed_home
    result = subprocess.run(
        [
            "zsh",
            "-dfc",
            f"fpath=({home / '.zfunc'} $fpath); autoload -Uz compinit; "
            "compinit -D; whence -w _pokedex",
        ],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert "_pokedex: function" in result.stdout


def test_install_completes_without_any_shell_rc_file(
    bare_home: tuple[Path, subprocess.CompletedProcess[str]],
) -> None:
    home, result = bare_home
    assert result.returncode == 0, result.stderr
    # Bash se queda listo; zsh solo se avisa, no se inventa un ~/.zshrc.
    assert "# pokedex-cli: autocompletado" in (home / ".bashrc").read_text()
    assert bash_completion_path(home).exists()
    assert not (home / ".zshrc").exists()
    assert "no hay ~/.zshrc" in result.stderr
    assert (home / ".zfunc" / "_pokedex").exists()
