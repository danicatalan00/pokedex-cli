"""Textos del combate en español, con el tono de los juegos de Gen 3.

Cada evento semántico del motor se cuenta aquí con frases cortas: la CLI las
imprime tal cual y la TUI las escribe a máquina. Todo es puro y sin estado, así
que el mismo evento produce siempre las mismas frases y la CLI, la TUI y los
tests comparten guion.

Los Pokémon se nombran con la etiqueta de su bando («Pidgey salvaje», «Onix
enemigo» o, si es del jugador, solo «Charizard»). Los adjetivos de estado van
en masculino porque concuerdan con «Pokémon», como en los juegos.
"""

from __future__ import annotations

from collections.abc import Sequence

from pokedex_cli.domain.battle.model import (
    BallThrown,
    BattleEnded,
    BattleEvent,
    BattleKind,
    BattleStarted,
    Charging,
    Confused,
    ConfusionCheck,
    ConfusionEnded,
    ConfusionHurt,
    DamageCause,
    DamageClass,
    Damaged,
    EscapeAttempted,
    ExperienceGained,
    FailReason,
    Fainted,
    ForcedOut,
    HealCause,
    Healed,
    HitCount,
    ImmobileReason,
    Immobilized,
    LeveledUp,
    Move,
    MoveFailed,
    MoveLearned,
    MoveMissed,
    MoveUsed,
    NoEffect,
    NoMovesLeft,
    OneHitKO,
    OpponentFled,
    Outcome,
    Protected,
    ProtectionRaised,
    PumpedUp,
    Recalled,
    Released,
    ReplacementNeeded,
    ScreenFaded,
    ScreenRaised,
    Seeded,
    SentOut,
    SideId,
    StatChanged,
    StatsReset,
    StatUnchanged,
    Status,
    StatusBlocked,
    StatusCured,
    StatusInflicted,
    Teleported,
    Trapped,
    TurnStarted,
)
from pokedex_cli.domain.battle.typechart import TYPE_NAMES_ES, effectiveness

# Nombres de las características tal y como aparecen en los juegos.
STAT_NAMES_ES: dict[str, str] = {
    "hp": "PS",
    "atk": "Ataque",
    "def": "Defensa",
    "spa": "Ataque Especial",
    "spd": "Defensa Especial",
    "spe": "Velocidad",
    "accuracy": "Precisión",
    "evasion": "Evasión",
}

# Artículo determinado de cada característica: «el Ataque», «la Defensa»,
# «los PS». Un stat desconocido usa el femenino de «la característica».
STAT_ARTICLES_ES: dict[str, str] = {
    "hp": "los",
    "atk": "el",
    "def": "la",
    "spa": "el",
    "spd": "la",
    "spe": "la",
    "accuracy": "la",
    "evasion": "la",
}

# Insignia de la caja de datos: abreviatura española y color de rich. Veneno y
# tóxico comparten abreviatura; el tóxico es más oscuro porque empeora cada turno.
STATUS_BADGES: dict[Status, tuple[str, str]] = {
    Status.BURN: ("QUE", "#e0603a"),
    Status.PARALYSIS: ("PAR", "#d8b62a"),
    Status.POISON: ("ENV", "#a85bbf"),
    Status.TOXIC: ("ENV", "#7e3a96"),
    Status.SLEEP: ("DOR", "#8d8d9e"),
    Status.FREEZE: ("CON", "#5cb8e0"),
}

DAMAGE_CLASS_NAMES_ES: dict[DamageClass, str] = {
    DamageClass.PHYSICAL: "Físico",
    DamageClass.SPECIAL: "Especial",
    DamageClass.STATUS: "Estado",
}

_STATUS_ADJECTIVES: dict[Status, str] = {
    Status.BURN: "quemado",
    Status.PARALYSIS: "paralizado",
    Status.POISON: "envenenado",
    Status.TOXIC: "envenenado",
    Status.SLEEP: "dormido",
    Status.FREEZE: "congelado",
}

# Frase de carga de los movimientos de dos turnos; el resto usa la genérica.
_CHARGE_PHRASES: dict[str, str] = {
    "solar-beam": "¡{who} absorbió luz solar!",
    "solar-blade": "¡{who} absorbió luz solar!",
    "fly": "¡{who} voló muy alto!",
    "dig": "¡{who} se ocultó bajo tierra!",
    "dive": "¡{who} se sumergió en el agua!",
    "bounce": "¡{who} dio un gran salto!",
    "skull-bash": "¡{who} agachó la cabeza!",
    "sky-attack": "¡{who} está envuelto en una luz cegadora!",
    "razor-wind": "¡{who} provocó un remolino!",
    "phantom-force": "¡{who} se desvaneció de repente!",
    "shadow-force": "¡{who} se desvaneció de repente!",
    "meteor-beam": "¡{who} rebosa energía cósmica!",
}

