# Goal: endurecer y refactorizar `pokedex-cli` con TDD estricto

## Objetivo

Convertir `pokedex-cli` de un proyecto experimental en una aplicación local
fiable y mantenible, conservando su comportamiento y personalidad actuales.
El refactor debe reducir acoplamiento, eliminar condiciones de carrera,
proteger la integridad del estado y permitir ampliar reglas y comandos sin
incrementar desproporcionadamente el riesgo de regresiones.

El trabajo se hará incrementalmente, con TDD estricto y sin una reescritura
total. Cada fase debe dejar el proyecto en un estado funcional, probado y
entregable aunque las fases posteriores no lleguen a ejecutarse.

## Contexto y línea base

Estado observado el 15 de julio de 2026:

- La suite actual contiene 34 tests y pasa completamente en unos 0,18 s.
- El proyecto tiene aproximadamente 3.575 líneas entre implementación y tests.
- `pokedex_cli/cli.py` tiene 809 líneas y mezcla parsing, entrada interactiva,
  presentación, acceso a SQLite, red, RNG y coordinación de casos de uso.
- `pokedex_cli/inventory.py` mezcla reglas de negocio, JSON, escaneo de `$HOME`,
  ejecución de Git y cálculo de recompensas.
- No hay tests directos para `paths.py`, `pokeapi.py` ni `krabby_bridge.py`.
- El estado mutable está dividido entre SQLite, `inventory.json` y
  `last_seen.json`, sin una transacción común.
- Rutas, reloj, RNG, red, terminal y entorno global están fuertemente acoplados.
- No hay `pyproject.toml`, configuración de cobertura, linting, tipos o CI.
- Algunos tests dejan escapar texto por consola y no están completamente
  aislados.

### Bug crítico ya diagnosticado

`paths._atomic_write_json()` utiliza siempre el mismo archivo temporal:

```python
tmp_path = path.with_suffix(path.suffix + ".tmp")
tmp_path.write_text(json.dumps(data))
os.replace(tmp_path, path)
```

Dos hooks de Zsh concurrentes pueden escribir y renombrar el mismo temporal.
Esto produce `FileNotFoundError` y también permite actualizaciones perdidas.
La solución debe proteger toda la operación read-modify-write, no limitarse a
generar nombres temporales únicos.

## Principios de ejecución

1. No hacer una reescritura completa.
2. Congelar primero el comportamiento observable mediante tests de
   caracterización.
3. Para cada cambio: rojo, verde y refactor.
4. No implementar producción sin un test que falle por la razón esperada.
5. No mezclar cambios funcionales y reorganizaciones grandes en el mismo commit.
6. Mantener la suite verde al terminar cada paso y cada commit.
7. Evitar mocks de detalles internos cuando pueda probarse un límite real con
   almacenamiento o procesos temporales.
8. Ningún test ordinario puede depender de Internet, del `$HOME` real ni de la
   base de datos real del usuario.
9. Preservar datos existentes mediante migraciones e importaciones idempotentes.
10. Favorecer funciones puras y dependencias explícitas frente a estado global.
11. No perseguir cobertura cosmética de código visual; priorizar invariantes,
    ramas de negocio y modos de fallo.

## Arquitectura objetivo

Usar cuatro capas ligeras, sin introducir un framework de arquitectura:

```text
pokedex_cli/
├── domain/
│   ├── models.py          # Pokemon, Encounter, Ball, Inventory...
│   ├── capture.py         # Probabilidad y reglas de captura
│   ├── progression.py     # EXP, niveles y evolución
│   └── rewards.py         # Stock y recompensas
├── application/
│   ├── hook.py            # Caso de uso: abrir terminal
│   ├── capture.py         # Caso de uso: capturar
│   ├── activity.py        # Sincronización de commits
│   └── team.py            # Gestión del equipo
├── infrastructure/
│   ├── database.py        # Conexión, transacciones y migraciones
│   ├── repositories.py
│   ├── pokeapi.py
│   ├── git_activity.py
│   └── krabby.py
└── presentation/
    ├── cli.py             # Parser y despacho, sin reglas de negocio
    ├── display.py
    └── animation.py
```

Las reglas de dominio deben ser funciones puras siempre que sea posible. Los
casos de uso coordinan repositorios y servicios. Los detalles externos quedan
detrás de interfaces pequeñas.

Dependencias que deben poder inyectarse:

- `Clock`
- `RandomSource`
- `PokemonRepository`
- `InventoryRepository`
- `EncounterRepository`
- `PokeApiClient`
- `GitActivitySource`
- `SpriteRenderer`

No es necesario crear todas las abstracciones al principio. Deben extraerse en
el momento en que un caso de uso se migre y sus tests demuestren su utilidad.

## Decisión de persistencia

Unificar progresivamente en SQLite todo el estado mutable:

- capturas;
- inventario y stock;
- encuentros, en sustitución de `last_seen.json`;
- actividad y última sincronización;
- commits procesados;
- evoluciones pendientes.

Mantener una importación automática, segura e idempotente de
`inventory.json` y `last_seen.json`. Los archivos de configuración y cachés
grandes pueden permanecer fuera de SQLite.

Configurar y probar explícitamente:

- `PRAGMA journal_mode=WAL`;
- `PRAGMA foreign_keys=ON`;
- `busy_timeout`;
- transacciones explícitas para operaciones read-modify-write;
- restricciones para invariantes importantes;
- migraciones numeradas y registradas en vez de una única función `_migrate()`
  creciente.

## Suite de tests objetivo

### 1. Tests de caracterización

Añadir antes de mover responsabilidades:

- comandos, argumentos, valores por defecto y códigos de salida;
- aliases de Pokéballs;
- captura exitosa, fallo, fuga y captura repetida;
- consumo y ausencia de stock;
- equipo completo y selección interactiva;
- evoluciones pendientes antes del encuentro salvaje;
- fallback cuando PokeAPI o Krabby no están disponibles;
- lectura de datos y bases antiguas;
- salida funcional importante sin acoplarse a todo el ANSI.

### 2. Tests unitarios de dominio

Cubrir exhaustivamente:

- tasas de captura `None`, 0, 1, 255 y entradas inválidas;
- multiplicadores, Masterball, límites y redondeos;
- stock infinito, máximos y consumo;
- recompensas antes, durante y después de cada umbral;
- curvas de experiencia en niveles 1, 2, 99 y 100;
- dificultad por diff y límite de 50x;
- evoluciones lineales, ramificadas y no basadas en nivel;
- fechas, zonas horarias, DST y límites del horario laboral;
- normalización de formas, nombres y slugs.

Usar Hypothesis para invariantes:

- la probabilidad siempre está entre 0 y 1;
- el stock nunca es negativo ni supera su máximo;
- el nivel nunca decrece y permanece entre 1 y 100;
- serializar y deserializar conserva el modelo;
- ejecutar una migración dos veces mantiene el mismo resultado.

### 3. Persistencia, migraciones y concurrencia

Usar bases SQLite temporales reales:

- creación desde cero;
- migración desde cada esquema histórico soportado;
- migraciones e importaciones idempotentes;
- importación de los JSON actuales;
- JSON ausente, truncado, inválido o con campos desconocidos;
- rollback si una operación falla a mitad;
- dos capturas concurrentes;
- al menos veinte hooks concurrentes;
- sincronizaciones concurrentes sin premios duplicados;
- consumo concurrente de la última bola disponible;
- invariantes de equipo bajo concurrencia;
- recuperación ante `database is locked` dentro de la política configurada.

El primer test nuevo debe reproducir de forma determinista el fallo de
`inventory.json.tmp` observado durante el arranque de WSL.

### 4. Contratos de adaptadores

- PokeAPI: éxito, 404, 429, 500, timeout, conexión fallida y JSON incompleto.
- Krabby: disponible, ausente, caché ausente, forma desconocida y ANSI inválido.
- Git: repositorio vacío, detached HEAD, merge commit, binarios, correo
  desconocido, fechas límite y directorios sin permisos.
- Terminal: TTY y no TTY, entrada cancelada e interrupción.
- Reloj: UTC, zona local y cambio DST.

El cliente HTTP debe aceptar una sesión/transporte, usar timeout explícito y no
hacer red real en la suite normal.

### 5. Tests CLI end-to-end

Ejecutar realmente `python -m pokedex_cli` con `HOME`, `XDG_DATA_HOME`, base y
repositorios temporales:

- `--help` y ayuda de cada subcomando;
- primera ejecución;
- hook con y sin Krabby;
- captura offline exitosa y fallida;
- bolsas, listado, búsqueda, visión y equipo;
- evolución pendiente;
- base de datos antigua;
- datos corruptos;
- comando o argumentos desconocidos;
- terminal no interactiva;
- salida limpia y código de retorno estable ante fallo recuperable.

