#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/pokedex-cli"
VENV_DIR="$DATA_DIR/venv"

mkdir -p "$DATA_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv --system-site-packages "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install \
    --disable-pip-version-check --no-deps --no-build-isolation --upgrade "$PROJECT_DIR"

"$VENV_DIR/bin/python" -c "import rich, requests" \
    || { echo "install.sh: falta rich/requests en el sistema (python3-rich / python3-requests via apt)" >&2; exit 1; }

mkdir -p "$HOME/bin"
cat > "$HOME/bin/pokedex" <<SHIM
#!/usr/bin/env bash
VENV_PY="$VENV_DIR/bin/python"
if [[ ! -x "\$VENV_PY" ]]; then
    echo "pokedex: no se encontró el entorno instalado en $VENV_DIR" >&2
    echo "pokedex: reinstala ejecutando install.sh desde el proyecto" >&2
    exit 1
fi
exec "\$VENV_PY" -m pokedex_cli "\$@"
SHIM
chmod +x "$HOME/bin/pokedex"

# --- Autocompletado zsh -----------------------------------------------------
ZFUNC_DIR="$HOME/.zfunc"
mkdir -p "$ZFUNC_DIR"
cp "$PROJECT_DIR/completions/_pokedex.zsh" "$ZFUNC_DIR/_pokedex"

ZSHRC="$HOME/.zshrc"
if [[ -f "$ZSHRC" ]] && ! grep -qF 'pokedex-cli: autocompletado' "$ZSHRC"; then
    if grep -qF 'source $ZSH/oh-my-zsh.sh' "$ZSHRC"; then
        # oh-my-zsh ejecuta compinit al hacer `source`, así que el fpath DEBE
        # ir antes de esa línea o el completado nunca se carga.
        TMP="$(mktemp)"
        awk '
            !inserted && index($0, "source $ZSH/oh-my-zsh.sh") {
                print "# pokedex-cli: autocompletado (antes del compinit de oh-my-zsh)"
                print "fpath=(\"$HOME/.zfunc\" $fpath)"
                print ""
                inserted = 1
            }
            { print }
        ' "$ZSHRC" > "$TMP" && mv "$TMP" "$ZSHRC"
        echo "Insertado ~/.zfunc en el fpath antes de oh-my-zsh en ~/.zshrc."
    else
        cat >> "$ZSHRC" <<'ZBLOCK'

# pokedex-cli: autocompletado
fpath=("$HOME/.zfunc" $fpath)
autoload -Uz compinit && compinit
ZBLOCK
        echo "Añadido ~/.zfunc al fpath en ~/.zshrc para el autocompletado."
    fi
    # Fuerza a compinit a regenerar su caché para que recoja el nuevo completado.
    rm -f "$HOME"/.zcompdump* 2>/dev/null || true
fi

echo "Listo. Abre una terminal nueva o \`source ~/.zshrc\`."
