"""Movimientos de PokeAPI (``/move/{slug}``) convertidos al registro de caché.

Las fixtures son recortes de payloads reales (Constricción, Arremetida,
Gruñido, Forcejeo): solo los campos que el parser lee, en el mismo formato.
"""

import copy
import json
from dataclasses import dataclass, fields

import pytest

from pokedex_cli.domain.battle.model import DamageClass, Move, StatChange
from pokedex_cli.infrastructure.pokeapi import (
    InvalidResponse,
    NotFound,
    PokeApiClient,
    ServerFailure,
    Timeout,
    TolerantPokeApiClient,
)
from pokedex_cli.infrastructure.pokeapi_parsing import _move_record

UNUSABLE_ES = (
    "Este movimiento no se puede usar, por lo que sería mejor olvidarlo, aunque eso "
    "implique que no se pueda recordar posteriormente."
)
UNUSABLE_EN = (
    "This move can’t be used. It’s recommended that this move is forgotten. Once "
    "forgotten, this move can’t be remembered."
)


def resource(name: str) -> dict:
    return {"name": name, "url": f"https://pokeapi.co/api/v2/resource/{name}/"}


def localized(lang: str, name: str) -> dict:
    return {"language": resource(lang), "name": name}


def flavor(lang: str, text: str, group: str = "x-y") -> dict:
    return {"flavor_text": text, "language": resource(lang), "version_group": resource(group)}


def meta(**overrides: object) -> dict:
    values: dict = {
        "ailment": resource("none"),
        "category": resource("damage"),
        "min_hits": None,
        "max_hits": None,
        "min_turns": None,
        "max_turns": None,
        "drain": 0,
        "healing": 0,
        "crit_rate": 0,
        "ailment_chance": 0,
        "flinch_chance": 0,
        "stat_chance": 0,
    }
    values.update(overrides)
    return values


def wrap_payload() -> dict:
    return {
        "id": 35,
        "name": "wrap",
        "accuracy": 90,
        "power": 15,
        "pp": 20,
        "priority": 0,
        "effect_chance": 100,
        "damage_class": resource("physical"),
        "type": resource("normal"),
        "target": resource("selected-pokemon"),
        "meta": meta(
            ailment=resource("trap"),
            category=resource("damage-ailment"),
            min_turns=2,
            max_turns=4,
            ailment_chance=100,
        ),
        "stat_changes": [],
        "names": [
            localized("ja", "まきつく"),
            localized("en", "Wrap"),
            localized("es", "Constricción"),
        ],
        "flavor_text_entries": [
            flavor("en", "Squeezes the foe\nfor 2-5 turns.", "gold-silver"),
            flavor("es", "Oprime al objetivo de cuatro a cinco turnos con\nramas o con su cuerpo."),
            flavor("es", UNUSABLE_ES, "sword-shield"),
        ],
        "effect_entries": [
            {
                "language": resource("en"),
                "short_effect": "Prevents the target from fleeing and inflicts damage for 2-5 turns.",
            }
        ],
    }


def headlong_rush_payload() -> dict:
    # Leyendas: Arceus. PokeAPI no le da ``meta`` ni textos en español.
    return {
        "id": 838,
        "name": "headlong-rush",
        "accuracy": 100,
        "power": 120,
        "pp": 5,
        "priority": 0,
        "effect_chance": None,
        "damage_class": resource("physical"),
        "type": resource("ground"),
        "target": resource("selected-pokemon"),
        "meta": None,
        "stat_changes": [],
        "names": [localized("es", "Arremetida"), localized("en", "Headlong Rush")],
        "flavor_text_entries": [
            flavor(
                "en",
                "The user smashes into the target in a full-body tackle.\nThis also lowers the "
                "user’s defensive stats.",
                "legends-arceus",
            ),
        ],
        "effect_entries": [],
    }


def growl_payload() -> dict:
    return {
        "id": 45,
        "name": "growl",
        "accuracy": 100,
        "power": None,
        "pp": 40,
        "priority": 0,
        "effect_chance": None,
        "damage_class": resource("status"),
        "type": resource("normal"),
        "target": resource("all-opponents"),
        "meta": meta(category=resource("net-good-stats")),
        "stat_changes": [{"change": -1, "stat": resource("attack")}],
        "names": [localized("es", "Gruñido")],
        "flavor_text_entries": [
            flavor("es", "Dulce gruñido que reduce el Ataque del contrincante.")
        ],
        "effect_entries": [],
    }


def struggle_payload() -> dict:
    return {
        "id": 165,
        "name": "struggle",
        "accuracy": None,
        "power": 50,
        "pp": 1,
        "priority": 0,
        "damage_class": resource("physical"),
        "type": resource("normal"),
        "target": resource("random-opponent"),
        "meta": meta(healing=-25),
        "stat_changes": [],
        "names": [localized("es", "Forcejeo")],
        "flavor_text_entries": [],
        "effect_entries": [],
    }


