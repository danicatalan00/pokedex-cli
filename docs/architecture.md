# Arquitectura

Pokédex CLI es una aplicación local en capas, con una raíz de composición y sin
framework arquitectónico.

```text
presentation ──→ application ──→ domain
      │                ↑
      └──→ composition ┴──→ infrastructure
```

- `domain`: modelos y reglas puras; no conoce terminal, red ni persistencia.
- `application`: casos de uso y puertos pequeños; coordina transacciones y
  decisiones del dominio.
- `infrastructure`: SQLite, PokeAPI, Git, Krabby, rutas y diagnóstico.
- `presentation`: argumentos, interacción y renderizado de resultados.
- `composition.py`: único lugar que selecciona adaptadores concretos.

## Flujo principal

El hook abre un caso de uso que procesa actividad y evoluciones antes de pedir
un encuentro al adaptador de Krabby. Capturar consume inventario, resuelve el
intento y persiste el resultado dentro de una única transacción. Las acciones de
Zona Safari (roca y cebo) son turnos del mismo encuentro: gastan la misma
paciencia y se resuelven en su propia transacción atómica. La presentación solo
traduce entrada/salida.

## Flujo del combate

`domain/battle` es un motor puro de reglas de Gen 3: `model` fija estado,
acciones y eventos; el resto (mecánicas, motor, IA, movesets, entrenadores y
codec) son funciones puras sobre ese contrato, con el azar inyectado.

1. `PrepareBattle` monta los combatientes fuera de transacción: equipo,
   perfiles con learnset y todos los movimientos del combate de una vez. Es el
   único paso del combate que puede usar la red, y cuenta su progreso a la
   interfaz.
2. `StartBattle` y cada `TakeBattleTurn` son una transacción `BEGIN IMMEDIATE`:
   revalidan el combate y su encuentro, aplican el motor (o la captura) y
   guardan a la vez el estado y lo que cambie con él: EXP, evoluciones, récord
   y encuentro.
3. `PersistedSession` (combate guardado) y `ExhibitionSession` (en memoria)
   comparten la interfaz `take(orden) → TurnOutcome`: la CLI y la TUI juegan
   igual cualquiera de los dos.
4. La presentación pura es común: `battle_text` narra cada evento y
   `battle_director` convierte los eventos de un turno en fotogramas (`Beat`)
   de la escena de `battle_scene`. `battle_cli` los reproduce con Rich a
   pantalla completa (sin TTY narra solo el texto) y `tui/battle`, con
   temporizadores de Textual.

Las interfaces del combate se importan al usarlas, así que el hook no paga su
coste. Con un combate salvaje en pausa, el hook lo recuerda y no pide otro
encuentro; si no puede consultarlo, sigue como siempre.

## Decisiones que protegen el diseño

- El dominio recibe reloj, azar y datos como dependencias explícitas.
- SQLite es la única fuente de verdad mutable.
- Los efectos externos se concentran en adaptadores con timeout y fallback.
- Los módulos históricos de la raíz son fachadas compatibles deliberadas, no
  una segunda implementación.
- El hook trata el prompt como frontera crítica: un fallo recuperable devuelve
  control sin traceback.

Consulta el [modelo de datos](data-model.md) para persistencia (también la del
[modo combate](data-model.md#modo-combate)) y la
[infraestructura](infrastructure.md) para políticas de adaptadores.
