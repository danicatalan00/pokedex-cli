"""Opt-in diagnostic logging that can never break normal CLI execution."""

from __future__ import annotations

import os
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

DIAGNOSTIC_LOG_ENV = "POKEDEX_DIAGNOSTIC_LOG"


def log_failure(
    context: str,
    error: BaseException,
    *,
    destination: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    """Append a traceback only when an explicit destination is configured."""
    raw_destination = os.environ.get(DIAGNOSTIC_LOG_ENV)
    path = destination or (Path(raw_destination).expanduser() if raw_destination else None)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = clock().astimezone(timezone.utc).isoformat()
        details = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        with path.open("a", encoding="utf-8") as log:
            log.write(f"[{timestamp}] {context}\n{details}\n")
    except OSError:
        pass