def test_wrap_becomes_the_exact_cache_record() -> None:
    assert _move_record(wrap_payload()) == {
        "slug": "wrap",
        "name": "Constricción",
        "type": "normal",
        "damage_class": "physical",
        "power": 15,
        "accuracy": 90,
        "pp": 20,
        "priority": 0,
        "target": "selected-pokemon",
        "category": "damage-ailment",
        "ailment": "trap",
        "ailment_chance": 100,
        "min_hits": None,
        "max_hits": None,
        "min_turns": 2,
        "max_turns": 4,
        "drain": 0,
        "healing": 0,
        "crit_rate": 0,
        "flinch_chance": 0,
        "stat_changes": [],
        "stat_chance": 0,
        "description": "Oprime al objetivo de cuatro a cinco turnos con ramas o con su cuerpo.",
    }


@pytest.mark.parametrize(
    "payload",
    [wrap_payload(), headlong_rush_payload(), growl_payload(), struggle_payload()],
    ids=["wrap", "headlong-rush", "growl", "struggle"],
)
def test_record_has_exactly_the_move_fields_and_survives_json(payload: dict) -> None:
    record = _move_record(payload)

    assert list(record) == [field.name for field in fields(Move)]
    assert json.loads(json.dumps(record)) == record
    move = Move(
        **{
            **record,
            "damage_class": DamageClass(record["damage_class"]),
            "stat_changes": tuple(StatChange(**change) for change in record["stat_changes"]),
        }
    )
    assert move.slug == payload["name"]


def test_missing_meta_means_a_plain_damaging_move() -> None:
    record = _move_record(headlong_rush_payload())

    assert record["name"] == "Arremetida"
    assert (record["power"], record["accuracy"], record["pp"]) == (120, 100, 5)
    assert record["category"] == "damage"
    assert record["ailment"] == "none"
    assert [record[key] for key in ("min_hits", "max_hits", "min_turns", "max_turns")] == [None] * 4
    assert [
        record[key]
        for key in (
            "ailment_chance",
            "drain",
            "healing",
            "crit_rate",
            "flinch_chance",
            "stat_chance",
        )
    ] == [0] * 6
    assert record["description"] == (
        "The user smashes into the target in a full-body tackle. This also lowers the "
        "user’s defensive stats."
    )


def test_status_move_keeps_null_power_target_and_domain_stat_keys() -> None:
    record = _move_record(growl_payload())

    assert (record["damage_class"], record["power"], record["accuracy"]) == ("status", None, 100)
    assert record["target"] == "all-opponents"
    assert record["category"] == "net-good-stats"
    assert record["stat_changes"] == [{"stat": "atk", "change": -1}]


def test_negative_healing_and_perfect_accuracy_are_preserved() -> None:
    record = _move_record(struggle_payload())

    assert record["healing"] == -25
    assert record["accuracy"] is None
    assert record["description"] is None


def test_recoil_is_kept_as_negative_drain() -> None:
    # Doble Filo: PokeAPI expresa el retroceso como drenaje negativo.
    payload = wrap_payload()
    payload.update(name="double-edge", power=120, accuracy=100, pp=15, meta=meta(drain=-33))
    payload["names"] = [localized("es", "Doble Filo")]

    record = _move_record(payload)

    assert (record["name"], record["category"]) == ("Doble Filo", "damage")
    assert (record["drain"], record["healing"]) == (-33, 0)


def test_every_pokeapi_stat_maps_to_a_domain_stage_key() -> None:
    payload = growl_payload()
    payload["stat_changes"] = [
        {"change": -1, "stat": resource("attack")},
        {"change": 1, "stat": resource("defense")},
        {"change": 2, "stat": resource("special-attack")},
        {"change": -2, "stat": resource("special-defense")},
        {"change": 1, "stat": resource("speed")},
        {"change": -1, "stat": resource("accuracy")},
        {"change": 1, "stat": resource("evasion")},
        {"change": 1, "stat": resource("hp")},
        {"change": "1", "stat": resource("speed")},
        {"change": 1},
    ]

    assert _move_record(payload)["stat_changes"] == [
        {"stat": "atk", "change": -1},
        {"stat": "def", "change": 1},
        {"stat": "spa", "change": 2},
        {"stat": "spd", "change": -2},
        {"stat": "spe", "change": 1},
        {"stat": "accuracy", "change": -1},
        {"stat": "evasion", "change": 1},
    ]


def test_legacy_plus_category_is_normalised_to_dashes() -> None:
    payload = wrap_payload()
    payload["meta"]["category"] = resource("damage+ailment")

    assert _move_record(payload)["category"] == "damage-ailment"


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        ([localized("en", "Wrap"), localized("es", "Constricción")], "Constricción"),
        ([localized("ja", "まきつく"), localized("en", "Wrap")], "Wrap"),
        ([localized("ja", "まきつく")], "wrap"),
        ([], "wrap"),
    ],
)
def test_name_prefers_spanish_then_english_then_slug(names: list, expected: str) -> None:
    payload = wrap_payload()
    payload["names"] = names

    assert _move_record(payload)["name"] == expected