# Pantallas: nombre en español con su artículo, y qué ataques frenan.
_SCREENS: dict[str, tuple[str, str, str]] = {
    "reflect": ("el", "Reflejo", "físicos"),
    "light-screen": ("la", "Pantalla Luz", "especiales"),
}

# Mensajes de la bola que se abre según las sacudidas (como en Gen 3).
_BREAKOUT_MESSAGES: tuple[str, ...] = (
    "¡Oh, no! ¡El Pokémon se ha escapado!",
    "¡Vaya! ¡Parecía que lo habías atrapado!",
    "¡Aaaah! ¡Casi lo consigues!",
    "¡Qué rabia! ¡Ha faltado muy poco!",
)


def type_name(type_slug: str) -> str:
    """Nombre español del tipo; un tipo desconocido se muestra legible."""
    return TYPE_NAMES_ES.get(type_slug, type_slug.replace("-", " ").title())


def stat_with_article(stat: str) -> str:
    """«el Ataque», «la Defensa», «los PS»… para usar dentro de una frase."""
    name = STAT_NAMES_ES.get(stat, stat.replace("-", " ").title())
    return f"{STAT_ARTICLES_ES.get(stat, 'la')} {name}"


def display_label(kind: BattleKind, side: SideId, name: str) -> str:
    """Cómo se nombra a un Pokémon en el texto: «Pidgey salvaje», «Pidgey
    enemigo» (entrenador o exhibición) o «Pidgey» si es del jugador."""
    if side == "player":
        return name
    if kind is BattleKind.WILD:
        return f"{name} salvaje"
    return f"{name} enemigo"


