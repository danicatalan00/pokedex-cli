from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pokedex_cli.domain.rewards import (
    elapsed_intervals,
    grant_stock,
    threshold_crossings,
)


@pytest.mark.parametrize(
    ("previous", "new_commits", "every", "expected"),
    [
        (0, 2, 3, 0),
        (0, 3, 3, 1),
        (2, 1, 3, 1),
        (3, 2, 3, 0),
        (8, 2, 3, 1),
        (8, 2, 10, 1),
        (49, 1, 50, 1),
        (50, 100, 50, 2),
    ],
)
def test_rewards_before_during_and_after_thresholds(
    previous: int, new_commits: int, every: int, expected: int
) -> None:
    assert threshold_crossings(previous, new_commits, every) == expected


@pytest.mark.parametrize(
    ("current", "requested", "maximum", "expected"),
    [(0, 1, 10, (1, 1)), (9, 5, 10, (10, 1)), (10, 1, 10, (10, 0))],
)
def test_stock_grants_respect_maximum(
    current: int, requested: int, maximum: int, expected: tuple[int, int]
) -> None:
    assert grant_stock(current, requested, maximum) == expected


def test_elapsed_intervals_are_timezone_aware_and_never_negative() -> None:
    start = datetime(2026, 3, 28, 12, tzinfo=timezone.utc)
    assert elapsed_intervals(start, start + timedelta(hours=49), timedelta(days=1)) == 2
    assert elapsed_intervals(start, start - timedelta(hours=1), timedelta(days=1)) == 0


@pytest.mark.parametrize("threshold", [0, -1])
def test_reward_threshold_must_be_positive(threshold: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        threshold_crossings(1, 1, threshold)


def test_negative_event_values_are_normalised_before_counting() -> None:
    assert threshold_crossings(-100, 3, 3) == 1
    assert threshold_crossings(2, -100, 3) == 0


def test_elapsed_intervals_reject_naive_dates_and_non_positive_intervals() -> None:
    aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
    naive = aware.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        elapsed_intervals(naive, aware, timedelta(days=1))
    with pytest.raises(ValueError, match="timezone-aware"):
        elapsed_intervals(aware, naive, timedelta(days=1))
    with pytest.raises(ValueError, match="positive"):
        elapsed_intervals(aware, aware, timedelta(0))


def test_stock_normalises_invalid_counts_and_rejects_invalid_maximum() -> None:
    assert grant_stock(-5, -2, 10) == (0, 0)
    assert grant_stock(20, 5, 10) == (10, 0)
    with pytest.raises(ValueError, match="negative"):
        grant_stock(0, 1, -1)


@given(
    current=st.integers(min_value=-1_000, max_value=1_000),
    requested=st.integers(min_value=-1_000, max_value=1_000),
    maximum=st.integers(min_value=0, max_value=1_000),
)
def test_stock_never_becomes_negative_or_exceeds_maximum(
    current: int, requested: int, maximum: int
) -> None:
    stock, granted = grant_stock(current, requested, maximum)
    assert 0 <= stock <= maximum
    assert 0 <= granted <= maximum


@given(
    previous=st.integers(min_value=0, max_value=10_000),
    new_commits=st.integers(min_value=0, max_value=10_000),
    every=st.integers(min_value=1, max_value=100),
)
def test_threshold_crossings_are_non_negative_and_exact(
    previous: int, new_commits: int, every: int
) -> None:
    crossings = threshold_crossings(previous, new_commits, every)
    assert crossings >= 0
    assert crossings == (previous + new_commits) // every - previous // every
