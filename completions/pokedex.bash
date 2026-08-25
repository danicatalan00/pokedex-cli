# Autocompletado bash para pokedex-cli. Requiere bash 4.0 o superior.
# Instalación:
#   pokedex completion bash > \
#     "${XDG_DATA_HOME:-$HOME/.local/share}/bash-completion/completions/pokedex"
#   # sin el paquete bash-completion, cárgalo a mano desde ~/.bashrc:
#   source "${XDG_DATA_HOME:-$HOME/.local/share}/bash-completion/completions/pokedex"
#
# No depende del paquete bash-completion: solo usa builtins y compgen, así que
# funciona igual en un bash pelado.

_pokedex_pokemon_names() {
    # Nombres válidos según krabby, uno por línea.
    krabby list 2>/dev/null
}

_pokedex_capture_ids() {
    # IDs de captura tal y como los lista `pokedex list` (primera columna numérica).
    pokedex list 2>/dev/null | awk '$1 ~ /^[0-9]+$/ {print $1}'
}

_pokedex_contains() {
    local needle=$1 item
    shift
    for item in "$@"; do
        [[ $item == "$needle" ]] && return 0
    done
    return 1
}

_pokedex_offer() {
    # Propone las candidatas recibidas como argumentos. Usa $cur del llamante.
    local IFS=$' \t\n'
    mapfile -t COMPREPLY < <(compgen -W "$*" -- "$cur")
}

_pokedex_offer_lines() {
    # Igual, pero con una lista ya separada por líneas (salida de un comando).
    local IFS=$' \t\n'
    mapfile -t COMPREPLY < <(compgen -W "$1" -- "$cur")
}

_pokedex_options() {
    # Opciones aceptadas por cada subcomando, además de -h/--help.
    case $1 in
        ver|capturar|roca|piedra|cebo|caramelo) printf '%s' '--debug' ;;&
        capturar) printf ' %s' '-b' '--bola' ;;
        bolsas) printf '%s' '--info' ;;
        ranking) printf '%s' '--equipo' ;;
        search|vision) printf '%s' '-f --form' ;;
        demo)
            printf '%s' '-L --legendary -s --shiny -f --form -g --generations' \
                ' -r --result -b --bola -a --accion'
            ;;
        demo-vision) printf '%s' '-n --nivel -s --shiny -f --form --seed' ;;
        demo-evolucion) printf '%s' '-s --shiny --form-origen --form-destino --speed' ;;
    esac
}

_pokedex() {
    local IFS=$' \t\n'
    local cur prev command word i positional
    local -a options_with_value=(
        -b --bola -f --form -g --generations -r --result -a --accion
        -n --nivel --seed --speed --form-origen --form-destino
    )
    local command_index=0

    COMPREPLY=()
    cur=${COMP_WORDS[COMP_CWORD]-}
    prev=${COMP_WORDS[COMP_CWORD - 1]-}

    # bash parte «--bola=poke» en tres palabras. Recompón el par opción/valor
    # para que el cursor detrás del '=' complete el valor y no la opción.
    if [[ $cur == "=" && $COMP_CWORD -ge 1 ]]; then
        cur=""
    elif [[ $prev == "=" && $COMP_CWORD -ge 2 ]]; then
        prev=${COMP_WORDS[COMP_CWORD - 2]-}
    fi

    # El parser raíz no tiene opciones con valor: el subcomando es la primera
    # palabra que no es una opción.
    command=""
    for ((i = 1; i < COMP_CWORD; i++)); do
        word=${COMP_WORDS[i]}
        if [[ -n $word && $word != -* && $word != "=" ]]; then
            command=$word
            command_index=$i
            break
        fi
    done

    if [[ -z $command ]]; then
        _pokedex_offer ver capturar roca cebo bolsas list search vision equipo \
            tipos ranking legendarios refresh demo demo-vision demo-evolucion \
            completion hook -h --help
        return 0
    fi

    case $prev in
        -b | --bola)
            _pokedex_offer poke super ultra master
            return 0
            ;;
        -r | --result)
            _pokedex_offer random catch escape
            return 0
            ;;
        -a | --accion)
            _pokedex_offer bola roca cebo
            return 0
            ;;
        --speed)
            _pokedex_offer 0.7 1.0 1.4
            return 0
            ;;
        -f | --form | --form-origen | --form-destino | -g | --generations | -n | --nivel | --seed)
            # Texto libre: mejor no proponer nada que proponer ficheros.
            return 0
            ;;
    esac

    if [[ $cur == -* ]]; then
        _pokedex_offer "$(_pokedex_options "$command")" -h --help
        return 0
    fi

    # Cuenta los posicionales ya escritos para saber cuál toca completar.
    positional=0
    for ((i = command_index + 1; i < COMP_CWORD; i++)); do
        word=${COMP_WORDS[i]}
        if [[ $word == -* ]]; then
            if [[ ${COMP_WORDS[i + 1]-} == "=" ]]; then
                i=$((i + 2))
            elif _pokedex_contains "$word" "${options_with_value[@]}"; then
                i=$((i + 1))
            fi
            continue
        fi
        positional=$((positional + 1))
    done

    case $command in
        search | demo | demo-vision)
            ((positional == 0)) && _pokedex_offer_lines "$(_pokedex_pokemon_names)"
            ;;
        demo-evolucion)
            ((positional <= 1)) && _pokedex_offer_lines "$(_pokedex_pokemon_names)"
            ;;
        vision)
            ((positional == 0)) && _pokedex_offer_lines "$(_pokedex_capture_ids)"
            ;;
        equipo)
            if ((positional == 0)); then
                _pokedex_offer add remove
            elif ((positional == 1)); then
                _pokedex_offer_lines "$(_pokedex_capture_ids)
$(_pokedex_pokemon_names)"
            fi
            ;;
        completion)
            ((positional == 0)) && _pokedex_offer bash zsh
            ;;
    esac
    return 0
}

complete -F _pokedex pokedex
