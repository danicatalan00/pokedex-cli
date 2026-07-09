import glob
import json
import random
import subprocess
import tomllib
from pathlib import Path

from pokedex_cli.paths import KRABBY_POKEMON_JSON, ensure_dirs

KRABBY_CONFIG_PATH = Path.home() / ".config/krabby/config.toml"
DEFAULT_SHINY_RATE = 1 / 128
CARGO_POKEMON_JSON_GLOB = str(
    Path.home() / ".cargo/registry/src/*/krabby-*/assets/pokemon.json"
)


def get_shiny_rate() -> float:
    try:
        with KRABBY_CONFIG_PATH.open("rb") as f:
            config = tomllib.load(f)
        return float(config["shiny_rate"])
    except Exception:
        return DEFAULT_SHINY_RATE


def parse_generations(spec: str) -> set[int]:
    if "-" in spec:
        start, end = spec.split("-", 1)
        return set(range(int(start), int(end) + 1))
    return {int(part) for part in spec.split(",")}


def _ensure_pokemon_db() -> list[dict] | None:
    if not KRABBY_POKEMON_JSON.exists():
        candidates = sorted(glob.glob(CARGO_POKEMON_JSON_GLOB))
        if not candidates:
            return None
        ensure_dirs()
        KRABBY_POKEMON_JSON.write_bytes(Path(candidates[-1]).read_bytes())
    try:
        return json.loads(KRABBY_POKEMON_JSON.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def list_pool(generations: str) -> list[str]:
    """Base species slugs for the given generation spec, via `krabby list`."""
    result = subprocess.run(
        ["krabby", "list", generations], capture_output=True, text=True, check=True
    )
    return [line for line in result.stdout.splitlines() if line]


def pick_species_form_shiny(generations: str) -> tuple[str, str, bool]:
    """Replicates krabby's own random-selection algorithm (species, then a
    uniform choice between its alternate forms + regular, then a shiny roll),
    using a local copy of krabby's own pokemon.json for full fidelity
    (including mega/gmax/regional forms)."""
    db = _ensure_pokemon_db()
    shiny = random.random() < get_shiny_rate()
    if db is not None:
        wanted_gens = parse_generations(generations)
        pool = [p for p in db if p["gen"] in wanted_gens]
        if pool:
            pokemon = random.choice(pool)
            form = random.choice(pokemon["forms"] + ["regular"])
            return pokemon["slug"], form, shiny
    # Fallback: cargo cache missing, only base forms available.
    pool = list_pool(generations)
    return random.choice(pool), "regular", shiny


def render_sprite(species: str, form: str, shiny: bool, show_title: bool, info: bool) -> None:
    args = ["krabby", "name", species]
    if form != "regular":
        args += ["-f", form]
    if info:
        args.append("-i")
    if shiny:
        args.append("-s")
    if not show_title:
        args.append("--no-title")
    subprocess.run(args, check=True)


def capture_sprite(species: str, form: str, shiny: bool) -> str | None:
    """Devuelve el arte ANSI del sprite (stdout de krabby) como cadena, para
    poder incrustarlo dentro de un layout de rich. None si krabby falla o no
    está instalado."""
    args = ["krabby", "name", species]
    if form != "regular":
        args += ["-f", form]
    if shiny:
        args.append("-s")
    args.append("--no-title")
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return result.stdout.rstrip("\n") or None


def _best_effort_fallback(generations: str) -> None:
    try:
        subprocess.run(
            ["krabby", "random", generations, "--no-title", "-i"], check=False
        )
    except Exception:
        pass


def run_hook(generations: str, write_last_seen) -> None:
    try:
        species, form, shiny = pick_species_form_shiny(generations)
        write_last_seen(species, form, shiny)
        render_sprite(species, form, shiny, show_title=False, info=True)
    except Exception:
        _best_effort_fallback(generations)
