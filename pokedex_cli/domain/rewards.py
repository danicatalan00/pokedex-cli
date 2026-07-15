"""Pure stock and activity-reward rules."""

from __future__ import annotations

from datetime import datetime, timedelta


def grant_stock(current: int, requested: int, maximum: int) -> tuple[int, int]:
    """Return ``(new_stock, granted)`` while preserving stock bounds."""
    if maximum < 0:
        raise ValueError("maximum stock cannot be negative")
    bounded_current = min(maximum, max(0, int(current)))
    wanted = max(0, int(requested))
    granted = min(wanted, maximum - bounded_current)
    return bounded_current + granted, granted


def threshold_crossings(previous: int, new_events: int, every: int) -> int:
    """Count reward thresholds crossed by a non-negative event increment."""
    if every <= 0:
        raise ValueError("threshold must be positive")
    start = max(0, int(previous))
    increment = max(0, int(new_events))
    return (start + increment) // every - start // every


def elapsed_intervals(start: datetime, end: datetime, interval: timedelta) -> int:
    """Count complete positive intervals between two aware instants."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    if interval <= timedelta(0):
        raise ValueError("interval must be positive")
    return max(0, int((end - start) // interval))
