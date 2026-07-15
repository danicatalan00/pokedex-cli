import math
import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pokedex_cli.domain.capture import catch_chance, escape_after_attempts, roll_capture


@pytest.mark.parametrize(
    ("capture_rate", "expected"),
    [(None, 0.8), (0, 0.0), (1, 1 / 255), (255, 1.0)],
)
def test_capture_rate_boundaries(capture_rate: int | None, expected: float) -> None:
    assert catch_chance(capture_rate) == pytest.approx(expected)


def test_missing_rate_uses_rare_species_fallback() -> None:
    assert catch_chance(None, is_legendary=True) == pytest.approx(0.55)
    assert catch_chance(None, is_mythical=True) == pytest.approx(0.55)
    assert catch_chance(None, is_legendary=True, is_mythical=True) == pytest.approx(0.55)


def test_shiny_is_explicitly_neutral_until_a_rule_is_introduced() -> None:
    assert catch_chance(127, shiny=True, ball_multiplier=1.5) == catch_chance(
        127, shiny=False, ball_multiplier=1.5
    )


@pytest.mark.parametrize("capture_rate", [-1, 256, 1.5, "255", True])
def test_invalid_capture_rates_are_rejected(capture_rate: object) -> None:
    with pytest.raises((TypeError, ValueError), match="capture_rate"):
        catch_chance(capture_rate)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("multiplier", "error"),
    [
        (-1.0, ValueError),
        (math.inf, ValueError),
        (-math.inf, ValueError),
        (math.nan, ValueError),
        (True, TypeError),
        ("2", TypeError),
        (None, TypeError),
    ],
)
def test_invalid_multipliers_are_rejected(multiplier: object, error: type[Exception]) -> None:
    with pytest.raises(error, match="ball_multiplier"):
        catch_chance(100, ball_multiplier=multiplier)


def test_masterball_sentinel_and_upper_clamp() -> None:
    assert catch_chance(1, ball_multiplier=255) == 1.0
    assert catch_chance(1, ball_multiplier=254.999) == pytest.approx(254.999 / 255)
    assert catch_chance(200, ball_multiplier=2) == 1.0
    assert catch_chance(200, ball_multiplier=0) == 0.0
    assert catch_chance(100, ball_multiplier=1.5) == pytest.approx(150 / 255)


def test_escape_pressure_selects_bounded_tiers_and_extends_shiny_upper_bound() -> None:
    assert escape_after_attempts(0, speed=180, rng=random.Random(1)) in range(2, 5)
    assert escape_after_attempts(255, speed=1, rng=random.Random(1)) in range(4, 7)
    assert escape_after_attempts(255, speed=1, shiny=True, rng=random.Random(5)) in range(4, 8)


def test_capture_roll_uses_the_injected_random_source() -> None:
    assert roll_capture(0.5, random.Random(1)) is True
    assert roll_capture(0.5, random.Random(2)) is False


@given(
    capture_rate=st.one_of(st.none(), st.integers(min_value=0, max_value=255)),
    multiplier=st.floats(
        min_value=0,
        max_value=300,
        allow_nan=False,
        allow_infinity=False,
    ),
    legendary=st.booleans(),
    mythical=st.booleans(),
)
def test_probability_is_always_closed_unit_interval(
    capture_rate: int | None,
    multiplier: float,
    legendary: bool,
    mythical: bool,
) -> None:
    probability = catch_chance(
        capture_rate,
        is_legendary=legendary,
        is_mythical=mythical,
        ball_multiplier=multiplier,
    )
    assert 0.0 <= probability <= 1.0
