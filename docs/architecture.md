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
intento y persiste el resultado dentro de una única transacción. La presentación
solo traduce entrada/salida.

## Decisiones que protegen el diseño

- El dominio recibe reloj, azar y datos como dependencias explícitas.
- SQLite es la única fuente de verdad mutable.
- Los efectos externos se concentran en adaptadores con timeout y fallback.
- Los módulos históricos de la raíz son fachadas compatibles deliberadas, no
  una segunda implementación.
- El hook trata el prompt como frontera crítica: un fallo recuperable devuelve
  control sin traceback.

Consulta el [modelo de datos](data-model.md) para persistencia y la
[infraestructura](infrastructure.md) para políticas de adaptadores.
