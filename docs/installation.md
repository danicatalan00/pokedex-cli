# Instalación y empaquetamiento

## Requisitos

- Python 3.11–3.13 con `venv`.
- `rich` y `requests` disponibles para el Python del sistema.
- Bash 4 o superior, Zsh, o ambos: el completado y el hook existen para los dos.
- Krabby es opcional, pero habilita la experiencia visual completa.

## Instalar o actualizar

```bash
./install.sh
```

El instalador es idempotente y genera una wheel antes de actualizar una copia
estable. No deja una instalación editable ligada al checkout.

| Artefacto | Ruta predeterminada |
|---|---|
| Entorno estable | `~/.local/share/pokedex-cli/venv` |
| Estado SQLite | `~/.local/share/pokedex-cli/pokedex.db` |
| Shim | `~/bin/pokedex` |
| Completado Bash | `~/.local/share/bash-completion/completions/pokedex` |
| Completado Zsh | `~/.zfunc/_pokedex` |

XDG puede cambiar la primera, la segunda y el completado de Bash.

El completado de Bash queda en el directorio que el paquete `bash-completion`
carga solo. Para que también funcione sin ese paquete, el instalador añade a
`~/.bashrc` un bloque idempotente que lo carga a mano. En Zsh el instalador
añade `~/.zfunc` al `fpath` antes de `compinit`. Ninguno de los dos toca un
archivo que no exista ya, salvo `~/.bashrc`, que sí se crea si falta.

## Activar el encuentro al abrir una terminal

El instalador prepara el comando y el completado; el hook se habilita
explícitamente en el bloque interactivo de `~/.bashrc` o de `~/.zshrc`. El
bloque es el mismo en los dos shells:

```bash
if command -v pokedex >/dev/null 2>&1; then
    pokedex hook 1-3
elif command -v krabby >/dev/null 2>&1; then
    krabby random 1-3 --no-title -i
fi
```

Valida siempre el archivo que hayas tocado antes de abrir otra terminal:
`bash -n ~/.bashrc` o `zsh -n ~/.zshrc`.

## Comprobar la versión efectiva

Haz la comprobación fuera del repositorio:

```bash
cd /tmp
"$HOME/.local/share/pokedex-cli/venv/bin/python" -c \
  'import pokedex_cli; print(pokedex_cli.__file__)'
```

La ruta debe terminar en `site-packages/pokedex_cli`, no en el checkout. Tras
cualquier cambio aceptado repite `./install.sh`; el ciclo completo está en
[gates para cambios](change-gates.md#dejar-la-versión-efectiva-lista).