@pytest.mark.parametrize(
    ("entries", "short_effect", "expected"),
    [
        (
            [flavor("en", "English."), flavor("es", "Primero.", "x-y"), flavor("es", "Luego.")],
            None,
            "Primero.",
        ),
        (
            [flavor("es", UNUSABLE_ES), flavor("en", UNUSABLE_EN), flavor("en", "Squeezes.")],
            None,
            "Squeezes.",
        ),
        (
            [],
            "Has a $effect_chance% chance to burn the target.",
            "Has a 10% chance to burn the target.",
        ),
        ([flavor("fr", "Texte.")], None, None),
    ],
    ids=["first-spanish", "skips-unusable", "short-effect", "nothing"],
)
def test_description_prefers_spanish_flavor_then_english_then_short_effect(
    entries: list, short_effect: str | None, expected: str | None
) -> None:
    payload = wrap_payload()
    payload["effect_chance"] = 10
    payload["flavor_text_entries"] = entries
    payload["effect_entries"] = (
        [] if short_effect is None else [{"language": resource("en"), "short_effect": short_effect}]
    )

    assert _move_record(payload)["description"] == expected


@dataclass
class FakeResponse:
    status_code: int
    payload: object

    def json(self) -> object:
        return self.payload


class FakeSession:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, *, timeout: float) -> FakeResponse:
        self.calls.append((url, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        assert isinstance(response, FakeResponse)
        return response


def test_client_fetches_one_move_with_explicit_timeout() -> None:
    session = FakeSession(FakeResponse(200, wrap_payload()))
    client = PokeApiClient(session=session, timeout=1.5, base_url="https://example.test/api/")

    record = client.fetch_move_data("wrap")

    assert record == _move_record(wrap_payload())
    assert session.calls == [("https://example.test/api/move/wrap", 1.5)]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", None),
        ("id", "35"),
        ("name", None),
        ("name", ""),
        ("type", None),
        ("type", {"name": None}),
        ("pp", None),
        ("pp", "20"),
    ],
)
def test_client_rejects_a_move_without_identity_type_or_pp(field: str, value: object) -> None:
    payload = wrap_payload()
    payload[field] = value
    client = PokeApiClient(session=FakeSession(FakeResponse(200, payload)))

    with pytest.raises(InvalidResponse):
        client.fetch_move_data("wrap")


def test_client_types_http_failures_and_retries_server_errors() -> None:
    with pytest.raises(NotFound):
        PokeApiClient(session=FakeSession(FakeResponse(404, {}))).fetch_move_data("missingno")

    session = FakeSession(FakeResponse(503, {}), FakeResponse(503, {}))
    client = PokeApiClient(session=session, max_retries=1, sleeper=lambda delay: None)
    with pytest.raises(ServerFailure):
        client.fetch_move_data("wrap")
    assert len(session.calls) == 2


class MovesOnlyClient:
    def __init__(self, result: dict | BaseException) -> None:
        self.result = result
        self.requested: list[str] = []

    def fetch_species_data(self, species: str, form: str) -> dict:
        raise AssertionError("species are not requested here")

    def fetch_move_data(self, slug: str) -> dict:
        self.requested.append(slug)
        if isinstance(self.result, BaseException):
            raise self.result
        return copy.deepcopy(self.result)


def test_tolerant_client_returns_the_move_record() -> None:
    record = _move_record(wrap_payload())
    client = MovesOnlyClient(record)

    assert TolerantPokeApiClient(client=client).fetch_move_data("wrap") == record
    assert client.requested == ["wrap"]


def test_tolerant_client_turns_a_typed_failure_into_offline_fallback() -> None:
    failures: list[tuple[str, BaseException]] = []
    tolerant = TolerantPokeApiClient(
        client=MovesOnlyClient(Timeout("offline")),
        on_error=lambda context, error: failures.append((context, error)),
    )

    assert tolerant.fetch_move_data("wrap") is None
    assert failures[0][0] == "PokeAPI move fallback"
    assert isinstance(failures[0][1], Timeout)


def test_species_only_doubles_keep_working_and_have_no_moves() -> None:
    class SpeciesOnlyClient:
        def fetch_species_data(self, species: str, form: str) -> dict:
            return {"pokedex_id": 25}

    tolerant = TolerantPokeApiClient(
        client=SpeciesOnlyClient(), on_error=lambda context, error: pytest.fail(context)
    )

    assert tolerant.fetch_species_data("pikachu", "regular") == {"pokedex_id": 25}
    assert tolerant.fetch_move_data("wrap") is None
