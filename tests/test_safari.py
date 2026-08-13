import pytest

from pokedex_cli.domain.capture import catch_chance
from pokedex_cli.domain.safari import (
    MAX_CATCH_STAGE,
    MAX_PATIENCE,
    MIN_CATCH_STAGE,
    SafariAction,
    SafariMood,
    catch_stage_multiplier,
    describe_mood,
    throw,
)


def test_rock_trades_patience_for_catch_probability() -> None:
    outcome = throw(SafariAction.ROCK, catch_stage=0, patience=4, spent_turns=0)

    assert outcome.catch_stage == 1
    assert outcome.patience == 3
    assert outcome.spent_turns == 1
    assert outcome.fled is False
    assert outcome.mood is SafariMood.ANGRY


def test_bait_trades_catch_probability_for_patience() -> None:
    outcome = throw(SafariAction.BAIT, catch_stage=0, patience=4, spent_turns=0)

    assert outcome.catch_stage == -1
    assert outcome.patience == 6
    assert outcome.spent_turns == 1
    assert outcome.fled is False
    assert outcome.mood is SafariMood.EATING


def test_a_rock_that_exhausts_the_last_turn_makes_it_flee() -> None:
    # Patience 2 with one turn already spent: the rock spends the second turn
    # and angers it out of the remaining one.
    outcome = throw(SafariAction.ROCK, catch_stage=0, patience=2, spent_turns=1)

    assert outcome.fled is True
    assert outcome.mood is SafariMood.ANGRY
    # La paciencia agotada se reporta tal cual; el encuentro se descarta, no se
    # guarda, así que no hace falta maquillar el umbral.
    assert (outcome.patience, outcome.remaining_turns) == (1, 0)


def test_a_rock_can_drive_the_patience_below_the_spent_turn() -> None:
    outcome = throw(SafariAction.ROCK, catch_stage=0, patience=1, spent_turns=0)

    assert outcome.fled is True
    assert outcome.patience == 0


def test_bait_never_makes_it_flee_while_it_still_buys_patience() -> None:
    outcome = throw(SafariAction.BAIT, catch_stage=0, patience=1, spent_turns=0)

    assert outcome.fled is False
    assert outcome.remaining_turns == 2


def test_bait_stops_working_once_it_is_too_calm_and_the_encounter_can_end() -> None:
    """Patience is capped, so the encounter always has an end: bait spends a
    turn it can no longer pay for."""
    outcome = throw(
        SafariAction.BAIT,
        catch_stage=0,
        patience=MAX_PATIENCE,
        spent_turns=MAX_PATIENCE - 1,
    )

    assert outcome.patience_gained == 0
    assert outcome.fled is True


@pytest.mark.parametrize(
    ("action", "stage", "expected"),
    [
        (SafariAction.ROCK, MAX_CATCH_STAGE, MAX_CATCH_STAGE),
        (SafariAction.BAIT, MIN_CATCH_STAGE, MIN_CATCH_STAGE),
    ],
)
def test_the_catch_stage_saturates_at_both_ends(
    action: SafariAction, stage: int, expected: int
) -> None:
    outcome = throw(action, catch_stage=stage, patience=MAX_PATIENCE, spent_turns=0)

    assert outcome.catch_stage == expected
    assert outcome.stage_delta == 0


@pytest.mark.parametrize(
    ("stage", "expected"),
    [(-3, 0.125), (-1, 0.5), (0, 1.0), (1, 2.0), (3, 8.0)],
)
def test_each_stage_doubles_or_halves_the_catch_probability(stage: int, expected: float) -> None:
    assert catch_stage_multiplier(stage) == expected


def test_out_of_range_stages_are_clamped_before_they_reach_the_probability() -> None:
    assert catch_stage_multiplier(99) == catch_stage_multiplier(MAX_CATCH_STAGE)
    assert catch_stage_multiplier(-99) == catch_stage_multiplier(MIN_CATCH_STAGE)


def test_stage_multiplier_composes_with_the_ball_but_never_beats_the_master_ball() -> None:
    assert catch_chance(30, ball_multiplier=1.0, mood_multiplier=2.0) == pytest.approx(60 / 255)
    # The Master Ball is unconditional: no amount of bait can spoil it.
    assert catch_chance(3, ball_multiplier=255, mood_multiplier=0.125) == 1.0


@pytest.mark.parametrize(
    ("stage", "expected"),
    [(2, SafariMood.ANGRY), (0, SafariMood.CALM), (-2, SafariMood.EATING)],
)
def test_mood_is_derived_from_the_stage_so_it_needs_no_extra_state(
    stage: int, expected: SafariMood
) -> None:
    assert describe_mood(stage) is expected


def test_invalid_patience_or_spent_turns_are_rejected() -> None:
    with pytest.raises(ValueError, match=r"^patience must be positive$"):
        throw(SafariAction.ROCK, catch_stage=0, patience=0, spent_turns=0)
    with pytest.raises(ValueError, match=r"^spent turns cannot be negative$"):
        throw(SafariAction.ROCK, catch_stage=0, patience=4, spent_turns=-1)
