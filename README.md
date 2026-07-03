# pokedex-cli

Cada terminal nueva pinta un Pokémon al azar (vía [`krabby`](https://github.com/yannjor/krabby)) sin decir cuál es. Si te interesa, lo capturas con un comando y queda guardado en una base de datos SQLite local, con tipos/stats/legendario enriquecidos desde [PokeAPI](https://pokeapi.co).

Nace de `chat-pokedex-cli.txt`, una conversación de chat donde surgió la idea.

## Cómo funciona

- `~/.zshrc` ya no llama a `krabby random` directamente: llama a `pokedex hook 1-3`, que:
  1. Elige especie + forma (incluye mega/gmax/regional, replicando el algoritmo real de `krabby random` a partir de una copia local de su base de datos) y tira shiny con el mismo `shiny_rate` de `~/.config/krabby/config.toml`.
  2. Pinta el sprite igual que antes (mismo aspecto, sigue sin revelar el nombre).
  3. Guarda en silencio cuál fue en `~/.local/share/pokedex-cli/last_seen.json` (solo se recuerda el último).
  - Si algo falla (`krabby` desinstalado, caché de datos ausente, etc.), cae de vuelta al `krabby random` de siempre — la terminal nunca se rompe.
- `pokedex capturar` guarda ese último Pokémon (con su forma y si es shiny) en SQLite, con una animación de pokeball, y lo enriquece con PokeAPI si hay red. No captura nada automáticamente: hay que pedirlo.

## Comandos

| Comando | Qué hace |
|---|---|
| `pokedex ver` | Muestra qué Pokémon está esperando, sin capturarlo |
| `pokedex capturar` | Captura el Pokémon que está esperando (animación + guardado) |
| `pokedex list` | Lista tus capturas |
| `pokedex search <nombre> [-f forma]` | Ficha de cualquier Pokémon/forma (ej. `pokedex search charizard -f mega-x`) |
| `pokedex equipo` | Muestra tu equipo (hasta 6) |
| `pokedex equipo add <id>` / `remove <id>` | Añade/quita una captura del equipo |
| `pokedex tipos` | Desglose de tus capturas por tipo |
| `pokedex ranking` | Ranking por suma de stats base, con medallas |
| `pokedex legendarios` | Salón de la fama de legendarios/singulares capturados |

## Datos

- Base de datos: `~/.local/share/pokedex-cli/pokedex.db` (tablas `captures` y `species_cache`).
- Último Pokémon visto: `~/.local/share/pokedex-cli/last_seen.json`.
- Para empezar de cero: `rm ~/.local/share/pokedex-cli/pokedex.db`.

## Instalación / reinstalación

```
./install.sh
```

Crea un venv (`--system-site-packages`, reutiliza `rich`/`requests` ya instalados en el sistema, sin tocar nada global) y el shim ejecutable `~/bin/pokedex`. Es idempotente: se puede volver a ejecutar sin problema si se mueve o reclona el proyecto.

## Simplificaciones y casos límite conocidos

- Las formas Paldeanas de Tauros (`tauros-paldea`) no tienen equivalente exacto en PokeAPI; se capturan igual, pero sus stats/tipos son los de la especie base (se marca como aproximado).
- Si se borra `~/pokedex-cli` pero queda el shim en `~/bin/pokedex`, el `.zshrc` detecta el fallo y usa `krabby random` normal sin romper la terminal.
- Si la caché de cargo con los datos de `krabby` no está disponible (se limpió, o krabby se instaló de otra forma), el hook solo mostrará formas base, sin mega/gmax/regionales, ese día.
