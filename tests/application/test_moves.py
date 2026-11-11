"""Caché de movimientos: cache-aside con lectura de una vez y descargas por ``mapper``."""

from datetime import datetime, timezone

from pokedex_cli.application.moves import GetMoveData
from pokedex_cli.domain.battle.moves import FALLBACK_BY_TYPE, TACKLE, move_to_record

NOW = datetime(2026, 11, 11, 9, 30, tzinfo=timezone.utc)
EMBER = FALLBACK_BY_TYPE["fire"]
WATER_GUN = FALLBACK_BY_TYPE["water"]


class Cache:
    def __init__(self, records=None):
        self.records = dict(records or {})
        self.reads: list[list[str]] = []
        self.saved: list[tuple[str, dict, str]] = []

    def get_many(self, slugs):
        wanted = list(slugs)
        self.reads.append(wanted)
        return {slug: self.records[slug] for slug in wanted if slug in self.records}

    def put(self, slug, record, fetched_at):
        self.saved.append((slug, dict(record), fetched_at))
        self.records[slug] = dict(record)


class Api:
    def __init__(self, records=None):
        self.records = dict(records or {})
        self.calls: list[str] = []

    def fetch_move_data(self, slug):
        self.calls.append(slug)
        return self.records.get(slug)


def use_case(cache, api, **kwargs) -> GetMoveData:
    return GetMoveData(cache=cache, api=api, clock=lambda: NOW, **kwargs)


def test_cached_moves_never_reach_the_api() -> None:
    cache = Cache({"tackle": move_to_record(TACKLE), "ember": move_to_record(EMBER)})
    api = Api()

    moves = use_case(cache, api).execute_many(["tackle", "ember"])

    assert moves == {"tackle": TACKLE, "ember": EMBER}
    assert api.calls == []
    assert cache.saved == []


def test_reads_the_cache_once_without_duplicates_and_fetches_only_what_is_missing() -> None:
    cache = Cache({"tackle": move_to_record(TACKLE)})
    api = Api({"ember": move_to_record(EMBER), "water-gun": move_to_record(WATER_GUN)})

    moves = use_case(cache, api).execute_many(["tackle", "ember", "tackle", "water-gun", ""])

    assert cache.reads == [["tackle", "ember", "water-gun"]]
    assert api.calls == ["ember", "water-gun"]
    assert moves == {"tackle": TACKLE, "ember": EMBER, "water-gun": WATER_GUN}
    assert [(slug, fetched_at) for slug, _, fetched_at in cache.saved] == [
        ("ember", NOW.isoformat()),
        ("water-gun", NOW.isoformat()),
    ]


def test_downloads_go_through_the_injected_mapper_in_order() -> None:
    cache = Cache()
    api = Api({"ember": move_to_record(EMBER), "tackle": move_to_record(TACKLE)})
    batches: list[list[str]] = []

    def mapper(function, slugs):
        batches.append(list(slugs))
        # Un mapper paralelo devuelve en el orden de entrada aunque termine en otro.
        return list(reversed([function(slug) for slug in reversed(slugs)]))

    moves = use_case(cache, api, mapper=mapper).execute_many(["ember", "tackle"])

    assert batches == [["ember", "tackle"]]
    assert moves == {"ember": EMBER, "tackle": TACKLE}


def test_offline_or_invalid_records_are_neither_saved_nor_returned() -> None:
    broken = {**move_to_record(EMBER), "slug": "flamethrower", "damage_class": None}
    cache = Cache()
    api = Api({"flamethrower": broken, "ember": move_to_record(EMBER)})

    moves = use_case(cache, api).execute_many(["flamethrower", "ember", "hyper-beam"])

    assert moves == {"ember": EMBER}
    assert [slug for slug, _, _ in cache.saved] == ["ember"]


def test_a_record_of_another_move_cannot_poison_the_cache_key() -> None:
    cache = Cache()
    api = Api({"ember": move_to_record(TACKLE)})

    assert use_case(cache, api).execute("ember") is None
    assert cache.saved == []


def test_an_unreadable_cached_record_is_downloaded_again_and_replaced() -> None:
    cache = Cache({"ember": {"slug": "ember", "type": "fire", "pp": "lots"}})
    api = Api({"ember": move_to_record(EMBER)})

    assert use_case(cache, api).execute("ember") == EMBER
    assert api.calls == ["ember"]
    assert cache.records["ember"] == move_to_record(EMBER)


def test_empty_requests_touch_nothing() -> None:
    cache = Cache()
    api = Api()

    assert use_case(cache, api).execute_many([]) == {}
    assert cache.reads == []


def test_naive_clock_is_stored_as_utc() -> None:
    cache = Cache()
    api = Api({"ember": move_to_record(EMBER)})
    naive = datetime(2026, 11, 11, 9, 30)

    GetMoveData(cache=cache, api=api, clock=lambda: naive).execute("ember")

    assert cache.saved[0][2] == "2026-11-11T09:30:00+00:00"