def messages_for(
    event: BattleEvent,
    *,
    kind: BattleKind,
    trainer: str | None,
    opponent: str | None = None,
    upcoming: BattleEvent | None = None,
) -> list[str]:
    """Frases que cuentan ``event``, en orden; ``[]`` si ese caso no se narra.

    ``trainer`` es el nombre del entrenador rival (None en salvaje o si no se
    conoce). ``opponent`` es el nombre del rival activo y solo hace falta para
    los eventos que no lo llevan dentro (la captura con ``BallThrown``).
    ``upcoming`` es el evento siguiente del turno: en un multigolpe la eficacia
    se dice una sola vez, tras el último golpe (como en Gen 3); sin él, cada
    golpe la cuenta entera.
    Ningún evento lanza: un caso imprevisto produce una frase neutra o nada.
    ``TurnStarted`` da un separador «— Turno N —» útil en el registro de la CLI;
    el director de la TUI no lo escribe en la caja de texto.
    """

    def who(side: SideId, name: str) -> str:
        return display_label(kind, side, name)

    match event:
        case BattleStarted():
            return _battle_started(event, trainer)
        case TurnStarted():
            return [f"— Turno {event.turn} —"]
        case SentOut():
            return _sent_out(event, kind, trainer)
        case Recalled():
            if event.side == "player":
                return [f"¡{event.name}, vuelve!"]
            if trainer:
                return [f"¡{trainer} retiró a {event.name}!"]
            return [f"¡{who(event.side, event.name)} se retira!"]
        case MoveUsed():
            return [f"¡{who(event.side, event.name)} usó {event.move_name}!"]
        case NoMovesLeft():
            return [f"¡A {who(event.side, event.name)} no le quedan movimientos!"]
        case MoveMissed():
            return [f"¡El ataque de {who(event.side, event.name)} falló!"]
        case MoveFailed():
            return [_failure(event.reason)]
        case NoEffect():
            return [f"No afecta a {who(event.side, event.name)}…"]
        case ProtectionRaised():
            return [f"¡{who(event.side, event.name)} se está protegiendo!"]
        case Protected():
            return [f"¡{who(event.side, event.name)} se ha protegido!"]
        case Damaged():
            return _damaged(event, who(event.side, event.name), _more_hits(event, upcoming))
        case HitCount():
            times = "vez" if event.hits == 1 else "veces"
            return [f"¡Alcanzó {event.hits} {times}!"]
        case OneHitKO():
            return ["¡Un golpe fulminante!"]
        case Healed():
            return _healed(event, who(event.side, event.name))
        case StatusInflicted():
            return _status_inflicted(event.status, who(event.side, event.name))
        case StatusCured():
            return _status_cured(event.status, who(event.side, event.name))
        case StatusBlocked():
            target = who(event.side, event.name)
            if event.already:
                return [f"¡{target} ya tiene un problema de estado!"]
            return [f"No afecta a {target}…"]
        case Immobilized():
            return [_immobilized(event.reason, who(event.side, event.name))]
        case Confused():
            return [f"¡{who(event.side, event.name)} se ha confundido!"]
        case ConfusionCheck():
            return [f"¡{who(event.side, event.name)} está confuso!"]
        case ConfusionHurt():
            return ["¡Está tan confuso que se hirió a sí mismo!"]
        case ConfusionEnded():
            return [f"¡{who(event.side, event.name)} ya no está confuso!"]
        case StatChanged():
            return [_stat_changed(event.stat, event.delta, who(event.side, event.name))]
        case StatUnchanged():
            subject = _capitalized(stat_with_article(event.stat))
            verb = "subir" if event.rising else "bajar"
            return [f"¡{subject} de {who(event.side, event.name)} no puede {verb} más!"]
        case StatsReset():
            return ["¡Se anularon los cambios de características!"]
        case PumpedUp():
            return [f"¡{who(event.side, event.name)} se está preparando para luchar!"]
        case Charging():
            phrase = _CHARGE_PHRASES.get(event.move, "¡{who} se está preparando!")
            return [phrase.format(who=who(event.side, event.name))]
        case ScreenRaised():
            return [_screen_raised(event.screen, _team_of(event.side, kind))]
        case ScreenFaded():
            return [_screen_faded(event.screen, _team_of(event.side, kind))]
        case Seeded():
            return [f"¡{who(event.side, event.name)} fue infectado por Drenadoras!"]
        case Trapped():
            return [f"¡{who(event.side, event.name)} ha sido atrapado por {event.move_name}!"]
        case Released():
            return [f"¡{who(event.side, event.name)} se liberó de {event.move_name}!"]
        case Teleported():
            return [f"¡{who(event.side, event.name)} se teletransportó y huyó del combate!"]
        case ForcedOut():
            if kind is BattleKind.WILD:
                return [f"¡{who(event.side, event.name)} huyó asustado!"]
            return [f"¡{who(event.side, event.name)} fue expulsado del combate!"]
        case Fainted():
            return [f"¡{who(event.side, event.name)} se debilitó!"]
        case ExperienceGained():
            points = "punto" if event.amount == 1 else "puntos"
            return [f"¡{event.name} ganó {event.amount} {points} de experiencia!"]
        case LeveledUp():
            return [f"¡{event.name} subió al nivel {event.level}!"]
        case MoveLearned():
            if event.forgot:
                return [
                    "1, 2 y… ¡Puf!",
                    f"¡{event.name} olvidó {event.forgot} y aprendió {event.move_name}!",
                ]
            return [f"¡{event.name} aprendió {event.move_name}!"]
        case EscapeAttempted():
            if event.forbidden:
                return ["¡No puedes huir de un combate contra un entrenador!"]
            return ["¡Escapaste sin problemas!" if event.success else "¡No puedes escapar!"]
        case BallThrown():
            return _ball_thrown(event, opponent)
        case OpponentFled():
            return [f"¡{who('opponent', event.name)} ha huido!"]
        case ReplacementNeeded():
            if event.side == "player":
                return ["¿A qué Pokémon vas a sacar?"]
            return [f"{trainer or 'El rival'} se prepara para sacar a otro Pokémon."]
        case BattleEnded():
            return _battle_ended(event.outcome, kind, trainer)
    return []


def move_summary(move: Move) -> str:
    """Resumen de una línea: «Fuego · Especial · Pot 90 · Prec 100».

    Un movimiento sin potencia (de estado o de daño fijo) o que no falla nunca
    muestra «—» en ese dato.
    """
    power = "—" if move.power is None else str(move.power)
    accuracy = "—" if move.accuracy is None else str(move.accuracy)
    category = DAMAGE_CLASS_NAMES_ES.get(move.damage_class, "Estado")
    return f"{type_name(move.type)} · {category} · Pot {power} · Prec {accuracy}"


