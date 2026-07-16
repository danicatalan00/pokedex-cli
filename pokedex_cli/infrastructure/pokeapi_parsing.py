import re
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
