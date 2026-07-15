# Gates para cambios

Los gates crecen con el riesgo. No conviertas una corrección local en una espera
innecesaria, pero no cierres un cambio con una frontera afectada sin probar.

## Bucle ligero obligatorio

Tras cada cambio de código o configuración:

```bash
.venv/bin/pytest -q tests/ruta/test_afectado.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

Para documentación pura basta revisar enlaces/rutas, `git diff --check` y el
paso de instalación final.

## Escalar según el riesgo

| Cambio | Gate adicional |
|---|---|
| Dominio o caso de uso | Suite normal con cobertura |
| SQLite, migración o concurrencia | Tests de infraestructura y `stress` |
| CLI, hook o fallback | E2E y prueba degradada del hook |
| Instalador, shim o completado | Marker `install` en `HOME` temporal |
| Reglas críticas | Mutation testing antes de integrar |

Suite normal:

```bash
.venv/bin/pytest -q -m 'not install and not stress' \
  --cov=pokedex_cli --cov-report=term-missing --cov-fail-under=85
```

## Dejar la versión efectiva lista

Esta es la última acción de **todo cambio aceptado**, también antes de entregar
un commit a otra persona:

```bash
./install.sh
(cd /tmp && "$HOME/bin/pokedex" --help >/dev/null)
zsh -n "$HOME/.zshrc"
```

Esto construye una instalación estable en `site-packages`; la siguiente
terminal usa esa copia mediante `~/bin/pokedex`. No valides la instalación desde
el checkout: el directorio actual podría ocultar el paquete efectivo.

Termina con `git diff --check` y un árbol limpio o con cambios explícitamente
explicados. La guía completa de rutas está en [instalación](installation.md).
