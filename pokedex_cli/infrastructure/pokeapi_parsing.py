import re
import sys
from collections.abc import Callable
from typing import Any

import requests

BASE_URL = "https://pokeapi.co/api/v2"
TIMEOUT = 5

STAT_NAME_MAP = {
    "hp": "hp",
    "attack": "atk",
    "defense": "def",
    "special-attack": "spa",
    "special-defense": "spd",
    "speed": "spe",
}
# Stats que un movimiento puede subir o bajar, con las claves de escalón del
# dominio (``domain.battle.model.STAGE_KEYS``).
STAGE_NAME_MAP = {
    "attack": "atk",
    "defense": "def",
    "special-attack": "spa",
    "special-defense": "spd",
    "speed": "spe",
    "accuracy": "accuracy",
    "evasion": "evasion",
}

# Grupo de versión del learnset por nivel, «clásica primero»: los learnsets de
# Gen 3 casan con las reglas del combate. Si ninguno de estos tiene al menos
# MIN_LEARNSET_MOVES movimientos, gana el grupo con más (sea cual sea).
LEARNSET_VERSION_PREFERENCE: tuple[str, ...] = (
    "emerald",
    "firered-leafgreen",
    "ruby-sapphire",
    "platinum",
    "heartgold-soulsilver",
    "diamond-pearl",
    "black-2-white-2",
    "black-white",
    "omega-ruby-alpha-sapphire",
    "x-y",
    "ultra-sun-ultra-moon",
    "sun-moon",
    "sword-shield",
    "scarlet-violet",
    "brilliant-diamond-shining-pearl",
    "legends-arceus",
    "crystal",
    "gold-silver",
    "yellow",
    "red-blue",
    "lets-go-pikachu-lets-go-eevee",
)
MIN_LEARNSET_MOVES = 4

# Texto con el que PokeAPI rellena los movimientos retirados en Espada/Escudo:
# no describe el movimiento, así que se salta como si no existiera.
UNUSABLE_MOVE_TEXTS = (
    "este movimiento no se puede usar",
    "this move can’t be used",
    "this move can't be used",
)

JsonObject = dict[str, Any]
JsonFetcher = Callable[[str], JsonObject | None]

# PokeAPI separa Meltan y Melmetal en dos evolution-chain distintas aunque
# pokemon-species/melmetal sí declara que evoluciona de Meltan.
MISSING_CHAIN_EVOLUTIONS = {"meltan": (("melmetal", "regular"),)}


def _get_json(url: str) -> JsonObject | None:
    try:
        response = requests.get(url, timeout=TIMEOUT)
    except requests.exceptions.RequestException:
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except ValueError:
        return None


def _flavor_text(species_json: JsonObject) -> str | None:
    entries = species_json.get("flavor_text_entries", [])
    for lang in ("es", "en"):
        for entry in entries:
            if entry.get("language", {}).get("name") == lang:
                return " ".join(entry["flavor_text"].split())
    return None


def _genus(species_json: JsonObject) -> str | None:
    for lang in ("es", "en"):
        for entry in species_json.get("genera", []):
            if entry.get("language", {}).get("name") == lang:
                value = entry.get("genus")
                return str(value) if value else None
    return None


def _resource_name(resource: object) -> str | None:
    if not isinstance(resource, dict):
        return None
    value = resource.get("name")
    return str(value) if value else None


def _evolution_chain(
    species_json: JsonObject,
    fetch_json: JsonFetcher | None = None,
) -> list[str]:
    chain_url = species_json.get("evolution_chain", {}).get("url")
    fetch = fetch_json or _get_json
    chain_json = fetch(chain_url) if chain_url else None
    root = chain_json.get("chain", {}) if chain_json else {}
    family: list[str] = []

    def visit(node: JsonObject) -> None:
        name = node.get("species", {}).get("name")
        if name and name not in family:
            family.append(str(name))
        for child in node.get("evolves_to", []):
            visit(child)

    visit(root)
    return family


def _pokemon_abilities(pokemon_json: JsonObject) -> list[str]:
    """Non-hidden abilities in slot order (Gen 3 canon: no hidden abilities)."""
    visible = [
        ability
        for ability in pokemon_json.get("abilities", [])
        if not ability.get("is_hidden", False)
    ]
    visible.sort(key=lambda ability: ability.get("slot", 0))
    return [ability["ability"]["name"] for ability in visible]