def effectiveness_hint(move: Move, defender_types: Sequence[str]) -> str | None:
    """Pista de eficacia para el menú: «Muy eficaz», «Poco eficaz» o «No afecta».

    None si el golpe es neutro o el movimiento es de estado. Los movimientos
    dañinos sin potencia (daño fijo, por nivel o fulminantes) solo avisan de la
    inmunidad: el resto de la tabla de tipos no les cambia el daño.
    """
    if not move.is_damaging:
        return None
    multiplier = effectiveness(move.type, defender_types)
    if multiplier == 0:
        return "No afecta"
    if move.power is None:
        return None
    if multiplier > 1:
        return "Muy eficaz"
    if multiplier < 1:
        return "Poco eficaz"
    return None


# --- Piezas de cada evento --------------------------------------------------


def _capitalized(text: str) -> str:
    return text[:1].upper() + text[1:]


def _battle_started(event: BattleStarted, trainer: str | None) -> list[str]:
    if event.kind is BattleKind.TRAINER:
        name = event.trainer or trainer
        return [f"¡{name} quiere luchar!" if name else "¡Un entrenador quiere luchar!"]
    if event.kind is BattleKind.EXHIBITION:
        return ["¡Comienza el combate de exhibición!"]
    # En salvaje la aparición la cuenta el SentOut del rival, como en los juegos.
    return []


def _sent_out(event: SentOut, kind: BattleKind, trainer: str | None) -> list[str]:
    if event.side == "player":
        return [f"¡Adelante, {event.name}!"]
    if kind is BattleKind.WILD:
        return [f"¡Un {event.name} salvaje apareció!"]
    if trainer:
        return [f"¡{trainer} sacó a {event.name}!"]
    if kind is BattleKind.TRAINER:
        return [f"¡El rival sacó a {event.name}!"]
    return [f"¡{event.name} enemigo sale al combate!"]


def _failure(reason: FailReason) -> str:
    if reason is FailReason.UNSUPPORTED:
        return "¡Pero no tuvo efecto!"
    if reason is FailReason.NOTHING_HAPPENED:
        return "¡Pero no pasó nada!"
    if reason is FailReason.TARGET_AWAKE:
        return "¡Pero falló! El objetivo no está dormido."
    if reason is FailReason.LEVEL_GAP:
        return "¡Pero falló! El objetivo es de nivel superior."
    return "¡Pero falló!"


def _more_hits(event: Damaged, upcoming: BattleEvent | None) -> bool:
    """¿Viene otro golpe del mismo movimiento sobre el mismo objetivo?"""
    return (
        event.cause is DamageCause.MOVE
        and isinstance(upcoming, Damaged)
        and upcoming.cause is DamageCause.MOVE
        and upcoming.key == event.key
    )


def _damaged(event: Damaged, target: str, more_hits: bool = False) -> list[str]:
    cause = event.cause
    if cause is DamageCause.MOVE:
        lines: list[str] = []
        if event.critical:
            lines.append("¡Un golpe crítico!")
        if more_hits:
            # La eficacia del multigolpe la cuenta el último golpe.
            return lines
        if event.effectiveness > 1:
            lines.append("¡Es muy eficaz!")
        elif 0 < event.effectiveness < 1:
            lines.append("No es muy eficaz…")
        return lines
    if cause is DamageCause.RECOIL:
        return [f"¡{target} se hizo daño con el retroceso!"]
    if cause is DamageCause.BURN:
        return [f"¡{target} se hizo daño por la quemadura!"]
    if cause is DamageCause.POISON:
        return [f"¡El veneno resta PS a {target}!"]
    if cause is DamageCause.LEECH_SEED:
        return [f"¡Las Drenadoras restan salud a {target}!"]
    if cause is DamageCause.TRAP:
        return [f"¡{target} sufre daño por estar atrapado!"]
    # La confusión ya la cuenta ConfusionHurt y el sacrificio, el desmayo.
    return []


def _healed(event: Healed, target: str) -> list[str]:
    if event.cause is HealCause.DRAIN:
        return [f"¡{target} absorbió energía!"]
    if event.cause is HealCause.REST:
        return [f"¡{target} descansó y recuperó la salud!"]
    if event.cause is HealCause.LEECH_SEED:
        # La frase de Drenadoras ya acompaña al daño del infectado.
        return []
    return [f"¡{target} recuperó salud!"]


