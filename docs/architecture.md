# Arquitectura

El proceso se divide en cuatro capas pequeñas y una raíz de composición:

```text
pokedex_cli/domain/          reglas puras y modelos
pokedex_cli/application/     casos de uso y puertos
pokedex_cli/infrastructure/  SQLite, Git, HTTP, Krabby, rutas y diagnóstico
pokedex_cli/presentation/    parser, renderizado Rich y frames
pokedex_cli/composition.py   conecta puertos con adaptadores locales
```

La dirección normal es `presentation → application → domain`. La raíz de
composición conoce las implementaciones de infraestructura y las inyecta. La
presentación no abre SQLite, no ejecuta procesos externos y no consulta la red;
solo traduce argumentos, invoca casos de uso y renderiza resultados preparados.

## Persistencia

`pokedex.db` es la única fuente de verdad mutable. Incluye capturas, caché de
especies, inventario, actividad, commits procesados, encuentro actual y
evoluciones pendientes. Cada conexión activa WAL, claves foráneas y un
`busy_timeout`. Las operaciones read-modify-write usan transacciones explícitas
y las migraciones numeradas se registran en `schema_migrations`.

Los antiguos `inventory.json` y `last_seen.json` se importan una vez de forma
idempotente mediante `legacy_imports`; después no se usan como estado activo.

## Compatibilidad deliberada

Los módulos históricos de la raíz (`cli`, `display`, `animation`, `inventory`,
`paths`, `storage`, `pokeapi`, `progression`, `capture` y `krabby_bridge`) son
aliases o exportadores finos. Se conservan como API compatible para scripts y
tests existentes; no contienen una segunda implementación ni estado paralelo.
Esta compatibilidad es soportada, no una migración pendiente.

## Límites externos

- PokeAPI usa sesión inyectable, timeout, errores tipados, un reintento acotado
  para fallos transitorios y fallback offline mediante caché SQLite.
- Git limita recorrido y duración, reconoce detached HEAD/merges/binarios y
  filtra por correos configurados y fecha local del autor.
- Krabby valida ANSI, impone timeout y degrada a un encuentro visual básico sin
  impedir que aparezca el prompt.
- `POKEDEX_DIAGNOSTIC_LOG` activa trazas a archivo; sin esa variable no se
  escribe ningún log diagnóstico.

## Verificación

La suite bloquea red real y usa `HOME`/XDG/SQLite temporales. CI separa tests
normales, instalación/estrés, calidad y mutación. La cobertura excluye solo
renderizado cosmético Rich/ANSI y aliases; la CLI real y los límites externos
siguen dentro del gate.
