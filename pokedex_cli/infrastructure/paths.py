import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from pokedex_cli.infrastructure.repositories import SQLiteEncounterRepository

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share")) / "pokedex-cli"
DB_PATH = DATA_DIR / "pokedex.db"
LAST_SEEN_PATH = DATA_DIR / "last_seen.json"
KRABBY_POKEMON_JSON = DATA_DIR / "krabby_pokemon.json"
INVENTORY_PATH = DATA_DIR / "inventory.json"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(data, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Serialise a complete read-modify-write operation across processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def write_last_seen(species: str, form: str, shiny: bool, seen_at: str) -> None:
    _encounter_repository().write(
        {
            "species": species,
            "form": form,
            "shiny": shiny,
            "seen_at": seen_at,
            "captured": False,
            "failed_capture_attempts": 0,
            "escape_after_attempts": None,
        }
    )


def _encounter_repository() -> SQLiteEncounterRepository:
    return SQLiteEncounterRepository(DB_PATH, LAST_SEEN_PATH)


def read_last_seen() -> dict[str, Any] | None:
    return _encounter_repository().read()


def mark_last_seen_captured() -> None:
    def mark(data: dict[str, Any] | None) -> None:
        if data is not None:
            data["captured"] = True

    _encounter_repository().update(mark)


def record_last_seen_failed_capture(escape_after_attempts: int) -> tuple[int, int]:
    result = (0, escape_after_attempts)

    def record(data: dict[str, Any] | None) -> None:
        nonlocal result
        if data is None:
            return
        attempts = int(data.get("failed_capture_attempts") or 0) + 1
        escape_after = int(data.get("escape_after_attempts") or escape_after_attempts)
        data["failed_capture_attempts"] = attempts
        data["escape_after_attempts"] = escape_after
        result = attempts, escape_after

    _encounter_repository().update(record)
    return result


def clear_last_seen() -> None:
    _encounter_repository().clear()