def _pokemon_stats_and_types(pokemon_json: JsonObject) -> JsonObject:
    types = [t["type"]["name"] for t in sorted(pokemon_json["types"], key=lambda t: t["slot"])]
    stats: JsonObject = {}
    for stat in pokemon_json["stats"]:
        key = STAT_NAME_MAP.get(stat["stat"]["name"])
        if key:
            stats[key] = stat["base_stat"]
    return {
        "types": types,
        "base_experience": pokemon_json.get("base_experience"),
        "height_dm": pokemon_json.get("height"),
        "weight_hg": pokemon_json.get("weight"),
        "abilities": _pokemon_abilities(pokemon_json),
        **stats,
    }


def _level_evolutions(
    species_json: JsonObject,
    pokemon_json: JsonObject,
    species: str,
    form: str,
    fetch_json: JsonFetcher | None = None,
) -> list[JsonObject]:
    chain_url = species_json.get("evolution_chain", {}).get("url")
    fetch = fetch_json or _get_json
    chain_json = fetch(chain_url) if chain_url else None
    if not chain_json:
        return []

    root = chain_json.get("chain", {})
    current_form_name = species if form == "regular" else f"{species}-{form}"

    def version_order(detail: JsonObject) -> int:
        version_url = detail.get("version_group", {}).get("url", "")
        match = re.search(r"/(\d+)/?$", version_url)
        return int(match.group(1)) if match else 10_000

    def path_to(node: JsonObject, name: str) -> list[JsonObject] | None:
        if node.get("species", {}).get("name") == name:
            return [node]
        for child in node.get("evolves_to", []):
            path = path_to(child, name)
            if path is not None:
                return [node, *path]
        return None

    def maximum_depth(node: JsonObject) -> int:
        children = node.get("evolves_to", [])
        return 0 if not children else 1 + max(maximum_depth(child) for child in children)

    def matches_base_form(detail: JsonObject) -> bool:
        base_form = detail.get("base_form")
        base_form_name = base_form.get("name") if base_form else None
        return (form == "regular" and base_form_name is None) or (
            base_form_name == current_form_name
        )

    def target_form(detail: JsonObject, target: str) -> str:
        evolved_form = detail.get("evolved_form")
        evolved_name = evolved_form.get("name") if evolved_form else target
        if evolved_name and evolved_name.startswith(target + "-"):
            return str(evolved_name[len(target) + 1 :])
        return "regular"

    def representative(details: list[JsonObject]) -> JsonObject:
        defaults = [detail for detail in details if detail.get("is_default") is True]
        return min(defaults or details, key=version_order)

    def learned_move_level(detail: JsonObject) -> int | None:
        required = detail.get("known_move") or detail.get("used_move")
        required_name = required.get("name") if required else None
        version_name = detail.get("version_group", {}).get("name")
        if not required_name or not version_name:
            return None
        for move in pokemon_json.get("moves", []):
            if move.get("move", {}).get("name") != required_name:
                continue
            levels = [
                int(entry["level_learned_at"])
                for entry in move.get("version_group_details", [])
                if entry.get("version_group", {}).get("name") == version_name
                and entry.get("move_learn_method", {}).get("name") == "level-up"
                and int(entry.get("level_learned_at") or 0) > 0
            ]
            return min(levels) if levels else None
        return None

    path = path_to(root, species)
    if path is None:
        return []
    current = path[-1]
    grouped: dict[tuple[str, str], list[JsonObject]] = {}
    order: list[tuple[str, str]] = []
    for child in current.get("evolves_to", []):
        target = child.get("species", {}).get("name")
        if not target:
            continue
        details = child.get("evolution_details", [])
        if not details and form == "regular":
            key = (str(target), "regular")
            grouped[key] = [{}]
            order.append(key)
        for detail in details:
            if not matches_base_form(detail):
                continue
            key = (str(target), target_form(detail, str(target)))
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(detail)

    if not grouped and form == "regular":
        for target, evolved_form in MISSING_CHAIN_EVOLUTIONS.get(species, ()):
            key = (target, evolved_form)
            grouped[key] = [{}]
            order.append(key)

    selected = {key: representative(details) for key, details in grouped.items()}
    exact_levels: dict[tuple[str, str], int] = {}
    for key, detail in selected.items():
        level_details = [
            candidate for candidate in grouped[key] if int(candidate.get("min_level") or 0) > 0
        ]
        explicit_detail = representative(level_details) if level_details else {}
        explicit = int(explicit_detail.get("min_level") or 0)
        learned = learned_move_level(detail)
        if explicit > 0:
            exact_levels[key] = explicit
        elif learned is not None:
            exact_levels[key] = learned

    sibling_level = min(exact_levels.values()) if exact_levels else None
    chain_depth = maximum_depth(root)
    current_depth = len(path) - 1
    if species_json.get("is_baby") or (current_depth == 0 and chain_depth >= 2):
        family_level = 20
    elif current_depth >= 1 and chain_depth >= 2:
        family_level = 36
    else:
        family_level = 30

    previous_explicit = 0
    if current_depth > 0:
        for detail in current.get("evolution_details", []):
            evolved_form = detail.get("evolved_form")
            evolved_name = evolved_form.get("name") if evolved_form else species
            if evolved_name == current_form_name:
                previous_explicit = max(previous_explicit, int(detail.get("min_level") or 0))
    inferred_level = max(sibling_level or family_level, previous_explicit + 10)

    return [
        {
            "species": key[0],
            "form": key[1],
            "min_level": exact_levels.get(key, inferred_level),
        }
        for key in order
    ]


