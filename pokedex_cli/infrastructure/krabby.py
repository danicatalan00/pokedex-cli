"""Krabby subprocess and cache adapter."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

_ANSI_SEQUENCE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class KrabbyClient:
    def __init__(
        self,
        *,
        runner: Callable[..., Any] = subprocess.run,
        pokemon_json_path: Path,
    ) -> None:
        self._runner = runner
        self._pokemon_json_path = pokemon_json_path

    def capture_sprite(self, species: str, form: str, shiny: bool) -> str | None:
        args = ["krabby", "name", species]
        if form != "regular":
            args += ["-f", form]
        if shiny:
            args.append("-s")
        args.append("--no-title")
        try:
            result = self._runner(
                args,
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        sprite = str(result.stdout).rstrip("\n")
        if not sprite or "\x1b" in _ANSI_SEQUENCE.sub("", sprite):
            return None
        return sprite

    def render_sprite(
        self,
        species: str,
        form: str,
        shiny: bool,
        *,
        show_title: bool,
        info: bool,
    ) -> None:
        args = ["krabby", "name", species]
        if form != "regular":
            args += ["-f", form]
        if info:
            args.append("-i")
        if shiny:
            args.append("-s")
        if not show_title:
            args.append("--no-title")
        self._runner(args, check=True, timeout=5)

    def load_database(self) -> list[dict[str, Any]] | None:
        try:
            payload = json.loads(self._pokemon_json_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        if not isinstance(payload, list) or not all(isinstance(entry, dict) for entry in payload):
            return None
        return payload
