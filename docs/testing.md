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
- **Install/stress:** empaquetamiento y concurrencia; se ejecutan por separado.

Prefiere el test más bajo que pueda demostrar el riesgo. Usa Hypothesis para
invariantes y bases temporales reales para transacciones o migraciones.

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
