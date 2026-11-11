# Guía de testing

## Preparar el entorno

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Los tests ordinarios bloquean red real y aíslan `HOME`, XDG, SQLite y Git. Un
test nuevo debe conservar esas fronteras.

## Elegir el nivel correcto

- **Dominio:** reglas e invariantes, sin mocks de infraestructura.
- **Aplicación:** un caso de uso con puertos falsos y dependencias controladas.
- **Infraestructura:** contratos contra SQLite, procesos o transportes reales
  temporales.
- **CLI/E2E:** comportamiento observable mediante `python -m pokedex_cli`.
- **Shell:** los completados se ejecutan en un bash o zsh reales, con órdenes
  falsas en `PATH` en lugar de krabby o del propio `pokedex`.
- **Install/stress:** empaquetamiento y concurrencia; se ejecutan por separado.

Prefiere el test más bajo que pueda demostrar el riesgo. Usa Hypothesis para
invariantes y bases temporales reales para transacciones o migraciones.

## Tests del combate

- **Dominio:** `tests/domain/battle/`, con cifras exactas de las reglas, el
  motor con azar guionizado y combates IA contra IA con Hypothesis.
- **Aplicación:** `tests/application/test_battle.py` con dobles,
  `test_battle_sqlite.py` con SQLite temporal y repositorios reales,
  `test_moves.py`, `test_movesets.py` y los casos de combate de
  `test_capture.py` y `test_hook.py`.
- **Infraestructura:** `tests/infrastructure/test_battle_repositories.py`,
  `test_learnsets.py` y `test_pokeapi_moves.py` (recortes de payloads reales),
  más los casos de combate de `test_pokeapi.py`, `test_species_cache.py` y
  `test_database.py` (migraciones).
- **Presentación:** `tests/presentation/test_battle_text.py`,
  `test_battle_scene.py`, `test_battle_director.py` y `test_tui_battle.py`
  (Pilot de Textual con sesiones guionizadas).
- **CLI/E2E:** `tests/test_cli_battle.py` (consola de Rich en memoria, teclado
  guionizado y un pseudoterminal para el teclado real) y los combates de
  `tests/test_cli_e2e.py`, que siembran especies y movimientos en una base
  temporal; los de la reserva offline desvían HTTP a un proxy local
  inalcanzable.

## Ciclo de trabajo

1. Escribe una prueba que falle por la conducta esperada.
2. Implementa el cambio mínimo.
3. Ejecuta primero el archivo o caso afectado.
4. Refactoriza con la prueba verde.
5. Aplica los [gates proporcionales al riesgo](change-gates.md).

Comandos de referencia:

```bash
.venv/bin/pytest -q tests/ruta/test_afectado.py
.venv/bin/pytest -q -m 'not install and not stress'
.venv/bin/pytest -q -m 'install or stress'
```

Mutation testing se reserva para captura, recompensas, progresión y actividad;
comprueba la fuerza de las pruebas, no sustituye cobertura ni revisión.
