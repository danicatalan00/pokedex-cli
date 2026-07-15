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
        **stats,
    }


def _level_evolutions(
    species_json: JsonObject,
    species: str,
    form: str,
    fetch_json: JsonFetcher | None = None,
) -> list[JsonObject]:
    chain_url = species_json.get("evolution_chain", {}).get("url")
    fetch = fetch_json or _get_json
    chain_json = fetch(chain_url) if chain_url else None
    if not chain_json:
        return []

    current_form_name = species if form == "regular" else f"{species}-{form}"
    # Una misma evolucion puede tener reglas distintas por version. Conservamos
    # la primera version cronologica (Rojo/Azul para la Gen I), no el nivel mas
    # bajo introducido años despues.
    found: dict[tuple[str, str], tuple[int, JsonObject]] = {}

    def walk(node: JsonObject) -> None:
        if node.get("species", {}).get("name") == species:
            for child in node.get("evolves_to", []):
                target = child.get("species", {}).get("name")
                for detail in child.get("evolution_details", []):
                    trigger = detail.get("trigger", {}).get("name")
                    extra_conditions = (
                        "gender",
                        "held_item",
                        "item",
                        "known_move",
                        "known_move_type",
                        "location",
                        "min_affection",
                        "min_beauty",
                        "min_happiness",
                        "min_damage_taken",
                        "min_move_count",
                        "min_steps",
                        "near_special_rock",
                        "needs_multiplayer",
                        "needs_overworld_rain",
                        "party_species",
                        "party_type",
                        "region",
                        "relative_physical_stats",
                        "time_of_day",
                        "trade_species",
                        "turn_upside_down",
                        "used_move",
                    )
                    is_pure_level = not any(detail.get(key) for key in extra_conditions)
                    base_form = detail.get("base_form")
                    base_form_name = base_form.get("name") if base_form else None
                    matches_form = (
                        form == "regular" and base_form_name is None
                    ) or base_form_name == current_form_name
                    if (
                        trigger != "level-up"
                        or not detail.get("min_level")
                        or not matches_form
                        or not is_pure_level
                    ):
                        continue
                    evolved_form = detail.get("evolved_form")
                    evolved_name = evolved_form.get("name") if evolved_form else target
                    target_form = "regular"
                    if evolved_name and target and evolved_name.startswith(target + "-"):
                        target_form = evolved_name[len(target) + 1 :]
                    option = {
                        "species": target,
                        "form": target_form,
                        "min_level": int(detail["min_level"]),
                    }
                    version_url = detail.get("version_group", {}).get("url", "")
                    match = re.search(r"/(\d+)/?$", version_url)
                    version_order = int(match.group(1)) if match else 10_000
                    key = (target, target_form)
                    if target and (key not in found or version_order < found[key][0]):
                        found[key] = (version_order, option)
        for child in node.get("evolves_to", []):
            walk(child)

    walk(chain_json.get("chain", {}))
    return [entry[1] for entry in sorted(found.values(), key=lambda entry: entry[0])]


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
