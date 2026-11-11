import math
import random
import sys

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


@pytest.mark.parametrize(
    ("multiplier", "error"),
    [
        (-1.0, ValueError),
        (math.inf, ValueError),
        (math.nan, ValueError),
        (True, TypeError),
        ("2", TypeError),
        (None, TypeError),
    ],
)
def test_invalid_mood_multipliers_are_rejected(multiplier: object, error: type[Exception]) -> None:
    with pytest.raises(error, match="mood_multiplier"):
        catch_chance(100, mood_multiplier=multiplier)


def test_mood_multiplier_scales_the_probability_but_not_the_masterball() -> None:
    """Rocas y cebos entran como un factor más, salvo en la Masterball: esa
    nunca falla, así que se resuelve antes de mirar el ánimo."""
    assert catch_chance(30, mood_multiplier=2.0) == pytest.approx(60 / 255)
    assert catch_chance(30, mood_multiplier=0.5) == pytest.approx(15 / 255)
    assert catch_chance(30, mood_multiplier=0.0) == 0.0
    assert catch_chance(200, mood_multiplier=2.0) == 1.0
    assert catch_chance(1, ball_multiplier=255, mood_multiplier=0.125) == 1.0
    assert catch_chance(100) == catch_chance(100, mood_multiplier=1.0)


@pytest.mark.parametrize("ball_multiplier", [1.0, 255.0])
@pytest.mark.parametrize(
    ("multiplier", "error"),
    [
        (-1.0, ValueError),
        (-0.001, ValueError),
        (math.inf, ValueError),
        (math.nan, ValueError),
        (True, TypeError),
        ("2", TypeError),
        (None, TypeError),
    ],
)
def test_invalid_battle_multipliers_are_rejected_even_with_the_masterball(
    ball_multiplier: float, multiplier: object, error: type[Exception]
) -> None:
    """Se valida como el ánimo: antes del atajo de la Masterball, para que un
    dato corrupto del combate no pase desapercibido con la bola infalible."""
    with pytest.raises(error, match=r"^battle_multiplier must"):
        catch_chance(100, ball_multiplier=ball_multiplier, battle_multiplier=multiplier)


def test_battle_multiplier_scales_the_probability_but_not_the_masterball() -> None:
    """Debilitar o dormir al salvaje es otro factor más; la Masterball se
    resuelve antes, así que ni un factor nulo la hace fallar."""
    assert catch_chance(30, battle_multiplier=2.0) == pytest.approx(60 / 255)
    assert catch_chance(30, battle_multiplier=0.5) == pytest.approx(15 / 255)
    assert catch_chance(30, battle_multiplier=0.0) == 0.0
    assert catch_chance(100, battle_multiplier=3.0) == 1.0
    assert catch_chance(1, ball_multiplier=255, battle_multiplier=0.0) == 1.0
    assert catch_chance(100) == catch_chance(100, battle_multiplier=1.0)


def test_a_null_battle_multiplier_never_becomes_a_sure_catch() -> None:
    """Un ánimo enorme pero finito pasa la validación y desborda el producto a
    infinito; por un factor de combate nulo daría NaN, y acotar NaN devolvía
    una captura segura. El producto exacto es 0."""
    huge = sys.float_info.max

    assert catch_chance(255, level=1, mood_multiplier=huge, battle_multiplier=0.0) == 0.0
    assert (
        catch_chance(255, ball_multiplier=254.0, mood_multiplier=1e306, battle_multiplier=0.0)
        == 0.0
    )


def test_battle_multiplier_composes_with_ball_level_and_mood() -> None:
    chance = catch_chance(
        10, ball_multiplier=1.5, level=20, mood_multiplier=2.0, battle_multiplier=3.0
    )

    assert chance == pytest.approx((10 / 255) * 1.5 * math.sqrt(5 / 20) * 2.0 * 3.0)


@given(
    capture_rate=st.one_of(st.none(), st.integers(min_value=0, max_value=255)),
    ball=st.floats(min_value=0, max_value=300, allow_nan=False, allow_infinity=False),
    first=st.floats(min_value=0, max_value=6, allow_nan=False, allow_infinity=False),
    second=st.floats(min_value=0, max_value=6, allow_nan=False, allow_infinity=False),
)
def test_a_weaker_wild_pokemon_is_never_harder_to_catch(
    capture_rate: int | None, ball: float, first: float, second: float
) -> None:
    low, high = sorted((first, second))

    weaker = catch_chance(capture_rate, ball_multiplier=ball, battle_multiplier=high)
    healthier = catch_chance(capture_rate, ball_multiplier=ball, battle_multiplier=low)

    assert 0.0 <= healthier <= weaker <= 1.0


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
