import pytest

from pokedex_cli.domain.battle.typechart import (
    TYPE_NAMES_ES,
    TYPES,
    effectiveness,
    single_effectiveness,
)


@pytest.mark.parametrize(
    ("move_type", "defender", "expected"),
    [
        ("fire", ["grass"], 2.0),
        ("water", ["fire"], 2.0),
        ("electric", ["ground"], 0.0),
        ("normal", ["ghost"], 0.0),
        ("ghost", ["normal"], 0.0),
        ("fighting", ["ghost"], 0.0),
        ("dragon", ["fairy"], 0.0),
        ("ground", ["flying"], 0.0),
        ("psychic", ["dark"], 0.0),
        ("poison", ["steel"], 0.0),
        ("ice", ["dragon", "flying"], 4.0),
        ("fire", ["water", "rock"], 0.25),
        ("ghost", ["steel"], 1.0),
        ("dark", ["steel"], 1.0),
        ("fairy", ["dragon", "dark"], 4.0),
        ("electric", ["water", "flying"], 4.0),
        ("grass", ["fire", "flying"], 0.25),
    ],
)
def test_canonical_matchups(move_type, defender, expected):
    assert effectiveness(move_type, defender) == expected


def test_unknown_types_are_neutral_never_immune():
    assert effectiveness("shadow", ["normal"]) == 1.0
    assert effectiveness("fire", ["unknown"]) == 1.0
    assert effectiveness("fire", []) == 1.0


def test_chart_has_the_modern_number_of_relations():
    """Gen 6+: 51 súper eficaces, 61 poco eficaces y 8 inmunidades."""
    values = [single_effectiveness(a, d) for a in TYPES for d in TYPES]
    assert values.count(2.0) == 51
    assert values.count(0.5) == 61
    assert values.count(0.0) == 8


def test_every_type_has_a_spanish_name():
    assert set(TYPE_NAMES_ES) == set(TYPES)
