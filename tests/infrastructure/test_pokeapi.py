from dataclasses import dataclass

import pytest
import requests

from pokedex_cli.infrastructure.pokeapi import (
    ConnectionFailure,
    InvalidResponse,
    NotFound,
    PokeApiClient,
    RateLimited,
    ServerFailure,
    Timeout,
    TolerantPokeApiClient,
)


@dataclass
class FakeResponse:
    status_code: int
    payload: object

    def json(self) -> object:
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse] | None = None, error=None) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, *, timeout: float) -> FakeResponse:
        self.calls.append((url, timeout))
        if self.error is not None:
            raise self.error
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def species_payload() -> dict:
    return {
        "id": 25,
        "capture_rate": 190,
        "is_legendary": False,
        "is_mythical": False,
        "generation": {"name": "generation-i"},
        "growth_rate": {"name": "medium"},
        "varieties": [{"is_default": True, "pokemon": {"name": "pikachu"}}],
        "flavor_text_entries": [],
        "evolution_chain": {},
    }


def pokemon_payload() -> dict:
    return {
        "base_experience": 112,
        "types": [{"slot": 1, "type": {"name": "electric"}}],
        "stats": [
            {"base_stat": 35, "stat": {"name": "hp"}},
            {"base_stat": 90, "stat": {"name": "speed"}},
        ],
    }


def test_success_uses_injected_session_and_explicit_timeout() -> None:
    session = FakeSession(
        [FakeResponse(200, species_payload()), FakeResponse(200, pokemon_payload())]
    )
    client = PokeApiClient(session=session, timeout=1.25)

    result = client.fetch_species_data("pikachu", "regular")

    assert result["pokedex_id"] == 25
    assert result["types"] == ["electric"]
    assert result["spe"] == 90
    assert [timeout for _, timeout in session.calls] == [1.25, 1.25]


@pytest.mark.parametrize(
    ("status", "error"),
    [(404, NotFound), (429, RateLimited), (500, ServerFailure)],
)
def test_http_failures_are_typed(status: int, error: type[Exception]) -> None:
    responses = [FakeResponse(status, {})] * (2 if status >= 500 else 1)
    client = PokeApiClient(session=FakeSession(responses), sleeper=lambda delay: None)
    with pytest.raises(error):
        client.fetch_species_data("missingno", "regular")


@pytest.mark.parametrize(
    ("transport_error", "typed_error"),
    [
        (requests.Timeout("slow"), Timeout),
        (requests.ConnectionError("offline"), ConnectionFailure),
    ],
)
def test_transport_failures_are_typed(transport_error, typed_error) -> None:
    client = PokeApiClient(session=FakeSession(error=transport_error))
    with pytest.raises(typed_error):
        client.fetch_species_data("pikachu", "regular")


@pytest.mark.parametrize(
    "payload",
    [{}, [], ValueError("invalid json")],
)
def test_incomplete_or_invalid_json_is_rejected(payload: object) -> None:
    client = PokeApiClient(session=FakeSession([FakeResponse(200, payload)]))
    with pytest.raises(InvalidResponse):
        client.fetch_species_data("pikachu", "regular")


def test_evolution_contract_keeps_only_pure_level_rules() -> None:
    species = species_payload()
    species["evolution_chain"] = {"url": "https://example.test/chain/1"}
    chain = {
        "chain": {
            "species": {"name": "pikachu"},
            "evolves_to": [
                {
                    "species": {"name": "raichu"},
                    "evolution_details": [
                        {
                            "trigger": {"name": "level-up"},
                            "min_level": 20,
                            "version_group": {"url": "https://example.test/version/1"},
                        },
                        {
                            "trigger": {"name": "use-item"},
                            "min_level": None,
                            "item": {"name": "thunder-stone"},
                        },
                    ],
                    "evolves_to": [],
                }
            ],
        }
    }
    session = FakeSession(
        [
            FakeResponse(200, species),
            FakeResponse(200, pokemon_payload()),
            FakeResponse(200, chain),
        ]
    )
    result = PokeApiClient(session=session).fetch_species_data("pikachu", "regular")
    assert result["level_evolutions"] == [{"species": "raichu", "form": "regular", "min_level": 20}]


@pytest.mark.parametrize(
    "transient",
    [FakeResponse(503, {}), requests.Timeout("slow"), requests.ConnectionError("offline")],
)
def test_retryable_failure_uses_bounded_backoff_then_recovers(transient) -> None:
    session = FakeSession(
        [transient, FakeResponse(200, species_payload()), FakeResponse(200, pokemon_payload())]
    )
    sleeps: list[float] = []
    client = PokeApiClient(
        session=session,
        max_retries=1,
        retry_backoff=0.25,
        sleeper=sleeps.append,
    )

    result = client.fetch_species_data("pikachu", "regular")

    assert result["pokedex_id"] == 25
    assert sleeps == [0.25]
    assert len(session.calls) == 3


@pytest.mark.parametrize("status", [404, 429])
def test_permanent_and_rate_limit_failures_are_not_retried(status: int) -> None:
    session = FakeSession([FakeResponse(status, {})])
    client = PokeApiClient(session=session, max_retries=3, sleeper=lambda delay: None)

    with pytest.raises((NotFound, RateLimited)):
        client.fetch_species_data("pikachu", "regular")

    assert len(session.calls) == 1


def test_tolerant_boundary_turns_typed_failure_into_offline_fallback() -> None:
    class FailingClient:
        def fetch_species_data(self, species: str, form: str) -> dict:
            raise Timeout("offline")

    assert (
        TolerantPokeApiClient(client=FailingClient()).fetch_species_data("pikachu", "regular")
        is None
    )
