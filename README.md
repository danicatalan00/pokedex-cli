# pokedex-cli

Cada terminal nueva pinta un Pokémon al azar (vía [`krabby`](https://github.com/yannjor/krabby)) sin decir cuál es. Si te interesa, lo capturas con un comando y queda guardado en una base de datos SQLite local, con tipos/stats/legendario enriquecidos desde [PokeAPI](https://pokeapi.co).

Nace de `chat-pokedex-cli.txt`, una conversación de chat donde surgió la idea.

## Cómo funciona

- `~/.zshrc` ya no llama a `krabby random` directamente: llama a `pokedex hook 1-3`, que:
  1. Elige especie + forma (incluye mega/gmax/regional, replicando el algoritmo real de `krabby random` a partir de una copia local de su base de datos) y tira shiny con el mismo `shiny_rate` de `~/.config/krabby/config.toml`.
  2. Pinta el sprite igual que antes (mismo aspecto, sigue sin revelar el nombre).
  3. Guarda en silencio cuál fue en `~/.local/share/pokedex-cli/last_seen.json` (solo se recuerda el último).
  - Si algo falla (`krabby` desinstalado, caché de datos ausente, etc.), cae de vuelta al `krabby random` de siempre — la terminal nunca se rompe.
- `pokedex capturar` intenta guardar ese último Pokémon (con su forma y si es shiny) en SQLite, con una animación de pokeball que se lanza **sobre el sprite real** del Pokémon (lo absorbe, bambolea y hace click), y lo enriquece con PokeAPI si hay red. No captura nada automáticamente: hay que pedirlo.
  - **La captura no está garantizada.** Se tira el dado según el `capture_rate` real del Pokémon y la bola elegida. Si se suelta, puede seguir esperando unos intentos más o huir definitivamente.
  - La Pokeball normal (1×) es infinita. Superball (1,5×), Ultraball (2×) y Masterball (captura garantizada) tienen stock y se gastan al lanzarlas.
  - Cada modelo tiene su propia animación: patrón, color, estela, impacto, absorción y cierre crecen en intensidad con la calidad de la bola.
  - Al capturar se muestra su **N.º de Pokédex oficial** (p.ej. `#257`) además del orden de captura interno (`captura #1`).
  - La bola usada queda guardada con la captura y aparece en `pokedex vision`.

## Comandos

| Comando | Qué hace |
|---|---|
| `pokedex ver` | Muestra qué Pokémon está esperando, sin capturarlo |
| `pokedex capturar [-b bola] [--debug]` | Elige una bola e intenta capturar; `--debug` muestra la probabilidad |
| `pokedex bolsas [--info]` | Muestra stock y progreso; `--info` añade reglas y diagnóstico |
| `pokedex list` | Lista tus capturas |
| `pokedex search <nombre> [-f forma]` | Ficha de cualquier Pokémon/forma (ej. `pokedex search charizard -f mega-x`) |
| `pokedex vision <id>` | Vista enriquecida de una captura, incluida la bola utilizada |
| `pokedex equipo` | Muestra tu equipo (hasta 6) |
| `pokedex equipo add [id]` / `remove <id>` | Añade con selector (↑/↓ y Enter) o por id; quita por id |
| `pokedex demo-evolucion [origen] [destino]` | Prueba una evolución sin guardar nada |
| `pokedex tipos` | Desglose de tus capturas por tipo |
| `pokedex ranking` | Ranking por suma de stats base, con medallas |
| `pokedex legendarios` | Salón de la fama de legendarios/singulares capturados |
| `pokedex demo [nombre]` | Prueba la animación de captura **sin guardar nada** (Pokémon al azar si no se indica) |
| `pokedex completion zsh` | Imprime el script de autocompletado para zsh |

### Cómo crecen las Pokeballs

No hay tienda ni moneda. La Pokeball normal siempre está disponible y las Pokeballs especiales crecen con actividad local:

- El taller fabrica 1 Superball cada 24 horas, hasta un máximo de 10.
- Cada 3 commits laborales registrados concede 1 Superball; cada 10, 1 Ultraball; cada 50, 1 Masterball.
- Se consideran commits propios (según `git config user.email`) cuya fecha de autor sea de lunes a viernes entre las 08:00 y las 19:00. Se buscan automáticamente repositorios Git bajo `HOME`; se omiten cachés, dependencias y entornos virtuales.
- La primera sincronización marca el punto de partida: no importa todo el historial ni infla la bolsa. Las recompensas se actualizan al ejecutar `pokedex bolsas` o antes de una captura.

El stock inicial es 3 Superballs, 1 Ultraball y ninguna Masterball. Los máximos son 10, 5 y 1 respectivamente. El sistema se basa deliberadamente en confianza local, como el resto de los datos de la Pokédex.

### Niveles, experiencia y evolución

- Todos los Pokémon se capturan en el nivel 5. Solo entrenan los que están en el equipo.
- Cada commit laboral nuevo se asigna al azar a uno de ellos. Si quieres entrenar uno concreto, deja solo ese en el equipo.
- Un commit simula un combate individual de primera generación contra un rival equivalente: `EXP base = experiencia base × nivel / 7`. Su dificultad crece con el diff: `× min(50, 1 + (líneas añadidas + borradas) / 50)`. Los binarios no cuentan líneas y el multiplicador queda limitado a 50×.
- El nivel usa la curva real de crecimiento de la especie publicada por PokeAPI, con límite 100.
- Al alcanzar una evolución disparada por nivel queda pendiente. En la siguiente terminal, en lugar del Pokémon salvaje, aparece la secuencia de evolución. Si varios estaban pendientes, evolucionan consecutivamente en esa misma apertura. Conservan id, shiny, nivel, experiencia y puesto en el equipo.
- Las evoluciones por piedra, intercambio, amistad u otra condición no se convierten artificialmente en evoluciones por nivel. Si hay ramas de nivel simultáneas, el resultado se elige al azar.

### Probar solo la animación

```
pokedex demo               # Pokémon al azar, resultado al azar
pokedex demo -L            # contra un legendario/singular al azar
pokedex demo pikachu -r catch   # Pokémon y resultado concretos
pokedex demo -r escape -s   # fuerza fuga, variante shiny
pokedex demo mewtwo -b master -r catch  # prueba la animación de Masterball
pokedex demo-evolucion bulbasaur ivysaur       # ritmo normal
pokedex demo-evolucion charmander charmeleon --speed 0.7  # más rápida
pokedex demo-evolucion gastly haunter --speed 1.4          # más suspense
pokedex demo-evolucion magikarp gyarados -s                # shiny
```

`demo` no toca la base de datos ni el Pokémon que está esperando: es seguro para trastear.

### Autocompletado zsh

`install.sh` lo deja listo (copia el script a `~/.zfunc/_pokedex` y añade `~/.zfunc` al `fpath` en `~/.zshrc`). Para instalarlo a mano:

```
pokedex completion zsh > ~/.zfunc/_pokedex
# y en ~/.zshrc, antes de compinit:  fpath=(~/.zfunc $fpath)
```

Completa subcomandos, formas, resultados de `demo`, nombres de Pokémon reales (vía `krabby list`) en `search`/`demo`, e IDs de captura en `equipo`.

## Datos

- Base de datos: `~/.local/share/pokedex-cli/pokedex.db` (tablas `captures` y `species_cache`).
- Último Pokémon visto: `~/.local/share/pokedex-cli/last_seen.json`.
- Inventario y progreso de actividad: `~/.local/share/pokedex-cli/inventory.json`.
- Para empezar de cero: `rm ~/.local/share/pokedex-cli/pokedex.db`.

## Instalación / reinstalación

```
./install.sh
```

Crea un venv (`--system-site-packages`, reutiliza `rich`/`requests` ya instalados en el sistema, sin tocar nada global) y el shim ejecutable `~/bin/pokedex`. Es idempotente: se puede volver a ejecutar sin problema si se mueve o reclona el proyecto.

## Desarrollo y verificación

El proyecto soporta Python 3.11–3.13. Para crear un entorno reproducible de
desarrollo y ejecutar los mismos controles que CI:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest --cov --cov-report=term-missing
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy pokedex_cli/domain pokedex_cli/application
```

Los tests ordinarios bloquean las conexiones de red no inyectadas y usan rutas
temporales para no tocar el `HOME`, la base de datos ni los repositorios reales
del usuario. Los escenarios de concurrencia ejecutan tanto sincronizaciones
simultáneas como veinte procesos reales del hook.

## Simplificaciones y casos límite conocidos

- Las formas Paldeanas de Tauros (`tauros-paldea`) no tienen equivalente exacto en PokeAPI; se capturan igual, pero sus stats/tipos son los de la especie base (se marca como aproximado).
- Si se borra `~/pokedex-cli` pero queda el shim en `~/bin/pokedex`, el `.zshrc` detecta el fallo y usa `krabby random` normal sin romper la terminal.
- Si la caché de cargo con los datos de `krabby` no está disponible (se limpió, o krabby se instaló de otra forma), el hook solo mostrará formas base, sin mega/gmax/regionales, ese día.
