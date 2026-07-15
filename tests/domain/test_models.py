from hypothesis import given
from hypothesis import strategies as st

from pokedex_cli.domain.models import Encounter, Pokemon

safe_text = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=30)
identity_text = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    min_size=1,
    max_size=30,
)


@given(
    species=identity_text,
    form=identity_text,
    shiny=st.booleans(),
    seen_at=safe_text,
    captured=st.booleans(),
    failed=st.integers(min_value=0, max_value=10_000),
    escape_after=st.one_of(st.none(), st.integers(min_value=1, max_value=10_000)),
)
def test_encounter_serialization_round_trip(
    species: str,
    form: str,
    shiny: bool,
    seen_at: str,
    captured: bool,
    failed: int,
    escape_after: int | None,
) -> None:
    encounter = Encounter(
        Pokemon(species, form, shiny),
        seen_at,
        captured,
        failed,
        escape_after,
    )
    assert Encounter.from_dict(encounter.to_dict()) == encounter


def test_encounter_rejects_missing_identity_fields() -> None:
    assert Encounter.from_dict({"species": "pikachu"}) is None


def test_pokemon_stores_normalized_identity() -> None:
    assert Pokemon(" Mr. Mime ", "Mega X") == Pokemon("mr-mime", "mega-x")
