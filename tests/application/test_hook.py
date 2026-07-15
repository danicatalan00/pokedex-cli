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


def test_preexisting_evolutions_are_snapshotted_before_sync_and_replace_wild_encounter() -> None:
    events: list[str] = []

    class Evolutions:
        def pending(self):
            events.append("snapshot")
            return (pending(),)

        def execute(self, capture_ids):
            events.append(f"evolve:{capture_ids}")
            return (pending(),)

    def sync_activity():
        events.append("sync")
        return "activity", ("training",)

    use_case = OpenTerminal(
        evolutions=Evolutions(),
        sync_activity=sync_activity,
        start_wild_encounter=lambda generations: events.append(f"wild:{generations}"),
    )

    result = use_case.execute("1-3")

    assert events == ["snapshot", "sync", "evolve:[1]"]
    assert result.evolutions == (pending(),)
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
        start_wild_encounter=lambda generations: events.append(f"wild:{generations}"),
    )

    result = use_case.execute("4-6")

    assert events == ["snapshot", "sync", "wild:4-6"]
    assert result.evolutions == ()
    assert result.wild_encounter_started
