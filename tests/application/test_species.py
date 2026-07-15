from datetime import datetime, timezone

from pokedex_cli.application.species import GetSpeciesData

NOW = datetime(2026, 7, 15, 10, tzinfo=timezone.utc)


class Cache:
    def __init__(self, initial=None):
        self.values = dict(initial or {})
        self.saved = []

    def get(self, species, form):
        return self.values.get((species, form))

    def put(self, species, form, data, fetched_at):
        self.saved.append((species, form, data, fetched_at))
        self.values[(species, form)] = {**data, "fetched_at": fetched_at}


class Api:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def fetch_species_data(self, species, form):
        self.calls.append((species, form))
        return self.result


def test_cache_hit_never_calls_api():
    cached = {"pokedex_id": 25}
    cache = Cache({("pikachu", "regular"): cached})
    api = Api({"pokedex_id": 999})

    result = GetSpeciesData(cache=cache, api=api, clock=lambda: NOW).execute("pikachu", "regular")

    assert result is cached
    assert api.calls == []
    assert cache.saved == []


def test_cache_miss_fetches_persists_and_returns_canonical_cached_value():
    cache = Cache()
    payload = {"pokedex_id": 25, "types": ["electric"]}
    api = Api(payload)

    result = GetSpeciesData(cache=cache, api=api, clock=lambda: NOW).execute("pikachu", "regular")

    assert api.calls == [("pikachu", "regular")]
    assert cache.saved == [("pikachu", "regular", payload, NOW.isoformat())]
    assert result == {**payload, "fetched_at": NOW.isoformat()}


def test_normalizes_species_and_form_before_cache_and_api_lookup():
    cache = Cache()
    api = Api({"name": "charizard-mega-x"})

    result = GetSpeciesData(cache=cache, api=api, clock=lambda: NOW).execute(
        " Charizárd ", "Mega X"
    )

    assert api.calls == [("charizard", "mega-x")]
    assert cache.saved[0][:2] == ("charizard", "mega-x")
    assert result is not None


def test_offline_miss_is_a_stable_none_and_does_not_poison_cache():
    cache = Cache()
    api = Api(None)

    result = GetSpeciesData(cache=cache, api=api, clock=lambda: NOW).execute("missingno", "regular")

    assert result is None
    assert cache.saved == []


def test_refresh_bypasses_existing_cache():
    cache = Cache({("pikachu", "regular"): {"pokedex_id": 1}})
    api = Api({"pokedex_id": 25})

    result = GetSpeciesData(cache=cache, api=api, clock=lambda: NOW).execute(
        "pikachu", "regular", refresh=True
    )

    assert result["pokedex_id"] == 25
    assert api.calls == [("pikachu", "regular")]