Usar snapshots solo para componentes visuales estables y pequeños. No convertir
toda la salida ANSI en snapshots difíciles de mantener.

### 6. Instalación, completado y startup

- `install.sh` es idempotente;
- funciona en un `HOME` temporal;
- no duplica bloques de `.zshrc`;
- conserva contenido preexistente;
- el shim encuentra correctamente el entorno y el proyecto;
- informa de forma controlada si el entorno desapareció;
- el completado es válido y cargable mediante `compinit`;
- abrir varios Zsh simultáneamente no produce traceback;
- el hook nunca impide obtener un prompt.

### 7. Animación y presentación

Separar la generación de frames de su reproducción y probar:

- dimensiones y número de frames;
- frame final;
- diferencias relevantes entre tipos de bola;
- fallback sin sprite;
- evolución entre sprites de tamaños diferentes;
- ausencia de acceso a red o almacenamiento desde presentación.

## Plan por fases

### Fase 0 — Línea base de ingeniería

- Crear `pyproject.toml` con metadatos y dependencias reproducibles.
- Adoptar `pytest`, `pytest-cov`, Hypothesis, Ruff y mypy.
- Crear fixtures para reloj, RNG, HOME/XDG, base, repositorios Git y consola.
- Capturar toda salida durante tests salvo cuando sea parte de la aserción.
- Bloquear llamadas de red no declaradas.
- Añadir CI para las versiones de Python soportadas, inicialmente 3.11–3.13.
- Mantener los 34 tests actuales verdes.

Criterio de salida: instalación y suite reproducibles con comandos documentados;
los tests no modifican datos reales ni ensucian el árbol Git.

### Fase 1 — Bugs críticos de startup y concurrencia

Orden TDD obligatorio:

1. Crear un test rojo que reproduzca la colisión de `inventory.json.tmp`.
2. Aplicar la solución mínima para evitar la colisión del temporal.
3. Crear un test rojo que demuestre una actualización perdida.
4. Proteger toda la operación mediante bloqueo o transacción.
5. Crear tests de múltiples hooks y sincronizaciones simultáneas.
6. Garantizar que un fallo recuperable del hook no imprime traceback ni bloquea
   el arranque de Zsh.

Criterio de salida: ninguna ejecución concurrente corrompe estado, pierde
actualizaciones o impide abrir la terminal.

### Fase 2 — Extraer dominio puro

- Introducir modelos tipados y enums donde reemplacen diccionarios ambiguos.
- Extraer reglas de captura, inventario, recompensas y progresión.
- Inyectar reloj y RNG.
- Mantener adaptadores temporales para las llamadas antiguas.
- Migrar una regla o caso de uso por vez.

Criterio de salida: las reglas principales no dependen de SQLite, archivos,
Rich, red, Git ni variables de entorno.

### Fase 3 — Fuente única de verdad en SQLite

- Crear infraestructura de conexión y migraciones versionadas.
- Añadir tablas de inventario, encuentros y actividad.
- Añadir importadores idempotentes de los JSON existentes.
- Hacer atómicos consumo de bola, intento de captura y recompensa.
- Deduplicar commits mediante una restricción o clave única.
- Conservar temporalmente compatibilidad con datos antiguos.

Criterio de salida: una interrupción o varias terminales concurrentes no pueden
duplicar premios, perder stock ni dejar estado parcial.

### Fase 4 — Extraer casos de uso

Extraer de `cli.py` al menos:

- `OpenTerminal`;
- `CaptureEncounter`;
- `SyncActivity`;
- `ManageTeam`;
- `ProcessEvolutions`.

La capa CLI solo debe validar/traducir argumentos, invocar un caso de uso y
renderizar el resultado. No debe abrir conexiones, llamar directamente a
PokeAPI ni calcular reglas.

Criterio de salida: cada caso de uso se prueba sin terminal real y `cli.py` deja
de ser el centro de la lógica de negocio.

### Fase 5 — Endurecer integraciones

- Cliente PokeAPI con timeout, errores tipados, validación y caché.
- Scanner Git limitado, inyectable y observable.
- Adaptador Krabby tolerante a ausencia y datos inesperados.
- Política explícita de reintento y fallback.
- Logging diagnóstico opcional a archivo; nunca traceback espontáneo durante el
  startup normal.
- Medición del coste del hook y del escaneo Git.