def _encounter_level(
    species_json: JsonObject,
    species: str,
    fetch_json: JsonFetcher | None = None,
) -> int:
    """Return the usual level at which this species enters its evolution chain."""
    chain_url = species_json.get("evolution_chain", {}).get("url")
    fetch = fetch_json or _get_json
    chain_json = fetch(chain_url) if chain_url else None
    root = chain_json.get("chain", {}) if chain_json else {}

    def path_to(node: JsonObject) -> list[JsonObject] | None:
        if node.get("species", {}).get("name") == species:
            return [node]
        for child in node.get("evolves_to", []):
            path = path_to(child)
            if path is not None:
                return [node, *path]
        return None

    path = path_to(root)
    if not path or len(path) == 1:
        return 5
    current = path[-1]
    explicit = [
        int(detail.get("min_level") or 0)
        for detail in current.get("evolution_details", [])
        if int(detail.get("min_level") or 0) > 0
    ]
    if explicit:
        return min(explicit)
    depth = len(path) - 1
    has_later_stage = bool(current.get("evolves_to"))
    if depth == 1 and has_later_stage:
        return 20
    return 36 if depth >= 2 else 30


def _strict_int(value: object) -> int | None:
    """Entero JSON de verdad: ``True`` o ``"5"`` no cuentan como números."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _one_line(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(value.split()) or None


def _level_up_learnset(pokemon_json: JsonObject) -> tuple[str | None, list[list[object]]]:
    """Learnset por nivel del grupo de versión preferido: ``(grupo, [[slug, nivel], …])``.

    Ordena por nivel; el 0 (se aprende al evolucionar) cuenta como 1. Dentro
    de un nivel manda el ``order`` de PokeAPI y después la posición en el
    payload, así que los de evolución, que no traen ``order``, quedan detrás
    de los básicos. Conserva las repeticiones: el dominio se queda con la
    última aparición de cada movimiento.
    """
    groups: dict[str, list[tuple[int, int, int, str]]] = {}
    for index, entry in enumerate(pokemon_json.get("moves") or []):
        if not isinstance(entry, dict):
            continue
        slug = _resource_name(entry.get("move"))
        if slug is None:
            continue
        for detail in entry.get("version_group_details") or []:
            if not isinstance(detail, dict):
                continue
            if _resource_name(detail.get("move_learn_method")) != "level-up":
                continue
            group = _resource_name(detail.get("version_group"))
            level = _strict_int(detail.get("level_learned_at"))
            if group is None or level is None or level < 0:
                continue
            order = _strict_int(detail.get("order"))
            groups.setdefault(group, []).append(
                (max(1, level), sys.maxsize if order is None else order, index, slug)
            )
    if not groups:
        return None, []

    def size(group: str) -> int:
        return len({slug for *_, slug in groups[group]})

    chosen = next(
        (
            group
            for group in LEARNSET_VERSION_PREFERENCE
            if group in groups and size(group) >= MIN_LEARNSET_MOVES
        ),
        None,
    )
    if chosen is None:
        rank = {group: position for position, group in enumerate(LEARNSET_VERSION_PREFERENCE)}
        chosen = min(groups, key=lambda group: (-size(group), rank.get(group, len(rank)), group))
    return chosen, [[slug, level] for level, _, _, slug in sorted(groups[chosen])]


def _localized_name(names: object) -> str | None:
    entries = names if isinstance(names, list) else []
    for lang in ("es", "en"):
        for entry in entries:
            if isinstance(entry, dict) and _resource_name(entry.get("language")) == lang:
                name = _one_line(entry.get("name"))
                if name:
                    return name
    return None


def _move_description(move_json: JsonObject) -> str | None:
    """Flavor text en español (en una línea), si no en inglés y si no el
    ``short_effect`` inglés con su probabilidad ya sustituida."""
    flavor_entries = move_json.get("flavor_text_entries")
    entries = flavor_entries if isinstance(flavor_entries, list) else []
    for lang in ("es", "en"):
        for entry in entries:
            if not isinstance(entry, dict) or _resource_name(entry.get("language")) != lang:
                continue
            text = _one_line(entry.get("flavor_text"))
            if text and not text.lower().startswith(UNUSABLE_MOVE_TEXTS):
                return text
    effect_entries = move_json.get("effect_entries")
    for entry in effect_entries if isinstance(effect_entries, list) else []:
        if not isinstance(entry, dict) or _resource_name(entry.get("language")) != "en":
            continue
        text = _one_line(entry.get("short_effect"))
        if text:
            chance = _strict_int(move_json.get("effect_chance"))
            return text if chance is None else text.replace("$effect_chance", str(chance))
    return None


def _stat_changes(raw: object) -> list[JsonObject]:
    changes: list[JsonObject] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        stat = STAGE_NAME_MAP.get(_resource_name(entry.get("stat")) or "")
        change = _strict_int(entry.get("change"))
        if stat is not None and change is not None:
            changes.append({"stat": stat, "change": change})
    return changes


def _move_record(move_json: JsonObject) -> JsonObject:
    """Registro de caché de ``/move/{slug}`` con las claves exactas de ``Move``.

    Solo traduce; la validación mínima (id, nombre, tipo y PP) es del cliente
    y la de reglas, de ``domain.battle.moves.move_from_record``. Sin ``meta``
    (p. ej. Arremetida) queda como movimiento de daño sin efectos añadidos.
    """
    raw_meta = move_json.get("meta")
    meta: JsonObject = raw_meta if isinstance(raw_meta, dict) else {}
    slug = _resource_name(move_json)
    category = _resource_name(meta.get("category")) or "damage"
    return {
        "slug": slug,
        "name": _localized_name(move_json.get("names")) or slug,
        "type": _resource_name(move_json.get("type")),
        "damage_class": _resource_name(move_json.get("damage_class")),
        "power": _strict_int(move_json.get("power")),
        "accuracy": _strict_int(move_json.get("accuracy")),
        "pp": _strict_int(move_json.get("pp")),
        "priority": _strict_int(move_json.get("priority")) or 0,
        "target": _resource_name(move_json.get("target")) or "selected-pokemon",
        # Versiones antiguas de PokeAPI escribían «damage+ailment».
        "category": category.replace("+", "-"),
        "ailment": _resource_name(meta.get("ailment")) or "none",
        "ailment_chance": _strict_int(meta.get("ailment_chance")) or 0,
        "min_hits": _strict_int(meta.get("min_hits")),
        "max_hits": _strict_int(meta.get("max_hits")),
        "min_turns": _strict_int(meta.get("min_turns")),
        "max_turns": _strict_int(meta.get("max_turns")),
        "drain": _strict_int(meta.get("drain")) or 0,
        "healing": _strict_int(meta.get("healing")) or 0,
        "crit_rate": _strict_int(meta.get("crit_rate")) or 0,
        "flinch_chance": _strict_int(meta.get("flinch_chance")) or 0,
        "stat_changes": _stat_changes(move_json.get("stat_changes")),
        "stat_chance": _strict_int(meta.get("stat_chance")) or 0,
        "description": _move_description(move_json),
    }


def fetch_species_data(species: str, form: str) -> JsonObject | None:
    """Fetch enrichment data for a species (+optional alternate form) from
    PokeAPI. Never raises: returns None if there is no network or the
    species name is unknown. Resolves the default variety via
    /pokemon-species (needed for species like giratina/deoxys/zygarde that
    404 on /pokemon/{name} directly), and for alternate forms tries
    /pokemon/{species}-{form} first, falling back to the base species data
    (marked form_data_exact=False) if that variety doesn't exist on PokeAPI
    (e.g. Paldean Tauros breeds)."""
    from pokedex_cli.infrastructure.pokeapi import PokeApiClient, PokeApiError

    try:
        return PokeApiClient().fetch_species_data(species, form)
    except PokeApiError:
        return None
