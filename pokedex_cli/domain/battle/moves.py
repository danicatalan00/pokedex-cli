"""Movimientos: qué sabe ejecutar el motor, cómo se guardan y los de reserva.

Los datos llegan de PokeAPI a través de la caché como registros con las claves
exactas de ``Move``; aquí se validan y se decide cuáles soporta el motor. Los
movimientos de reserva permiten jugar sin conexión con datos canónicos
actuales. Todas las tablas son inmutables (``frozenset`` o vistas de solo
lectura): no hay estado global que mutar.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pokedex_cli.domain.battle.model import STAGE_KEYS, DamageClass, Move, StatChange

# --- Reglas especiales por slug ---------------------------------------------

# Daño fijo en PS: ignora stats, STAB, crítico y eficacia (salvo la inmunidad).
FIXED_DAMAGE: Mapping[str, int] = MappingProxyType({"dragon-rage": 40, "sonic-boom": 20})
# Quitan tantos PS como el nivel del usuario.
LEVEL_DAMAGE: frozenset[str] = frozenset({"seismic-toss", "night-shade"})
# Quitan la mitad de los PS actuales del objetivo (mínimo 1).
HALF_HP_DAMAGE: frozenset[str] = frozenset({"super-fang", "natures-madness", "ruination"})
# El usuario se debilita al usarlos.
SACRIFICE: frozenset[str] = frozenset({"explosion", "self-destruct", "misty-explosion"})
# Dos turnos: el primero carga y el segundo golpea.
CHARGE_MOVES: frozenset[str] = frozenset(
    {
        "solar-beam",
        "solar-blade",
        "razor-wind",
        "skull-bash",
        "sky-attack",
        "fly",
        "dig",
        "dive",
        "bounce",
        "phantom-force",
        "shadow-force",
        "meteor-beam",
    }
)
# Durante su carga el usuario es semi-invulnerable: los ataques le fallan.
SEMI_INVULNERABLE: frozenset[str] = frozenset(
    {"fly", "dig", "dive", "bounce", "phantom-force", "shadow-force"}
)
# Tras golpear, el usuario pasa el turno siguiente recargando.
RECHARGE_MOVES: frozenset[str] = frozenset(
    {
        "hyper-beam",
        "giga-impact",
        "blast-burn",
        "hydro-cannon",
        "frenzy-plant",
        "rock-wrecker",
        "roar-of-time",
        "prismatic-laser",
        "eternabeam",
        "meteor-assault",
    }
)
# Bloquean los movimientos rivales de ese turno; encadenarlos cada vez falla más.
PROTECT_MOVES: frozenset[str] = frozenset({"protect", "detect"})
# Descanso: cura PS y estado y duerme dos turnos.
REST = "rest"
# Pantallas: slug → nombre de la pantalla en ``ScreenRaised``/``ScreenFaded``.
SCREENS: Mapping[str, str] = MappingProxyType(
    {"reflect": "reflect", "light-screen": "light-screen"}
)
# Niebla: anula los cambios de características de los dos lados.
HAZE: frozenset[str] = frozenset({"haze"})
# Foco Energía: +2 escalones de crítico mientras siga en el campo.
FOCUS_ENERGY: frozenset[str] = frozenset({"focus-energy"})
# Dejan al objetivo con al menos 1 PS.
FALSE_SWIPE: frozenset[str] = frozenset({"false-swipe", "hold-back"})
# Envenenamiento grave (n/16 creciente) en lugar de veneno normal.
TOXIC_MOVES: frozenset[str] = frozenset({"toxic"})
# Solo funcionan contra un objetivo dormido.
DREAM_EATER: frozenset[str] = frozenset({"dream-eater"})
# No hacen nada: «¡Pero no pasó nada!».
SPLASH: frozenset[str] = frozenset({"splash", "celebrate", "hold-hands"})
# Huida del combate salvaje; contra un entrenador falla.
TELEPORT: frozenset[str] = frozenset({"teleport"})
# Rugido y Remolino: echan al rival del combate (o fuerzan un relevo).
FORCE_SWITCH: frozenset[str] = frozenset({"roar", "whirlwind"})

# Objetivos de PokeAPI que apuntan al propio usuario o a su lado del campo.
SELF_TARGETS: frozenset[str] = frozenset(
    {"user", "users-field", "user-or-ally", "user-and-allies", "all-allies"}
)

# Categorías ``meta`` de PokeAPI que el motor ejecuta de forma genérica.
SUPPORTED_CATEGORIES: frozenset[str] = frozenset(
    {
        "damage",
        "ailment",
        "net-good-stats",
        "heal",
        "damage-ailment",
        "swagger",
        "damage-lower",
        "damage-raise",
        "damage-heal",
        "ohko",
        "force-switch",
    }
)
# Problemas que el motor aplica; ``none`` y ``unknown`` no aplican ninguno.
SUPPORTED_AILMENTS: frozenset[str] = frozenset(
    {
        "paralysis",
        "sleep",
        "freeze",
        "burn",
        "poison",
        "confusion",
        "trap",
        "leech-seed",
        "none",
        "unknown",
    }
)

# Efectos únicos que el motor ejecuta por slug, sea cual sea su categoría
# (``unique``, ``field-effect``, ``whole-field-effect``…).
_SLUG_EFFECTS: frozenset[str] = (
    PROTECT_MOVES
    | {REST}
    | frozenset(SCREENS)
    | HAZE
    | FOCUS_ENERGY
    | SPLASH
    | TELEPORT
    | FORCE_SWITCH
)
_DAMAGING_CATEGORIES: frozenset[str] = frozenset(
    {
        "damage",
        "damage-ailment",
        "damage-lower",
        "damage-raise",
        "damage-heal",
        "ohko",
        "force-switch",
    }
)
_STATUS_CATEGORIES: frozenset[str] = frozenset(
    {"ailment", "net-good-stats", "heal", "swagger", "force-switch"}
)
_EFFECTIVE_AILMENTS: frozenset[str] = SUPPORTED_AILMENTS - {"none", "unknown"}
# Un movimiento de estado que solo atrapa (Mal de Ojo) impide huir o cambiar,
# y eso el motor no lo modela: atrapar solo cuenta en los que dañan.
_STATUS_AILMENTS: frozenset[str] = _EFFECTIVE_AILMENTS - {"trap"}


def is_fixed_damage(move: Move) -> bool:
    """Daño que no depende de stats: fijo, igual al nivel o mitad de los PS."""
    return move.slug in FIXED_DAMAGE or move.slug in LEVEL_DAMAGE or move.slug in HALF_HP_DAMAGE


def hit_range(move: Move) -> tuple[int, int]:
    """Golpes mínimo y máximo por uso; ``(1, 1)`` si no es multigolpe."""
    low = move.min_hits if move.min_hits is not None else move.max_hits
    high = move.max_hits if move.max_hits is not None else move.min_hits
    minimum = max(1, low or 1)
    return minimum, max(minimum, high or 1)


def is_supported(move: Move) -> bool:
    """¿Sabe el motor ejecutar este movimiento de principio a fin?

    - Los efectos únicos de las tablas (Protección, Descanso, Reflejo…) siempre.
    - Si no, la categoría debe ser genérica (``unique`` y los efectos de campo
      quedan fuera) y coherente con la clase de daño.
    - Un dañino sin potencia solo si su daño es fijo o fulminante (``ohko``).
    - Uno de estado solo si hace algo: un problema aplicable, algún cambio de
      características o una curación positiva. Así Atracción o un movimiento
      sin ``meta`` fallan con «no tuvo efecto» en vez de no hacer nada en
      silencio, y los movesets no los eligen.
    """
    if move.slug in _SLUG_EFFECTS:
        return True
    category = move.category or "damage"
    if category not in SUPPORTED_CATEGORIES:
        return False
    if move.is_damaging:
        if category not in _DAMAGING_CATEGORIES:
            return False
        return bool(move.power) or category == "ohko" or is_fixed_damage(move)
    if category not in _STATUS_CATEGORIES:
        return False
    if category == "ailment":
        return move.ailment in _STATUS_AILMENTS
    if category == "net-good-stats":
        return bool(move.stat_changes)
    if category == "heal":
        return move.healing > 0
    return True


# --- Registros de caché -----------------------------------------------------


class _InvalidRecord(ValueError):
    """Un campo presente tiene un tipo o un valor imposible."""


def move_from_record(record: Mapping[str, object]) -> Move | None:
    """Reconstruye un ``Move`` desde su registro de caché (``move_to_record``).

    Tolerante con lo que falta: un campo opcional ausente o nulo toma su valor
    por defecto y un ``name`` ausente o vacío se deriva del slug. Estricto con
    lo que está: un tipo inválido, un valor imposible o la falta de ``slug``,
    ``type``, ``damage_class`` o ``pp`` devuelven ``None``.
    """
    if not isinstance(record, Mapping):
        return None
    try:
        return _parse_move(record)
    except _InvalidRecord:
        return None


def _parse_move(record: Mapping[str, object]) -> Move:
    slug = _required_text(record, "slug")
    min_hits = _nullable_int(record, "min_hits", low=0)
    max_hits = _nullable_int(record, "max_hits", low=0)
    min_turns = _nullable_int(record, "min_turns", low=0)
    max_turns = _nullable_int(record, "max_turns", low=0)
    _require_order(min_hits, max_hits, "hits")
    _require_order(min_turns, max_turns, "turns")
    return Move(
        slug=slug,
        name=_optional_text(record, "name", _display_name(slug)),
        type=_required_text(record, "type"),
        damage_class=_damage_class(record.get("damage_class")),
        power=_nullable_int(record, "power", low=0),
        accuracy=_nullable_int(record, "accuracy", low=1, high=100),
        pp=_checked_int("pp", record.get("pp"), low=1),
        priority=_optional_int(record, "priority", 0),
        target=_optional_text(record, "target", "selected-pokemon"),
        category=_optional_text(record, "category", "damage"),
        ailment=_optional_text(record, "ailment", "none"),
        ailment_chance=_optional_int(record, "ailment_chance", 0, low=0, high=100),
        min_hits=min_hits,
        max_hits=max_hits,
        min_turns=min_turns,
        max_turns=max_turns,
        drain=_optional_int(record, "drain", 0, low=-100, high=100),
        healing=_optional_int(record, "healing", 0, low=-100, high=100),
        crit_rate=_optional_int(record, "crit_rate", 0, low=0),
        flinch_chance=_optional_int(record, "flinch_chance", 0, low=0, high=100),
        stat_changes=_stat_changes(record.get("stat_changes")),
        stat_chance=_optional_int(record, "stat_chance", 0, low=0, high=100),
        description=_nullable_text(record, "description"),
    )


def _display_name(slug: str) -> str:
    """``dragon-rage`` → ``Dragon Rage``: nombre provisional sin traducción."""
    return " ".join(part.capitalize() for part in slug.split("-") if part)


def _required_text(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _InvalidRecord(key)
    return value


def _optional_text(record: Mapping[str, object], key: str, default: str) -> str:
    value = record.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise _InvalidRecord(key)
    return value if value.strip() else default


def _nullable_text(record: Mapping[str, object], key: str) -> str | None:
    value = record.get(key)
    if value is None or isinstance(value, str):
        return value
    raise _InvalidRecord(key)


def _checked_int(
    key: str, value: object, *, low: int | None = None, high: int | None = None
) -> int:
    # ``bool`` es subclase de ``int`` en Python, pero ``True`` no es un número válido.
    if isinstance(value, bool) or not isinstance(value, int):
        raise _InvalidRecord(key)
    if (low is not None and value < low) or (high is not None and value > high):
        raise _InvalidRecord(key)
    return value


def _optional_int(
    record: Mapping[str, object],
    key: str,
    default: int,
    *,
    low: int | None = None,
    high: int | None = None,
) -> int:
    value = record.get(key)
    if value is None:
        return default
    return _checked_int(key, value, low=low, high=high)


def _nullable_int(
    record: Mapping[str, object],
    key: str,
    *,
    low: int | None = None,
    high: int | None = None,
) -> int | None:
    value = record.get(key)
    if value is None:
        return None
    return _checked_int(key, value, low=low, high=high)


def _require_order(low: int | None, high: int | None, key: str) -> None:
    if low is not None and high is not None and low > high:
        raise _InvalidRecord(key)


def _damage_class(value: object) -> DamageClass:
    if not isinstance(value, str):
        raise _InvalidRecord("damage_class")
    try:
        return DamageClass(value)
    except ValueError as error:
        raise _InvalidRecord("damage_class") from error


def _stat_changes(value: object) -> tuple[StatChange, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise _InvalidRecord("stat_changes")
    changes: list[StatChange] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise _InvalidRecord("stat_changes")
        stat = item.get("stat")
        if not isinstance(stat, str) or stat not in STAGE_KEYS:
            raise _InvalidRecord("stat_changes")
        change = _checked_int("stat_changes", item.get("change"))
        if change == 0:
            raise _InvalidRecord("stat_changes")
        changes.append(StatChange(stat=stat, change=change))
    return tuple(changes)


def move_to_record(move: Move) -> dict[str, object]:
    """Registro JSON-serializable con las claves exactas de ``Move``.

    Es el inverso exacto de ``move_from_record`` y el formato de la caché.
    """
    return {
        "slug": move.slug,
        "name": move.name,
        "type": move.type,
        "damage_class": move.damage_class.value,
        "power": move.power,
        "accuracy": move.accuracy,
        "pp": move.pp,
        "priority": move.priority,
        "target": move.target,
        "category": move.category,
        "ailment": move.ailment,
        "ailment_chance": move.ailment_chance,
        "min_hits": move.min_hits,
        "max_hits": move.max_hits,
        "min_turns": move.min_turns,
        "max_turns": move.max_turns,
        "drain": move.drain,
        "healing": move.healing,
        "crit_rate": move.crit_rate,
        "flinch_chance": move.flinch_chance,
        "stat_changes": [
            {"stat": change.stat, "change": change.change} for change in move.stat_changes
        ],
        "stat_chance": move.stat_chance,
        "description": move.description,
    }


# --- Movimientos de reserva (sin conexión) ----------------------------------

_PHYSICAL = DamageClass.PHYSICAL
_SPECIAL = DamageClass.SPECIAL

# Sin PP en ningún movimiento: golpe de 50 que hiere al usuario (¼ de sus PS máx.).
STRUGGLE = Move(
    slug="struggle",
    name="Forcejeo",
    type="normal",
    damage_class=_PHYSICAL,
    power=50,
    accuracy=None,
    pp=1,
    target="random-opponent",
    healing=-25,
)
TACKLE = Move(
    slug="tackle",
    name="Placaje",
    type="normal",
    damage_class=_PHYSICAL,
    power=40,
    accuracy=100,
    pp=35,
)

# Un dañino básico por tipo, con su clase de daño moderna y sus efectos.
FALLBACK_BY_TYPE: Mapping[str, Move] = MappingProxyType(
    {
        "normal": TACKLE,
        "fire": Move(
            slug="ember",
            name="Ascuas",
            type="fire",
            damage_class=_SPECIAL,
            power=40,
            accuracy=100,
            pp=25,
            category="damage-ailment",
            ailment="burn",
            ailment_chance=10,
        ),
        "water": Move(
            slug="water-gun",
            name="Pistola Agua",
            type="water",
            damage_class=_SPECIAL,
            power=40,
            accuracy=100,
            pp=25,
        ),
        "grass": Move(
            slug="vine-whip",
            name="Látigo Cepa",
            type="grass",
            damage_class=_PHYSICAL,
            power=45,
            accuracy=100,
            pp=25,
        ),
        "electric": Move(
            slug="thunder-shock",
            name="Impactrueno",
            type="electric",
            damage_class=_SPECIAL,
            power=40,
            accuracy=100,
            pp=30,
            category="damage-ailment",
            ailment="paralysis",
            ailment_chance=10,
        ),
        "ice": Move(
            slug="powder-snow",
            name="Nieve Polvo",
            type="ice",
            damage_class=_SPECIAL,
            power=40,
            accuracy=100,
            pp=25,
            target="all-opponents",
            category="damage-ailment",
            ailment="freeze",
            ailment_chance=10,
        ),
        "fighting": Move(
            slug="rock-smash",
            name="Golpe Roca",
            type="fighting",
            damage_class=_PHYSICAL,
            power=40,
            accuracy=100,
            pp=15,
            category="damage-lower",
            stat_changes=(StatChange(stat="def", change=-1),),
            stat_chance=50,
        ),
        "poison": Move(
            slug="poison-sting",
            name="Picotazo Ven",
            type="poison",
            damage_class=_PHYSICAL,
            power=15,
            accuracy=100,
            pp=35,
            category="damage-ailment",
            ailment="poison",
            ailment_chance=30,
        ),
        "ground": Move(
            slug="mud-slap",
            name="Bofetón Lodo",
            type="ground",
            damage_class=_SPECIAL,
            power=20,
            accuracy=100,
            pp=10,
            category="damage-lower",
            stat_changes=(StatChange(stat="accuracy", change=-1),),
            stat_chance=100,
        ),
        "flying": Move(
            slug="gust",
            name="Tornado",
            type="flying",
            damage_class=_SPECIAL,
            power=40,
            accuracy=100,
            pp=35,
        ),
        "psychic": Move(
            slug="confusion",
            name="Confusión",
            type="psychic",
            damage_class=_SPECIAL,
            power=50,
            accuracy=100,
            pp=25,
            category="damage-ailment",
            ailment="confusion",
            ailment_chance=10,
        ),
        "bug": Move(
            slug="bug-bite",
            name="Picadura",
            type="bug",
            damage_class=_PHYSICAL,
            power=60,
            accuracy=100,
            pp=20,
        ),
        "rock": Move(
            slug="rock-throw",
            name="Lanzarrocas",
            type="rock",
            damage_class=_PHYSICAL,
            power=50,
            accuracy=90,
            pp=15,
        ),
        "ghost": Move(
            slug="lick",
            name="Lengüetazo",
            type="ghost",
            damage_class=_PHYSICAL,
            power=30,
            accuracy=100,
            pp=30,
            category="damage-ailment",
            ailment="paralysis",
            ailment_chance=30,
        ),
        "dragon": Move(
            slug="dragon-breath",
            name="Dragoaliento",
            type="dragon",
            damage_class=_SPECIAL,
            power=60,
            accuracy=100,
            pp=20,
            category="damage-ailment",
            ailment="paralysis",
            ailment_chance=30,
        ),
        "dark": Move(
            slug="bite",
            name="Mordisco",
            type="dark",
            damage_class=_PHYSICAL,
            power=60,
            accuracy=100,
            pp=25,
            flinch_chance=30,
        ),
        "steel": Move(
            slug="metal-claw",
            name="Garra Metal",
            type="steel",
            damage_class=_PHYSICAL,
            power=50,
            accuracy=95,
            pp=35,
            category="damage-raise",
            stat_changes=(StatChange(stat="atk", change=1),),
            stat_chance=10,
        ),
        "fairy": Move(
            slug="fairy-wind",
            name="Viento Feérico",
            type="fairy",
            damage_class=_SPECIAL,
            power=40,
            accuracy=100,
            pp=30,
        ),
    }
)