Criterio de salida: cada integración puede fallar sin comprometer datos ni el
prompt, y su comportamiento está cubierto por tests de contrato.

### Fase 6 — Presentación y animaciones

- Separar generación y reproducción de frames.
- Eliminar consultas y escrituras desde presentación.
- Reducir snapshots frágiles.
- Mantener la experiencia visual existente mediante caracterización.

Criterio de salida: presentación recibe view models/resultados preparados y no
contiene reglas ni efectos persistentes.

### Fase 7 — Consolidación

- Eliminar adaptadores y compatibilidad transitoria cuando ya no sean necesarios.
- Dividir módulos grandes restantes.
- Actualizar README, instalación y documentación de arquitectura.
- Documentar backup, recuperación y diagnóstico.
- Añadir mutation testing para captura, inventario, recompensas y progresión.
- Revisar rendimiento y complejidad del escaneo de repositorios.

## Flujo TDD obligatorio por cambio

1. Elegir una única conducta o riesgo.
2. Escribir el test más pequeño que lo exprese.
3. Ejecutarlo y confirmar que falla por la razón correcta.
4. Implementar lo mínimo para hacerlo pasar.
5. Ejecutar la suite rápida completa.
6. Refactorizar nombres, estructura y duplicación con la suite verde.
7. Ejecutar integración y end-to-end cuando el límite afectado lo requiera.
8. Hacer un commit pequeño con intención única.

Si una modificación no puede empezar con un test rojo, documentar explícitamente
por qué es un cambio puramente mecánico y demostrar equivalencia con la suite
antes y después.

## Objetivos de calidad

- 100% de cobertura de ramas en reglas puras críticas.
- 90–95% de cobertura en casos de uso.
- Más de 85% de cobertura global, sin tests cosméticos para Rich/ANSI.
- Mutation testing satisfactorio en captura, inventario, recompensas y
  progresión.
- Suite unitaria por debajo de 2 s en desarrollo.
- Suite completa por debajo de 30 s en CI, salvo jobs explícitos de estrés.
- Ruff sin errores y formato estable.
- mypy estricto al menos en `domain` y `application`, ampliándolo gradualmente.
- Cero acceso a datos reales del usuario durante tests.
- Cero red real en la suite normal.
- Cero traceback del hook durante un arranque normal o degradado.

La cobertura es una señal, no el objetivo final. Los criterios principales son
invariantes verificadas, fallos reproducibles, transacciones correctas y límites
externos bien probados.

## Estrategia de commits

Mantener commits pequeños y reversibles. Secuencia orientativa:

1. tooling y línea base;
2. fixtures y aislamiento;
3. caracterización del hook e inventario;
4. reproducción del bug concurrente;
5. corrección mínima del bug;
6. dominio puro por verticales;
7. migraciones SQLite e importación;
8. casos de uso uno por uno;
9. adaptadores externos;
10. presentación, documentación y limpieza.

No realizar un movimiento masivo de archivos antes de tener caracterización y
tests de las fronteras implicadas.

## Definition of Done global

El goal se considera completado cuando:

- todo el comportamiento deliberadamente conservado está cubierto;
- inventario, encuentros y actividad usan persistencia transaccional;
- los JSON existentes se migran sin pérdida y de forma idempotente;
- los escenarios concurrentes pasan repetidamente;
- el hook nunca rompe ni ensucia el startup con un traceback recuperable;
- dominio, aplicación, infraestructura y presentación tienen límites claros;
- CLI y animaciones no contienen reglas de persistencia o red;
- PokeAPI, Git y Krabby tienen contratos y políticas de fallo probadas;
- instalación, completado y startup están cubiertos en entornos temporales;
- lint, tipos, unitarios, integración y end-to-end pasan en CI;
- README y documentación reflejan el diseño final;
- no quedan compatibilidades transitorias sin una decisión documentada;
- el rendimiento del hook es aceptable y está medido;
- el proyecto puede ampliarse sin depender de parchear estado global.

## Orden de prioridad

1. Concurrencia e integridad de persistencia.
2. Caracterización de la CLI y del hook.
3. Aislamiento de reglas de dominio.
4. Fuente única de verdad en SQLite.
5. Casos de uso y reducción de `cli.py`.
6. Robustez de PokeAPI, Git y Krabby.
7. Presentación, instalación, documentación y optimización.

Este orden es deliberado: cada fase reduce riesgo inmediatamente y permite
detener el refactor temporalmente sin dejar el programa peor que al comenzar.
