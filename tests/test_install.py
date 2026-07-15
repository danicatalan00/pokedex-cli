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
        timeout=30,
    )


@pytest.fixture(scope="module")
def installed_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    home = tmp_path_factory.mktemp("installed-home") / "home"
    home.mkdir()
    zshrc = home / ".zshrc"
    zshrc.write_text("# existing configuration\nexport EDITOR=vim\n")
    first = run_install(home)
    second = run_install(home)
    assert first.returncode == second.returncode == 0
    return home


def test_install_is_idempotent_and_preserves_existing_zshrc(
    installed_home: Path,
) -> None:
    home = installed_home
    zshrc = home / ".zshrc"
    contents = zshrc.read_text()
    assert "# existing configuration" in contents
    assert "export EDITOR=vim" in contents
    assert contents.count("# pokedex-cli: autocompletado") == 1
    shim = home / "bin" / "pokedex"
    assert shim.stat().st_mode & 0o111
    assert "PYTHONPATH" not in shim.read_text()
    assert (
        subprocess.run([shim, "--help"], capture_output=True, text=True, timeout=10).returncode == 0
    )
    assert (home / ".zfunc" / "_pokedex").read_text() == (
        PROJECT_ROOT / "completions" / "_pokedex.zsh"
    ).read_text()


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
