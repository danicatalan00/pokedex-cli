# Instalación y empaquetamiento

## Requisitos

- Python 3.11–3.13 con `venv`.
- `rich` y `requests` disponibles para el Python del sistema.
- Zsh; Krabby es opcional, pero habilita la experiencia visual completa.

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
| Completado Zsh | `~/.zfunc/_pokedex` |

XDG puede cambiar las dos primeras rutas.

## Activar el encuentro al abrir Zsh

El instalador prepara el comando y el completado; el hook se habilita
explícitamente en el bloque interactivo de `~/.zshrc`:

```zsh
if command -v pokedex >/dev/null 2>&1; then
    pokedex hook 1-3
elif command -v krabby >/dev/null 2>&1; then
    krabby random 1-3 --no-title -i
fi
```

Valida siempre `zsh -n ~/.zshrc` antes de abrir otra terminal.

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
