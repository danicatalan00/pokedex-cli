# Estándares de ingeniería

## Diseño

- Conserva la dirección `presentation → application → domain`.
- Añade efectos externos detrás de un puerto pequeño y enlázalos en
  `composition.py`.
- Mantén reglas de negocio puras; inyecta reloj, azar, transporte y repositorios.
- Prefiere cambios verticales pequeños a reorganizaciones masivas.
- Toda compatibilidad histórica debe ser deliberada y documentada.

## Estado y fallos

- SQLite es la única fuente de verdad mutable.
- Toda operación read-modify-write comparte una transacción.
- Los cambios de esquema son migraciones numeradas, idempotentes y probadas.
- El hook nunca propaga un fallo recuperable ni imprime traceback por defecto.
- Red y procesos externos siempre tienen timeout, validación y fallback.

## Pruebas y mantenibilidad

- Empieza por una prueba que exprese el riesgo observable.
- No accedas a Internet, al `HOME` real ni a datos reales desde tests.
- Prueba contratos y resultados, no detalles internos ni ANSI completo.
- Ruff define estilo; mypy estricto cubre las capas de núcleo.
- Cobertura y mutación son señales: prioriza invariantes y modos de fallo.

## Entrega

- Un commit tiene una intención y deja la suite pertinente verde.
- No mezcles cambios funcionales con movimientos mecánicos evitables.
- Sigue los [gates del cambio](change-gates.md) y reinstala la versión estable
  antes de darlo por terminado.
