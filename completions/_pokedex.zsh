#compdef pokedex
# Autocompletado zsh para pokedex-cli.
# Instalación:
#   pokedex completion zsh > ~/.zfunc/_pokedex
#   # y en ~/.zshrc, antes de `compinit`:
#   fpath=(~/.zfunc $fpath)

_pokedex_pokemon_names() {
  local -a names
  # Nombres válidos según krabby (cacheado por zsh en la misma línea).
  names=(${(f)"$(krabby list 2>/dev/null)"})
  _describe -t pokemon 'Pokémon' names
}

_pokedex_capture_ids() {
  local -a ids
  # IDs de captura tal y como los lista `pokedex list` (primera columna numérica).
  ids=(${(f)"$(pokedex list 2>/dev/null | awk '$1 ~ /^[0-9]+$/ {print $1}')"})
  _describe -t ids 'id de captura' ids
}

_pokedex() {
  local curcontext="$curcontext" state line
  typeset -A opt_args

  local -a commands
  commands=(
    'ver:muestra qué Pokémon está esperando'
    'capturar:intenta capturar el Pokémon que espera'
    'bolsas:muestra y actualiza tus Pokeballs'
    'list:lista tus capturas'
    'search:ficha de cualquier Pokémon o forma'
    'vision:vista enriquecida de un Pokémon capturado (sprite + ficha)'
    'equipo:ve o gestiona tu equipo (máx. 6)'
    'tipos:desglose de tus capturas por tipo'
    'ranking:ranking por suma de stats base'
    'legendarios:tu salón de la fama de legendarios'
    'demo:prueba la animación de captura sin guardar nada'
    'demo-evolucion:prueba la animación de evolución sin guardar nada'
    'completion:imprime el script de autocompletado'
    'hook:(interno) pinta un Pokémon y recuerda cuál fue'
  )

  _arguments -C \
    '1: :->command' \
    '*:: :->args'

  case $state in
    command)
      _describe -t commands 'comando pokedex' commands
      ;;
    args)
      case $line[1] in
        capturar)
          _arguments \
            '(-b --bola)'{-b,--bola}'[elige la Pokeball sin menú]:bola:(poke super ultra master)' \
            '--debug[muestra la probabilidad exacta de captura]'
          ;;
        bolsas)
          _arguments \
            '--info[muestra efectividad, límites y reglas de reposición]'
          ;;
        search)
          _arguments \
            '(-f --form)'{-f,--form}'[forma alternativa]:forma:' \
            '1:Pokémon:_pokedex_pokemon_names'
          ;;
        vision)
          _arguments \
            '(-f --form)'{-f,--form}'[desambigua la forma]:forma:' \
            '1:captura:_pokedex_capture_ids'
          ;;
        demo)
          _arguments \
            '(-L --legendary)'{-L,--legendary}'[contra un legendario al azar]' \
            '(-s --shiny)'{-s,--shiny}'[fuerza variante shiny]' \
            '(-f --form)'{-f,--form}'[forma alternativa]:forma:' \
            '(-g --generations)'{-g,--generations}'[generaciones para el azar]:gens:' \
            '(-r --result)'{-r,--result}'[fuerza el resultado]:resultado:(random catch escape)' \
            '(-b --bola)'{-b,--bola}'[animación de Pokeball]:bola:(poke super ultra master)' \
            '1:Pokémon:_pokedex_pokemon_names'
          ;;
        demo-evolucion)
          _arguments \
            '(-s --shiny)'{-s,--shiny}'[fuerza variante shiny]' \
            '--form-origen[forma de la especie original]:forma:' \
            '--form-destino[forma de la especie evolucionada]:forma:' \
            '--speed[factor de duración]:factor:(0.7 1.0 1.4)' \
            '1:origen:_pokedex_pokemon_names' \
            '2:destino:_pokedex_pokemon_names'
          ;;
        equipo)
          if (( CURRENT == 2 )); then
            _values 'acción' 'add[añade una captura]' 'remove[quita una captura]'
          else
            _pokedex_capture_ids
          fi
          ;;
        hook)
          _message 'generaciones (p.ej. 1-3 o 1,3,6)'
          ;;
        completion)
          _values 'shell' 'zsh'
          ;;
      esac
      ;;
  esac
}

_pokedex "$@"
