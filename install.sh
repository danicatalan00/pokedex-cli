#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
XDG_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
DATA_DIR="$XDG_DATA_DIR/pokedex-cli"
VENV_DIR="$DATA_DIR/venv"

mkdir -p "$DATA_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv --system-site-packages "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install \
    --disable-pip-version-check --no-deps --no-build-isolation --upgrade "$PROJECT_DIR"

"$VENV_DIR/bin/python" -c "import rich, requests" \
    || { echo "install.sh: falta rich/requests en el sistema (python3-rich / python3-requests via apt)" >&2; exit 1; }

# textual alimenta `pokedex` sin argumentos (la Pokédex interactiva). Si no se
# puede instalar (p.ej. sin red), la CLI clásica sigue funcionando igual.
if ! "$VENV_DIR/bin/python" -c "import textual" >/dev/null 2>&1; then
    "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check "textual>=1,<9" \
        || echo "install.sh: AVISO: no se pudo instalar textual; la Pokédex interactiva quedará deshabilitada" >&2
fi

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

# --- Autocompletado bash ----------------------------------------------------
# El fichero va al directorio XDG que el paquete bash-completion carga solo; el
# bloque de ~/.bashrc lo hace funcionar también sin ese paquete.
install_bash_completion() {
    local completion_dir="$XDG_DATA_DIR/bash-completion/completions"
    mkdir -p "$completion_dir"
    cp "$PROJECT_DIR/completions/pokedex.bash" "$completion_dir/pokedex"

    local bashrc="$HOME/.bashrc"
    if [[ ! -f "$bashrc" ]]; then
        : > "$bashrc"
        echo "Creado ~/.bashrc para cargar el autocompletado de pokedex."
    fi
    if grep -qF 'pokedex-cli: autocompletado' "$bashrc"; then
        return 0
    fi
    cat >> "$bashrc" <<'BBLOCK'

# pokedex-cli: autocompletado
_pokedex_completion="${XDG_DATA_HOME:-$HOME/.local/share}/bash-completion/completions/pokedex"
[ -r "$_pokedex_completion" ] && . "$_pokedex_completion"
unset _pokedex_completion
BBLOCK
    echo "Añadida la carga del autocompletado de pokedex en ~/.bashrc."
}

# --- Autocompletado zsh -----------------------------------------------------
install_zsh_completion() {
    local zfunc_dir="$HOME/.zfunc"
    mkdir -p "$zfunc_dir"
    cp "$PROJECT_DIR/completions/_pokedex.zsh" "$zfunc_dir/_pokedex"

    # El archivo puede cambiar aunque el bloque de ~/.zshrc ya existiera.
    # Invalida siempre el dump: la función en memoria se descargará al recargar.
    rm -f "$HOME"/.zcompdump* 2>/dev/null || true

    local zshrc="$HOME/.zshrc"
    if [[ ! -f "$zshrc" ]]; then
        # Zsh sin configurar (o máquina solo-bash): no inventamos un ~/.zshrc.
        echo "install.sh: no hay ~/.zshrc; el completado zsh queda en ~/.zfunc sin activar." >&2
        return 0
    fi

    local temporary
    if ! grep -qF 'pokedex-cli: autocompletado' "$zshrc"; then
        if grep -qF 'source $ZSH/oh-my-zsh.sh' "$zshrc"; then
            # oh-my-zsh ejecuta compinit al hacer `source`, así que el fpath DEBE
            # ir antes de esa línea o el completado nunca se carga.
            temporary="$(mktemp)"
            awk '
                !inserted && index($0, "source $ZSH/oh-my-zsh.sh") {
                    print "# pokedex-cli: autocompletado (antes del compinit de oh-my-zsh)"
                    print "fpath=(\"$HOME/.zfunc\" $fpath)"
                    print "unfunction _pokedex 2>/dev/null"
                    print ""
                    inserted = 1
                }
                { print }
            ' "$zshrc" > "$temporary" && mv "$temporary" "$zshrc"
            echo "Insertado ~/.zfunc en el fpath antes de oh-my-zsh en ~/.zshrc."
        else
            cat >> "$zshrc" <<'ZBLOCK'

# pokedex-cli: autocompletado
fpath=("$HOME/.zfunc" $fpath)
unfunction _pokedex 2>/dev/null
autoload -Uz compinit && compinit
ZBLOCK
            echo "Añadido ~/.zfunc al fpath en ~/.zshrc para el autocompletado."
        fi
    fi

    if ! grep -qF 'unfunction _pokedex 2>/dev/null' "$zshrc"; then
        temporary="$(mktemp)"
        awk '
            { print }
            !inserted && index($0, "# pokedex-cli: autocompletado") {
                marker = 1
                next
            }
            marker && !inserted && index($0, "fpath=(") {
                print "unfunction _pokedex 2>/dev/null"
                inserted = 1
            }
        ' "$zshrc" > "$temporary" && mv "$temporary" "$zshrc"
    fi
}

install_bash_completion
install_zsh_completion

case ":$PATH:" in
    *":$HOME/bin:"*) ;;
    *) echo "install.sh: AVISO: $HOME/bin no está en PATH; añádelo en tu ~/.bashrc o ~/.zshrc" >&2 ;;
esac

echo "Listo. Abre una terminal nueva o recarga tu shell (\`source ~/.bashrc\` o \`source ~/.zshrc\`)."
