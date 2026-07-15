import pytest
from hypothesis import given
from hypothesis import strategies as st

from pokedex_cli.domain.progression import (
    DEFAULT_BASE_EXPERIENCE,
    MAX_LEVEL,
    commit_difficulty,
    commit_experience,
    experience_for_level,
    level_for_experience,
)


def expected_experience(rate: str, level: int) -> int:
    """Independent conformance oracle for the six official growth curves."""
    n = level
    if rate in {"medium", "medium-fast"}:
        value = n**3
    elif rate == "fast":
        value = 4 * n**3 // 5
    elif rate == "slow":
        value = 5 * n**3 // 4
    elif rate == "medium-slow":
        value = 6 * n**3 // 5 - 15 * n**2 + 100 * n - 140
    elif rate == "erratic":
        if n <= 50:
            value = n**3 * (100 - n) // 50
        elif n <= 68:
            value = n**3 * (150 - n) // 100
        elif n <= 98:
            value = n**3 * ((1911 - 10 * n) // 3) // 500
        else:
            value = n**3 * (160 - n) // 100
    else:
        if n <= 15:
            value = n**3 * ((n + 1) // 3 + 24) // 50
        elif n <= 36:
            value = n**3 * (n + 14) // 50
        else:
            value = n**3 * (n // 2 + 32) // 50
    return 0 if n == 1 else max(0, value)


@pytest.mark.parametrize(
    "growth_rate",
    ["fast", "medium", "medium-fast", "medium-slow", "slow", "erratic", "fluctuating"],
)
def test_every_supported_level_matches_the_official_curve(growth_rate: str) -> None:
    assert [experience_for_level(growth_rate, level) for level in range(1, 101)] == [
        expected_experience(growth_rate, level) for level in range(1, 101)
    ]


def test_missing_unknown_and_case_variant_rates_use_the_documented_curve() -> None:
    expected = 27_000
    assert experience_for_level(None, 30) == expected
    assert experience_for_level("unknown", 30) == expected
    assert experience_for_level("MEDIUM", 30) == expected


@pytest.mark.parametrize("level", [1, 2, 99, 100])
@pytest.mark.parametrize(
    "growth_rate",
    ["fast", "medium", "medium-slow", "slow", "erratic", "fluctuating"],
)
def test_experience_landmarks_round_trip_to_their_level(growth_rate: str, level: int) -> None:
    experience = experience_for_level(growth_rate, level)
    assert level_for_experience(growth_rate, experience) == level


@pytest.mark.parametrize("requested", [-10, 0, 1, 100, 101, 1000])
def test_level_input_is_clamped_to_supported_bounds(requested: int) -> None:
    expected = min(MAX_LEVEL, max(1, requested))
    assert experience_for_level("medium", requested) == experience_for_level("medium", expected)


@pytest.mark.parametrize(
    ("changed_lines", "expected"),
    [(-1, 1.0), (0, 1.0), (50, 2.0), (2450, 50.0), (1_000_000, 50.0)],
)
def test_diff_difficulty_boundaries(changed_lines: int, expected: float) -> None:
    assert commit_difficulty(changed_lines) == expected


@pytest.mark.parametrize(
    ("level", "base", "changed_lines", "expected"),
    [
        (5, None, 0, DEFAULT_BASE_EXPERIENCE * 5 // 7),
        (1, 1, 0, 1),
        (10, 70, 50, 200),
        (10, 70, 2450, 5000),
        (-1, -1, -1, 1),
    ],
)
def test_commit_experience_uses_level_base_and_bounded_diff_exactly(
    level: int, base: int | None, changed_lines: int, expected: int
) -> None:
    assert commit_experience(level, base, changed_lines) == expected


def test_commit_experience_default_means_zero_changed_lines() -> None:
    assert commit_experience(10, 70) == 100


@pytest.mark.parametrize("growth_rate", ["fast", "medium-slow", "erratic", "fluctuating"])
@pytest.mark.parametrize("level", [2, 15, 16, 36, 37, 50, 51, 68, 69, 98, 99, 100])
def test_level_changes_exactly_at_each_experience_boundary(growth_rate: str, level: int) -> None:
    boundary = experience_for_level(growth_rate, level)
    assert level_for_experience(growth_rate, boundary) == level
    assert level_for_experience(growth_rate, boundary - 1) == level - 1


def test_level_is_clamped_for_negative_and_excess_experience() -> None:
    assert level_for_experience("medium", -1) == 1
    assert level_for_experience("medium", 10**20) == MAX_LEVEL


@given(
    growth_rate=st.sampled_from(
        ["fast", "medium", "medium-slow", "slow", "erratic", "fluctuating"]
    ),
    lower=st.integers(min_value=1, max_value=100),
    upper=st.integers(min_value=1, max_value=100),
)
def test_level_never_decreases_when_experience_increases(
    growth_rate: str, lower: int, upper: int
) -> None:
    low_level, high_level = sorted((lower, upper))
    low_experience = experience_for_level(growth_rate, low_level)
    high_experience = experience_for_level(growth_rate, high_level)
    assert high_experience >= low_experience
    assert level_for_experience(growth_rate, high_experience) >= level_for_experience(
        growth_rate, low_experience
    )
