# Infraestructura

Los adaptadores implementan efectos locales y externos sin filtrar sus detalles
al dominio.

| Adaptador | Responsabilidad | Política de fallo |
|---|---|---|
| SQLite | Estado, migraciones y transacciones | WAL, FK, timeout y rollback |
| PokeAPI | Especies, stats y progresión | Timeout, errores tipados, reintento acotado y caché |
| Git | Actividad del usuario | Recorrido limitado, deduplicación y coste acotado |
| Krabby | Selección y sprite | Validación ANSI, timeout y fallback visual |
| Diagnóstico | Evidencia opt-in | Sin escritura ni traceback por defecto |

## Fronteras

- Los transportes HTTP son inyectables; la suite normal no usa red.
- El escáner Git ignora dependencias, cachés, binarios y repositorios no
  accesibles; usa identidad y fechas locales explícitas.
- Krabby puede faltar o entregar datos incompletos sin impedir el prompt.
- Las rutas se resuelven desde `HOME`/XDG en composición, nunca en reglas puras.

Para decisiones de dependencias consulta [arquitectura](architecture.md); para
tablas e invariantes, [modelo de datos](data-model.md).
