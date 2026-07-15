import pytest

from pokedex_cli.domain.identity import normalize_form, normalize_slug, normalize_species


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" Mr. Mime ", "mr-mime"),
        ("Farfetch'd", "farfetchd"),
        ("Flabébé", "flabebe"),
        ("Nidoran♀", "nidoran-f"),
        ("Nidoran♂", "nidoran-m"),
        ("Type: Null", "type-null"),
    ],
)
def test_species_names_normalize_to_stable_slugs(raw: str, expected: str) -> None:
    assert normalize_species(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Mega X", "mega-x"), ("  AL0LA  ", "al0la"), ("", "regular"), (None, "regular")],
)
def test_forms_normalize_and_empty_forms_mean_regular(raw: str | None, expected: str) -> None:
    assert normalize_form(raw) == expected


def test_generic_slug_collapses_separators_and_rejects_empty_values() -> None:
    assert normalize_slug("  Ultra__Ball / Deluxe  ") == "ultra-ball-deluxe"
    with pytest.raises(ValueError, match="vacío"):
        normalize_slug("´' . ")
