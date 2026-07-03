import json
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share")) / "pokedex-cli"
DB_PATH = DATA_DIR / "pokedex.db"
LAST_SEEN_PATH = DATA_DIR / "last_seen.json"
KRABBY_POKEMON_JSON = DATA_DIR / "krabby_pokemon.json"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, data: dict) -> None:
    ensure_dirs()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data))
    os.replace(tmp_path, path)


def write_last_seen(species: str, form: str, shiny: bool, seen_at: str) -> None:
    _atomic_write_json(
        LAST_SEEN_PATH,
        {
            "species": species,
            "form": form,
            "shiny": shiny,
            "seen_at": seen_at,
            "captured": False,
        },
    )


def read_last_seen() -> dict | None:
    try:
        return json.loads(LAST_SEEN_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def mark_last_seen_captured() -> None:
    data = read_last_seen()
    if data is None:
        return
    data["captured"] = True
    _atomic_write_json(LAST_SEEN_PATH, data)
