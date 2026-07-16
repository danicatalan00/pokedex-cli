from pokedex_cli.application.pokedex_catalog import (
    CaptureAggregate,
    PokedexCatalog,
    SightingAggregate,
)


class FakeKrabby:
    def __init__(self, database):
        self._database = database

    def load_database(self):
        return self._database


class FakeSightings:
    def __init__(self, aggregates):
        self._aggregates = aggregates

    def aggregated(self):
        return self._aggregates


class FakeCaptures:
    def __init__(self, aggregates):
        self._aggregates = aggregates

    def captures_aggregated(self):
        return self._aggregates


class FakeSpeciesCache:
    def __init__(self, cache):
        self._cache = cache

    def species_cache_by_species(self):
        return self._cache


NATIONAL_DEX = [
    {
        "idx": 1,
        "slug": "bulbasaur",
        "gen": 1,
        "name": {"en": "Bulbasaur"},
        "desc": {"en": "A strange seed was planted on its back at birth."},
    },
    {
        "idx": 4,
        "slug": "charmander",
        "gen": 1,
        "name": {"en": "Charmander"},
        "desc": {"en": "Obviously prefers hot places."},
    },
    {
        "idx": 7,
        "slug": "squirtle",
        "gen": 1,
        "name": {"en": "Squirtle"},
        "desc": {"en": "Shoots water at prey."},
    },
]


class FakeDexRegistry:
    def __init__(self, species):
        self._species = species

    def dex_caught_species(self):
        return self._species


def catalog(
    database=NATIONAL_DEX,
    sightings=None,
    captures=None,
    species_cache=None,
    dex_registry=None,
) -> PokedexCatalog:
    return PokedexCatalog(
        krabby=FakeKrabby(database),
        sightings=FakeSightings(sightings or {}),
        captures=FakeCaptures(captures or {}),
        species_cache=FakeSpeciesCache(species_cache or {}),
        dex_registry=FakeDexRegistry(dex_registry) if dex_registry is not None else None,
    )


def test_status_is_unseen_seen_or_captured_and_entries_are_ordered_by_idx():
    entries = catalog(
        sightings={"charmander": SightingAggregate("2026-07-01T00:00:00+00:00", 3)},
        captures={"squirtle": CaptureAggregate(captures_count=1, max_level=12, any_shiny=False)},
    ).execute()

    assert [entry.idx for entry in entries] == [1, 4, 7]
    by_slug = {entry.slug: entry for entry in entries}
    assert by_slug["bulbasaur"].status == "unseen"
    assert by_slug["charmander"].status == "seen"
    assert by_slug["squirtle"].status == "captured"


def test_dex_registry_keeps_evolved_species_captured_without_living_captures():
    entries = catalog(
        sightings={"charmander": SightingAggregate("2026-07-01T00:00:00+00:00", 1)},
        captures={},
        dex_registry={"charmander"},
    ).execute()
    by_slug = {entry.slug: entry for entry in entries}
    assert by_slug["charmander"].status == "captured"
    assert by_slug["charmander"].captures_count == 0


def test_base_stats_come_from_the_species_cache_when_complete():
    cache_row = {
        "types": ["water"],
        "hp": 44,
        "atk": 48,
        "def": 65,
        "spa": 50,
        "spd": 64,
        "spe": 43,
    }
    entries = catalog(
        captures={"squirtle": CaptureAggregate(captures_count=1, max_level=12, any_shiny=False)},
        species_cache={"squirtle": cache_row},
    ).execute()
    by_slug = {entry.slug: entry for entry in entries}
    assert by_slug["squirtle"].base_stats == (
        ("hp", 44),
        ("atk", 48),
        ("def", 65),
        ("spa", 50),
        ("spd", 64),
        ("spe", 43),
    )
    assert by_slug["bulbasaur"].base_stats is None


def test_unseen_species_have_no_description_and_a_hidden_dex_entry_is_never_fabricated():
    entries = catalog().execute()
    bulbasaur = next(entry for entry in entries if entry.slug == "bulbasaur")
    assert bulbasaur.description is None
    assert bulbasaur.types is None
    assert bulbasaur.captures_count == 0
    assert bulbasaur.max_level is None
    assert bulbasaur.any_shiny is False
    assert bulbasaur.times_seen == 0


def test_seen_species_shows_krabby_english_description_when_no_cache_flavor_exists():
    entries = catalog(
        sightings={"charmander": SightingAggregate("2026-07-01T00:00:00+00:00", 5)},
    ).execute()
    charmander = next(entry for entry in entries if entry.slug == "charmander")
    assert charmander.status == "seen"
    assert charmander.description == "Obviously prefers hot places."
    assert charmander.times_seen == 5


def test_captured_species_prefers_the_cached_spanish_flavor_text_over_krabby_english():
    entries = catalog(
        captures={"squirtle": CaptureAggregate(captures_count=2, max_level=30, any_shiny=True)},
        species_cache={
            "squirtle": {
                "types": ["water"],
                "is_legendary": False,
                "is_mythical": False,
                "flavor_text": "Dispara agua a sus presas.",
            }
        },
    ).execute()
    squirtle = next(entry for entry in entries if entry.slug == "squirtle")
    assert squirtle.status == "captured"
    assert squirtle.description == "Dispara agua a sus presas."
    assert squirtle.types == ("water",)
    assert squirtle.captures_count == 2
    assert squirtle.max_level == 30
    assert squirtle.any_shiny is True


def test_missing_krabby_database_falls_back_to_only_seen_and_captured_species():
    entries = catalog(
        database=None,
        sightings={"eevee": SightingAggregate("2026-07-01T00:00:00+00:00", 1)},
        captures={"pikachu": CaptureAggregate(captures_count=1, max_level=5, any_shiny=False)},
    ).execute()

    assert [entry.slug for entry in entries] == ["eevee", "pikachu"]
    assert [entry.idx for entry in entries] == [1, 2]
    assert {entry.status for entry in entries} == {"seen", "captured"}


def test_empty_krabby_database_and_no_local_progress_yields_an_empty_catalog():
    assert catalog(database=[]).execute() == []
