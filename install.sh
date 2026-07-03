#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv --system-site-packages "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -c "import rich, requests" \
    || { echo "install.sh: falta rich/requests en el sistema (python3-rich / python3-requests via apt)" >&2; exit 1; }

DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/pokedex-cli"
mkdir -p "$DATA_DIR"

mkdir -p "$HOME/bin"
cat > "$HOME/bin/pokedex" <<SHIM
#!/usr/bin/env bash
PROJECT_DIR="$PROJECT_DIR"
VENV_PY="\$PROJECT_DIR/.venv/bin/python"
if [[ ! -x "\$VENV_PY" ]]; then
    echo "pokedex: no se encontró el entorno virtual en \$PROJECT_DIR/.venv" >&2
    echo "pokedex: ¿se movió o borró el proyecto? Reinstala con install.sh" >&2
    exit 1
fi
exec env PYTHONPATH="\$PROJECT_DIR\${PYTHONPATH:+:\$PYTHONPATH}" "\$VENV_PY" -m pokedex_cli "\$@"
SHIM
chmod +x "$HOME/bin/pokedex"

echo "Listo. Abre una terminal nueva o \`source ~/.zshrc\`."
