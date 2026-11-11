# Modelo de datos

El archivo `${XDG_DATA_HOME:-$HOME/.local/share}/pokedex-cli/pokedex.db` es la
única fuente de verdad mutable.

| Área | Tablas | Invariantes principales |
|---|---|---|
| Colección | `captures`, `species_cache` | Identidad canónica; equipo máximo de seis |
| Encuentro | `encounter_state` | Una sola criatura activa; intentos no negativos; escalón de captura acotado |
| Inventario | `inventory_balls` | Stock no negativo y máximos por bola |
| Actividad | `activity_state`, `processed_commits` | Un estado global; cada commit se procesa una vez |
| Evolución | columnas pendientes en `captures` | Conserva captura, nivel, EXP, shiny y equipo |
| Movimientos | `move_cache`, `capture_moves`, learnset en `species_cache` | Solo registros que el dominio acepta; moveset propio de 1 a 4 movimientos distintos |
| Combate | `battle_state`, `battle_record` | Como mucho un combate guardado; récord con contadores no negativos |
| Esquema | `schema_migrations`, `legacy_imports` | Migraciones e importaciones idempotentes |

## Política de persistencia

- Las conexiones activan WAL, claves foráneas y espera acotada ante bloqueos.
- Consumo, captura y recompensas se confirman o revierten como una unidad.
- Restricciones y triggers sostienen invariantes incluso bajo concurrencia.
- `species_cache` permite continuar offline; no es una segunda fuente de verdad
  para el estado del juego.
- Los stats son los valores base compartidos por especie y forma. Cada captura
  mantiene identidad propia mediante ID, nivel, EXP, shiny, fecha y Pokéball;
  pueden coexistir duplicados para formas y evoluciones ramificadas.
- El estado de Zona Safari vive en el encuentro: `catch_stage` multiplica la
  probabilidad de captura y `item_turns` cuenta los turnos gastados en rocas y
  cebos. Ambos comparten la paciencia (`escape_after_attempts`) con los
  lanzamientos fallidos, así que huir es una sola regla y una sola transacción.
- Preparar un combate (equipo, learnsets y movimientos, quizá descargándolos)
  ocurre fuera de transacción. Empezarlo y cada turno son una transacción
  `BEGIN IMMEDIATE` que revalida el combate y su encuentro.
- Un turno aplica el motor o la captura (que gasta bola y paciencia como
  `pokedex capturar`) y lo guarda todo junto: la EXP ganada se suma a la que
  la captura tiene ahora en la base, así que no pisa la de los commits; nivel y
  EXP nunca bajan y las evoluciones se reencolan. Al terminar se borra el
  combate, se anota el récord y, si el salvaje no se capturó, se borra el
  encuentro. Una orden inválida no gasta turno ni escribe nada.

`inventory.json` y `last_seen.json` solo son entradas históricas. Se importan
una vez y se conservan para recuperación, no para escrituras activas.

## Modo combate

Las migraciones 13 y 14 añaden estas columnas y tablas:

- `species_cache.level_moves` guarda el learnset por nivel en JSON
  (`[["tackle", 1], …]`, ordenado por nivel) del grupo de versión
  `moves_version_group`. `NULL` marca un perfil anterior al combate, que se
  refresca una vez al necesitarlo; `[]`, uno ya consultado sin learnset. Un
  perfil que no trae esas claves conserva el learnset guardado.
- `move_cache` guarda por `slug` el registro JSON con los campos del `Move`
  del dominio y su `fetched_at`. Solo entra lo que el dominio acepta para ese
  slug; un registro ilegible cuenta como ausente y se vuelve a pedir.
- `capture_moves` es el moveset elegido por el jugador: una fila por hueco
  (`slot` de 0 a 3), clave `(capture_id, slot)` y borrado en cascada con la
  captura. Sin filas, la captura lucha con el moveset automático, que se
  recalcula al subir de nivel. Se sustituye entero en una transacción.
- `battle_state` tiene como mucho una fila (`singleton = 1`). `kind` es `wild`
  o `trainer`: las exhibiciones viven en memoria. `state_json` envuelve el
  estado del codec del dominio con su versión y los datos de captura del
  salvaje (`{"v": 1, "state": …, "context": …}`). Una fila ilegible, terminada
  o incoherente se borra al leerla.
- `battle_record` es una fila de contadores. Cada combate salvaje terminado
  suma uno a `wild_battles` y, según acabe, a `wild_wins` o `wild_captures`.
  Contra un entrenador, ganar alarga `streak` (y `best_streak`), perder la
  pone a cero y las tablas solo actualizan `last_battle_at`.

El combate salvaje está ligado a su encuentro por `encounter_seen_at`, el
`seen_at` de `encounter_state`. Mientras siga vigente, el hook no saca otro
Pokémon y `capturar`, `roca` y `cebo` se niegan. Si el encuentro cambia,
desaparece o se captura por otra vía, el combate queda obsoleto y se borra al
leerlo, así que nunca bloquea encuentros nuevos. El salvaje sale siempre igual
de la semilla del encuentro y al nivel que usa la captura; capturarlo en
combate inserta ese mismo individuo.

Para copiar o restaurar la base consulta [operación y recuperación](operations.md).
