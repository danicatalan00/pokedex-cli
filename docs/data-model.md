# Modelo de datos

El archivo `${XDG_DATA_HOME:-$HOME/.local/share}/pokedex-cli/pokedex.db` es la
única fuente de verdad mutable.

| Área | Tablas | Invariantes principales |
|---|---|---|
| Colección | `captures`, `species_cache` | Identidad canónica; equipo máximo de seis |
| Encuentro | `encounter_state` | Una sola criatura activa; intentos no negativos |
| Inventario | `inventory_balls` | Stock no negativo y máximos por bola |
| Actividad | `activity_state`, `processed_commits` | Un estado global; cada commit se procesa una vez |
| Evolución | columnas pendientes en `captures` | Conserva captura, nivel, EXP, shiny y equipo |
| Esquema | `schema_migrations`, `legacy_imports` | Migraciones e importaciones idempotentes |

## Política de persistencia

- Las conexiones activan WAL, claves foráneas y espera acotada ante bloqueos.
- Consumo, captura y recompensas se confirman o revierten como una unidad.
- Restricciones y triggers sostienen invariantes incluso bajo concurrencia.
- `species_cache` permite continuar offline; no es una segunda fuente de verdad
  para el estado del juego.

`inventory.json` y `last_seen.json` solo son entradas históricas. Se importan
una vez y se conservan para recuperación, no para escrituras activas.

Para copiar o restaurar la base consulta [operación y recuperación](operations.md).
