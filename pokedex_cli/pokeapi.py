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


def _get_json(url: str) -> dict | None:
    try:
        response = requests.get(url, timeout=TIMEOUT)
    except requests.exceptions.RequestException:
        return None
    if response.status_code != 200:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _flavor_text(species_json: dict) -> str | None:
    entries = species_json.get("flavor_text_entries", [])
    for lang in ("es", "en"):
        for entry in entries:
            if entry.get("language", {}).get("name") == lang:
                return " ".join(entry["flavor_text"].split())
    return None


def _pokemon_stats_and_types(pokemon_json: dict) -> dict:
    types = [t["type"]["name"] for t in sorted(pokemon_json["types"], key=lambda t: t["slot"])]
    stats = {}
    for stat in pokemon_json["stats"]:
        key = STAT_NAME_MAP.get(stat["stat"]["name"])
        if key:
            stats[key] = stat["base_stat"]
    return {"types": types, **stats}


def fetch_species_data(species: str, form: str) -> dict | None:
    """Fetch enrichment data for a species (+optional alternate form) from
    PokeAPI. Never raises: returns None if there is no network or the
    species name is unknown. Resolves the default variety via
    /pokemon-species (needed for species like giratina/deoxys/zygarde that
    404 on /pokemon/{name} directly), and for alternate forms tries
    /pokemon/{species}-{form} first, falling back to the base species data
    (marked form_data_exact=False) if that variety doesn't exist on PokeAPI
    (e.g. Paldean Tauros breeds)."""
    species_json = _get_json(f"{BASE_URL}/pokemon-species/{species}")
    if species_json is None:
        return None

    varieties = species_json.get("varieties", [])
    default_variety = next(
        (v["pokemon"]["name"] for v in varieties if v.get("is_default")), species
    )

    form_data_exact = True
    variety_name = default_variety
    if form != "regular":
        candidate = f"{species}-{form}"
        candidate_json = _get_json(f"{BASE_URL}/pokemon/{candidate}")
        if candidate_json is not None:
            variety_name = candidate
        else:
            form_data_exact = False

    pokemon_json = _get_json(f"{BASE_URL}/pokemon/{variety_name}")
    if pokemon_json is None:
        return None

    return {
        "pokedex_id": species_json.get("id"),
        "capture_rate": species_json.get("capture_rate"),
        "is_legendary": bool(species_json.get("is_legendary")),
        "is_mythical": bool(species_json.get("is_mythical")),
        "generation": species_json.get("generation", {}).get("name"),
        "flavor_text": _flavor_text(species_json),
        "form_data_exact": form_data_exact,
        **_pokemon_stats_and_types(pokemon_json),
    }
