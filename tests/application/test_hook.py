import sqlite3

import pytest

from pokedex_cli.application.evolutions import PendingEvolution
from pokedex_cli.application.hook import OpenTerminal


def pending(capture_id: int = 1) -> PendingEvolution:
    return PendingEvolution(
        capture_id,
        "bulbasaur",
        "regular",
        "ivysaur",
        "regular",
        False,
        16,
    )


def test_sync_queues_evolutions_before_snapshot_and_prepares_them_in_order() -> None:
    events: list[str] = []

    class Evolutions:
        def pending(self):
            events.append("snapshot")
            return (pending(1), pending(2))

        def execute(self, capture_ids):
            events.append(f"evolve:{capture_ids}")
            return (pending(1), pending(2))

    def sync_activity():
        events.append("sync")
        return "activity", ("training",)

    use_case = OpenTerminal(
        evolutions=Evolutions(),
        sync_activity=sync_activity,
        prepare_evolution=lambda item: events.append(f"prepare:{item.capture_id}"),
        start_wild_encounter=lambda generations: events.append(f"wild:{generations}"),
    )

    result = use_case.execute("1-3")

    assert events == ["sync", "snapshot", "prepare:1", "prepare:2", "evolve:[1, 2]"]
    assert result.evolutions == (pending(1), pending(2))
    assert result.activity == "activity"
    assert result.training == ("training",)
    assert not result.wild_encounter_started


def test_without_pending_evolution_sync_runs_before_wild_encounter() -> None:
    events: list[str] = []

    class Evolutions:
        def pending(self):
            events.append("snapshot")
            return ()

        def execute(self, capture_ids):
            raise AssertionError("must not process an empty snapshot")

    use_case = OpenTerminal(
        evolutions=Evolutions(),
        sync_activity=lambda: (events.append("sync") or "activity", ()),
        prepare_evolution=lambda item: None,
        start_wild_encounter=lambda generations: events.append(f"wild:{generations}"),
    )

    result = use_case.execute("4-6")

    assert events == ["sync", "snapshot", "wild:4-6"]
    assert result.evolutions == ()
    assert result.wild_encounter_started


class NoEvolutions:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def pending(self):
        self.events.append("snapshot")
        return ()

    def execute(self, capture_ids):
        raise AssertionError("must not process an empty snapshot")


def terminal(events: list[str], battle_in_progress, evolutions=None) -> OpenTerminal:
    return OpenTerminal(
        evolutions=evolutions or NoEvolutions(events),
        sync_activity=lambda: (events.append("sync") or "activity", ("training",)),
        prepare_evolution=lambda item: events.append(f"prepare:{item.capture_id}"),
        start_wild_encounter=lambda generations: events.append(f"wild:{generations}"),
        battle_in_progress=battle_in_progress,
    )


def test_an_active_wild_battle_keeps_its_encounter_and_is_reminded() -> None:
    """Arrancar otro encuentro sustituiría al rival y dejaría el combate huérfano."""
    events: list[str] = []
    use_case = terminal(events, lambda: events.append("battle?") or "Pidgey")

    result = use_case.execute("1-3")

    assert events == ["sync", "snapshot", "battle?"]
    assert result.battle_reminder == "Pidgey"
    assert result.wild_encounter_started is False
    assert result.evolutions == ()
    assert (result.activity, result.training) == ("activity", ("training",))


def test_without_an_active_battle_the_wild_encounter_starts_as_before() -> None:
    events: list[str] = []
    use_case = terminal(events, lambda: events.append("battle?") or None)

    result = use_case.execute("4-6")

    assert events == ["sync", "snapshot", "battle?", "wild:4-6"]
    assert result.battle_reminder is None
    assert result.wild_encounter_started is True
    assert result.evolutions == ()
    assert (result.activity, result.training) == ("activity", ("training",))


def test_without_a_battle_lookup_the_reminder_stays_empty() -> None:
    events: list[str] = []
    use_case = terminal(events, None)

    result = use_case.execute("4-6")

    assert events == ["sync", "snapshot", "wild:4-6"]
    assert result.battle_reminder is None
    assert result.wild_encounter_started is True


@pytest.mark.parametrize(
    "failure",
    [sqlite3.OperationalError("database is locked"), ValueError("corrupt battle"), KeyError("x")],
)
def test_a_failing_battle_lookup_counts_as_no_battle(failure: Exception) -> None:
    """El hook nunca propaga un fallo recuperable: sin saber si hay combate,
    la terminal sigue como siempre."""
    events: list[str] = []

    def battle_in_progress() -> str | None:
        raise failure

    result = terminal(events, battle_in_progress).execute("4-6")

    assert events == ["sync", "snapshot", "wild:4-6"]
    assert result.battle_reminder is None
    assert result.wild_encounter_started is True


def test_interrupting_the_battle_lookup_is_not_swallowed() -> None:
    """Solo se tragan los fallos recuperables: Ctrl+C sigue cortando el hook."""
    events: list[str] = []

    def battle_in_progress() -> str | None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        terminal(events, battle_in_progress).execute("4-6")

    assert events == ["sync", "snapshot"]


def test_evolutions_still_run_during_a_battle_and_carry_the_reminder() -> None:
    """La rama de evoluciones tampoco arranca encuentro; el aviso permite a la
    presentación no limpiar el encuentro del combate tras evolucionar."""
    events: list[str] = []

    class Evolutions:
        def pending(self):
            events.append("snapshot")
            return (pending(1),)

        def execute(self, capture_ids):
            events.append(f"evolve:{capture_ids}")
            return (pending(1),)

    use_case = terminal(events, lambda: events.append("battle?") or "Pidgey", Evolutions())

    result = use_case.execute("1-3")

    assert events == ["sync", "snapshot", "battle?", "prepare:1", "evolve:[1]"]
    assert result.evolutions == (pending(1),)
    assert result.battle_reminder == "Pidgey"
    assert result.wild_encounter_started is False