def _status_inflicted(status: Status, target: str) -> list[str]:
    if status is Status.BURN:
        return [f"¡{target} fue quemado!"]
    if status is Status.PARALYSIS:
        return [f"¡{target} está paralizado! ¡Quizás no pueda moverse!"]
    if status is Status.POISON:
        return [f"¡{target} fue envenenado!"]
    if status is Status.TOXIC:
        return [f"¡{target} fue gravemente envenenado!"]
    if status is Status.SLEEP:
        return [f"¡{target} se durmió!"]
    if status is Status.FREEZE:
        return [f"¡{target} se congeló!"]
    return []


def _status_cured(status: Status, target: str) -> list[str]:
    if status is Status.SLEEP:
        return [f"¡{target} se despertó!"]
    if status is Status.FREEZE:
        return [f"¡{target} se descongeló!"]
    adjective = _STATUS_ADJECTIVES.get(status)
    return [f"¡{target} ya no está {adjective}!"] if adjective else []


def _immobilized(reason: ImmobileReason, target: str) -> str:
    if reason is ImmobileReason.ASLEEP:
        return f"{target} está dormido como un tronco."
    if reason is ImmobileReason.FROZEN:
        return f"{target} está congelado."
    if reason is ImmobileReason.PARALYZED:
        return f"¡{target} está paralizado! ¡No se puede mover!"
    if reason is ImmobileReason.FLINCHED:
        return f"¡{target} retrocedió!"
    return f"{target} necesita recuperarse."


def _stat_changed(stat: str, delta: int, target: str) -> str:
    subject = _capitalized(stat_with_article(stat))
    verb = "subió" if delta > 0 else "bajó"
    size = abs(delta)
    intensity = "" if size <= 1 else " mucho" if size == 2 else " muchísimo"
    return f"¡{subject} de {target} {verb}{intensity}!"


def _team_of(side: SideId, kind: BattleKind) -> tuple[str, str]:
    """Complemento con preposición para las pantallas: («a tu equipo», «de tu equipo»)."""
    if side == "player":
        return "a tu equipo", "de tu equipo"
    if kind is BattleKind.WILD:
        return "al Pokémon salvaje", "del Pokémon salvaje"
    return "al equipo rival", "del equipo rival"


def _screen_parts(screen: str) -> tuple[str, str, str | None]:
    if screen in _SCREENS:
        return _SCREENS[screen]
    return "el", screen.replace("-", " ").title(), None


def _screen_raised(screen: str, team: tuple[str, str]) -> str:
    _, name, kind_of_attack = _screen_parts(screen)
    if kind_of_attack is None:
        return f"¡{name} protege {team[0]}!"
    return f"¡{name} protege {team[0]} de los ataques {kind_of_attack}!"


def _screen_faded(screen: str, team: tuple[str, str]) -> str:
    article, name, _ = _screen_parts(screen)
    return f"¡{_capitalized(article)} {name} {team[1]} se agotó!"


def _ball_thrown(event: BallThrown, opponent: str | None) -> list[str]:
    lines = [f"¡Lanzaste una {event.ball_name}!"]
    if event.caught:
        # «a» + «el» se contrae: «¡Has capturado al Pokémon!».
        target = f"a {opponent}" if opponent else "al Pokémon"
        lines.append(f"¡Ya está! ¡Has capturado {target}!")
    else:
        index = max(0, min(len(_BREAKOUT_MESSAGES) - 1, event.shakes))
        lines.append(_BREAKOUT_MESSAGES[index])
    return lines


def _battle_ended(outcome: Outcome, kind: BattleKind, trainer: str | None) -> list[str]:
    if outcome is Outcome.WON:
        if kind is BattleKind.TRAINER:
            return [f"¡Has derrotado a {trainer}!" if trainer else "¡Has derrotado al rival!"]
        if kind is BattleKind.EXHIBITION:
            return ["¡Tu equipo gana la exhibición!"]
        return ["¡Has ganado el combate!"]
    if outcome is Outcome.LOST:
        if kind is BattleKind.EXHIBITION:
            return ["¡El equipo rival gana la exhibición!"]
        lines = ["¡No te quedan Pokémon en condiciones de luchar!"]
        if kind is BattleKind.TRAINER and trainer:
            lines.append(f"Has perdido contra {trainer}.")
        return lines
    if outcome is Outcome.DRAW:
        return ["¡Las dos partes están agotadas! El combate acaba en tablas."]
    # Huida, captura y huida del rival ya se han contado con su propio evento.
    return []
