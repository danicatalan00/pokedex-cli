"""Injectable PokeAPI HTTP adapter with typed failures."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

import requests

from pokedex_cli.infrastructure import pokeapi_parsing as parsing
from pokedex_cli.infrastructure.diagnostics import log_failure


class PokeApiError(Exception):
    """Base class for recoverable PokeAPI adapter failures."""


class NotFound(PokeApiError):
    pass


class RateLimited(PokeApiError):
    pass


class ServerFailure(PokeApiError):
    pass


class Timeout(PokeApiError):
    pass


class ConnectionFailure(PokeApiError):
    pass


class InvalidResponse(PokeApiError):
    pass


class Response(Protocol):
    status_code: int

    def json(self) -> object: ...


class HttpSession(Protocol):
    def get(self, url: str, *, timeout: float) -> Response: ...


class SpeciesClient(Protocol):
    def fetch_species_data(self, species: str, form: str) -> dict[str, Any]: ...


class PokeApiClient:
    def __init__(
        self,
        *,
        session: HttpSession | None = None,
        timeout: float = parsing.TIMEOUT,
        base_url: str = parsing.BASE_URL,
        max_retries: int = 1,
        retry_backoff: float = 0.1,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_backoff < 0:
            raise ValueError("retry_backoff must be non-negative")
        self._session = session or requests.Session()
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._sleeper = sleeper

    def _get_json(self, url: str) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                return self._get_json_once(url)
            except (Timeout, ConnectionFailure, ServerFailure):
                if attempt >= self._max_retries:
                    raise
                self._sleeper(self._retry_backoff * (attempt + 1))
        raise AssertionError("unreachable retry state")

    def _get_json_once(self, url: str) -> dict[str, Any]:
        try:
            response = self._session.get(url, timeout=self._timeout)
        except requests.Timeout as error:
            raise Timeout(str(error)) from error
        except requests.ConnectionError as error:
            raise ConnectionFailure(str(error)) from error
        except requests.RequestException as error:
            raise ConnectionFailure(str(error)) from error
        if response.status_code == 404:
            raise NotFound(url)
        if response.status_code == 429:
            raise RateLimited(url)
        if response.status_code >= 500:
            raise ServerFailure(f"HTTP {response.status_code}: {url}")
        if response.status_code != 200:
            raise PokeApiError(f"HTTP {response.status_code}: {url}")
        try:
            payload = response.json()
        except (TypeError, ValueError) as error:
            raise InvalidResponse(f"invalid JSON from {url}") from error
        if not isinstance(payload, dict):
            raise InvalidResponse(f"expected object from {url}")
        return payload

    def fetch_species_data(self, species: str, form: str) -> dict[str, Any]:
        species_json = self._get_json(f"{self._base_url}/pokemon-species/{species}")
        self._validate_species(species_json)
        varieties = species_json["varieties"]
        default_variety = next(
            (variety["pokemon"]["name"] for variety in varieties if variety.get("is_default")),
            species,
        )

        form_data_exact = True
        variety_name = default_variety
        if form != "regular":
            candidate = f"{species}-{form}"
            try:
                self._get_json(f"{self._base_url}/pokemon/{candidate}")
                variety_name = candidate
            except NotFound:
                form_data_exact = False

        pokemon_json = self._get_json(f"{self._base_url}/pokemon/{variety_name}")
        self._validate_pokemon(pokemon_json)
        return {
            "pokedex_id": species_json["id"],
            "capture_rate": species_json["capture_rate"],
            "is_legendary": bool(species_json.get("is_legendary")),
            "is_mythical": bool(species_json.get("is_mythical")),
            "gender_rate": species_json.get("gender_rate"),
            "generation": species_json.get("generation", {}).get("name"),
            "growth_rate": species_json.get("growth_rate", {}).get("name"),
            "level_evolutions": parsing._level_evolutions(
                species_json, pokemon_json, species, form, self._get_json
            ),
            "flavor_text": parsing._flavor_text(species_json),
            "form_data_exact": form_data_exact,
            **parsing._pokemon_stats_and_types(pokemon_json),
        }

    @staticmethod
    def _validate_species(payload: dict[str, Any]) -> None:
        if not isinstance(payload.get("id"), int):
            raise InvalidResponse("species id is missing")
        if not isinstance(payload.get("capture_rate"), int):
            raise InvalidResponse("capture rate is missing")
        if not isinstance(payload.get("varieties"), list):
            raise InvalidResponse("species varieties are missing")

    @staticmethod
    def _validate_pokemon(payload: dict[str, Any]) -> None:
        if not isinstance(payload.get("types"), list):
            raise InvalidResponse("pokemon types are missing")
        if not isinstance(payload.get("stats"), list):
            raise InvalidResponse("pokemon stats are missing")


class TolerantPokeApiClient:
    """Translate typed adapter failures into the application's offline fallback."""

    def __init__(
        self,
        client: SpeciesClient | None = None,
        on_error: Callable[[str, BaseException], None] = log_failure,
    ) -> None:
        self._client = client or PokeApiClient()
        self._on_error = on_error

    def fetch_species_data(self, species: str, form: str) -> dict[str, Any] | None:
        try:
            return self._client.fetch_species_data(species, form)
        except PokeApiError as error:
            self._on_error("PokeAPI fallback", error)
            return None
