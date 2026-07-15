from pokedex_cli.application.collection import CollectionQueries
from pokedex_cli.domain.individuality import STAT_KEYS, compute_stats, derive_ivs_nature


def row(
    capture_id,
    species,
    *,
    form="regular",
    types=None,
    team=False,
    legendary=False,
    mythical=False,
    stats=(),
    level=5,
    experience=0,
    caught_at="2026-07-15T10:00:00+00:00",
):
    values = {
        "id": capture_id,
        "species": species,
        "form": form,
        "types": types,
        "in_team": team,
        "is_legendary": legendary,
        "is_mythical": mythical,
        "hp": None,
        "atk": None,
        "def": None,
        "spa": None,
        "spd": None,
        "spe": None,
        "level": level,
        "experience": experience,
        "growth_rate": "medium",
        "caught_at": caught_at,
        # No stored individuality: every test row exercises the same
        # deterministic-derivation fallback the retroactive backfill uses.
        "iv_hp": None,
        "iv_atk": None,
        "iv_def": None,
        "iv_spa": None,
        "iv_spd": None,
        "iv_spe": None,
        "nature": None,
        "gender": None,
        "ability": None,
        "gender_rate": None,
        "abilities": None,
    }
    if stats:
        for key, value in zip(("hp", "atk", "def", "spa", "spd", "spe"), stats, strict=True):
            values[key] = value
    return values


def expected_actual_total(capture_id: int, caught_at: str, bases: dict) -> int:
    """Independent oracle: same derivation the collection query falls back to."""
    ivs, nature = derive_ivs_nature(f"{capture_id}:{caught_at}")
    stats = compute_stats(bases, ivs, 5, nature)
    return sum(value for value in stats.values() if value is not None)


class Repository:
    def __init__(self, rows):
        self.rows = rows

    def list_captures(self):
        return [dict(item) for item in self.rows]


def queries():
    return CollectionQueries(
        Repository(
            [
                row(3, "raichu", types=["electric"], team=True, stats=(60, 90, 55, 90, 80, 110)),
                row(2, "mew", types=["psychic"], mythical=True, stats=(100,) * 6),
                row(1, "raichu", form="alola", types=None),
            ]
        )
    )


def test_resolve_supports_id_latest_name_and_form():
    service = queries()
    assert service.resolve("1", None)["form"] == "alola"
    assert service.resolve("RAICHU", None)["id"] == 3
    assert service.resolve("raichu", "alola")["id"] == 1
    assert service.resolve("missing", None) is None


def test_resolve_normalizes_user_facing_species_and_form():
    service = CollectionQueries(Repository([row(1, "mr-mime", form="mega-x")]))

    assert service.resolve(" Mr. Mime ", "Mega X")["id"] == 1


def test_team_available_types_ranking_and_rare_views_are_prepared():
    service = queries()
    assert [item["id"] for item in service.team()] == [3]
    assert [item["id"] for item in service.available_for_team()] == [2, 1]
    assert service.type_counts() == {"electric": 1, "psychic": 1}

    ranked, missing = service.ranking()
    assert [item["species"] for item in ranked] == ["mew", "raichu"]
    mew_bases = {key: 100 for key in STAT_KEYS}
    raichu_bases = dict(zip(STAT_KEYS, (60, 90, 55, 90, 80, 110), strict=True))
    assert [item["total"] for item in ranked] == [
        expected_actual_total(2, "2026-07-15T10:00:00+00:00", mew_bases),
        expected_actual_total(3, "2026-07-15T10:00:00+00:00", raichu_bases),
    ]
    assert [item["base_total"] for item in ranked] == [600, 485]
    assert missing == 1
    assert [item["species"] for item in service.rare()] == ["mew"]


def test_collection_returns_fresh_rows_that_callers_cannot_mutate_across_calls():
    service = queries()
    first = service.captures()
    first[0]["species"] = "changed"
    assert service.captures()[0]["species"] == "raichu"


def test_collection_prepares_experience_progress_for_presentation():
    service = CollectionQueries(
        Repository(
            [
                row(1, "pikachu", level=5, experience=150),
                row(2, "mewtwo", level=100, experience=1_000_000),
            ]
        )
    )

    progressing, maximum = service.captures()
    assert (progressing["experience_into_level"], progressing["experience_for_next_level"]) == (
        25,
        91,
    )
    assert progressing["is_max_level"] is False
    assert maximum["is_max_level"] is True
    assert maximum["experience_for_next_level"] == 0


def test_team_is_sorted_by_descending_level_then_capture_id():
    service = CollectionQueries(
        Repository(
            [
                row(8, "eevee", team=True, level=12),
                row(3, "pikachu", team=True, level=30),
                row(2, "raichu", team=True, level=30),
            ]
        )
    )

    assert [item["id"] for item in service.team()] == [2, 3, 8]
