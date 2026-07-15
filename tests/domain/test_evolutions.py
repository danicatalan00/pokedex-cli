import random

from pokedex_cli.domain.evolutions import EvolutionOption, select_level_evolution


def test_linear_evolution_unlocks_at_required_level() -> None:
    option = EvolutionOption("ivysaur", "regular", 16)
    assert select_level_evolution([option], 15, random.Random(1)) is None
    assert select_level_evolution([option], 16, random.Random(1)) == option


def test_branched_evolution_chooses_only_earliest_unlocked_tier() -> None:
    options = [
        EvolutionOption("branch-a", "regular", 20),
        EvolutionOption("branch-b", "regular", 20),
        EvolutionOption("late", "regular", 30),
    ]
    selected = {select_level_evolution(options, 100, random.Random(seed)) for seed in range(20)}
    assert selected == {options[0], options[1]}


def test_no_level_options_means_no_automatic_evolution() -> None:
    assert select_level_evolution([], 100, random.Random(1)) is None
