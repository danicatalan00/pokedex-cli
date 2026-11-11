# Infraestructura

Los adaptadores implementan efectos locales y externos sin filtrar sus detalles
al dominio.

| Adaptador | Responsabilidad | Política de fallo |
|---|---|---|
| SQLite | Estado, migraciones y transacciones | WAL, FK, timeout y rollback |
| PokeAPI | Especies, stats, progresión, movimientos y learnsets | Timeout, errores tipados, reintento acotado y caché |
| Git | Actividad del usuario | Recorrido limitado, deduplicación y coste acotado |
| Krabby | Selección y sprite | Validación ANSI, timeout y fallback visual |
| Diagnóstico | Evidencia opt-in | Sin escritura ni traceback por defecto |

## Fronteras

- Los transportes HTTP son inyectables; la suite normal no usa red.
- El escáner Git ignora dependencias, cachés, binarios y repositorios no
  accesibles; usa identidad y fechas locales explícitas.
- La raíz del escáner es `HOME` o `POKEDEX_REPOS_ROOT`; Git se consulta como
  mucho una vez por hora.
- Krabby puede faltar o entregar datos incompletos sin impedir el prompt. En
  combate, cada sprite se pide una vez por combate y, si falta, la escena
  pinta un sustituto.
- Las rutas se resuelven desde `HOME`/XDG en composición, nunca en reglas puras.

## Movimientos y learnsets

- El learnset por nivel sale del mismo `/pokemon` que ya descarga el perfil de
  especie. Se recorre una lista de grupos de versión «clásica primero»
  (Esmeralda, Rojo Fuego y Verde Hoja, Rubí y Zafiro, Platino…, con Gen 1,
  Gen 2 y Let's Go al final) y gana el primero con al menos cuatro movimientos
  distintos; si ninguno llega, el que más tiene. Solo una forma alternativa
  sin movimientos por nivel en PokeAPI, como las Gigamax, pide además la
  variedad por defecto.
- Cada movimiento sale de `/move/{slug}` como un registro con los campos del
  `Move` del dominio (nombre y descripción en español si existen) y se guarda
  en `move_cache`.
- `GetMoveData` busca en una sola consulta todos los movimientos del combate y
  descarga en paralelo solo los que faltan: hasta ocho hilos, cada uno con su
  cliente HTTP.
- Sin conexión, `TolerantPokeApiClient` devuelve `None` y anota el fallo en el
  diagnóstico opt-in. El combate sigue con lo cacheado; un Pokémon sin datos
  de ningún movimiento lucha con los de reserva de su tipo y Placaje. Un fallo
  no se cachea: se reintenta en el siguiente combate.

Para decisiones de dependencias consulta [arquitectura](architecture.md); para
tablas e invariantes, [modelo de datos](data-model.md).
