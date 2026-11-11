"""Entrenadores generados: clases originales y equipos a la altura del jugador."""

from __future__ import annotations

import random

from hypothesis import given, settings
from hypothesis import strategies as st

from pokedex_cli.domain.battle.trainers import (
    TRAINER_CLASSES,
    SpeciesCandidate,
    TrainerPlan,
    plan_trainer,
)
from pokedex_cli.domain.battle.typechart import TYPES
from tests.domain.battle.test_engine import ScriptedRandom


def candidate(species: str, *types: str, form: str | None = None) -> SpeciesCandidate:
    return SpeciesCandidate(species=species, form=form or species, types=types or ("normal",))


NORMALS = [candidate(name) for name in ("rattata", "pidgey", "meowth", "eevee", "snorlax")]
# Nombres de personajes de los juegos (también en su versión española) que
# no deben salir nunca: líderes, Alto Mando, rivales y profesores.
GAME_CHARACTERS = {
    "Petra", "Marcial", "Erico", "Candela", "Norman", "Alana", "Galano", "Roco",
    "Gardenia", "Brega", "Fantina", "Acerón", "Inverna", "Lectro", "Pegaso", "Antón",
    "Blanca", "Morti", "Aníbal", "Yasmina", "Fredo", "Débora", "Brock", "Misty",
    "Erika", "Koga", "Sabrina", "Blaine", "Giovanni", "Bruno", "Agatha", "Lance",
    "Sixto", "Fátima", "Nívea", "Dracón", "Plubio", "Máximo", "Gaia", "Fausto",
    "Delos", "Cintia", "Azul", "Rojo", "Oak", "Abedul", "Serbal", "Encina", "Ciprés",
    "Aura", "Blasco", "Israel", "Maya", "León", "Guzmán", "Iris", "Hiedra", "Lirio",
}  # fmt: skip


def test_classes_are_original_spanish_and_have_favourite_types() -> None:
    titles = [trainer_class.title for trainer_class in TRAINER_CLASSES]
    assert len(titles) == len(set(titles))
    assert {
        "Montañero", "Nadadora", "Pescador", "Ornitólogo", "Cazabichos", "Karateka",
        "Científica", "Médium", "Domadora", "Jardinera", "Joven Guay", "Chica Guay",
    } <= set(titles)  # fmt: skip
    for trainer_class in TRAINER_CLASSES:
        assert set(trainer_class.types) <= set(TYPES)
        assert trainer_class.names and len(set(trainer_class.names)) == len(trainer_class.names)
        assert not GAME_CHARACTERS & set(trainer_class.names)
    open_classes = {c.title for c in TRAINER_CLASSES if not c.types}
    assert open_classes == {"Joven Guay", "Chica Guay"}


def test_no_pool_or_no_team_means_no_trainer() -> None:
    assert plan_trainer([], [10], ScriptedRandom()) is None
    assert plan_trainer(NORMALS, [], ScriptedRandom()) is None


def test_a_full_plan_with_scripted_rolls() -> None:
    # Solo hay especies Normal: valen Joven Guay y Chica Guay (índice 0 → Joven).
    # 0,7: mismo tamaño que el jugador. Niveles: media 20 + (−3, 0, +1).
    rng = ScriptedRandom(floats=(0.7,), ints=(0, 0, 4, 0, 1, -3, 0, 1))
    plan = plan_trainer(NORMALS, [10, 20, 30], rng)
    assert rng.exhausted
    assert plan == TrainerPlan(
        name="Anselmo",
        trainer_class="Joven Guay",
        members=(("snorlax", "snorlax", 17), ("eevee", "eevee", 20), ("pidgey", "pidgey", 21)),
    )
    assert plan.display_name == "Joven Guay Anselmo"


def test_sometimes_one_member_fewer_but_never_none() -> None:
    smaller = plan_trainer(NORMALS, [10, 20, 30], ScriptedRandom(floats=(0.2,), ints=(0,) * 6))
    assert smaller is not None and len(smaller.members) == 2
    single = plan_trainer(NORMALS, [40], ScriptedRandom(ints=(0, 0, 0, 0)))
    assert single is not None and len(single.members) == 1


def test_levels_round_the_player_average_and_stay_in_range() -> None:
    low = plan_trainer(NORMALS[:1], [3, 4], ScriptedRandom(floats=(0.9,), ints=(0, 0, 0, -3)))
    # Media 3,5 → 4; 4 − 3 = 1 → mínimo 2. Solo hay una especie: un miembro.
    assert low is not None and low.members == (("rattata", "rattata", 2),)
    high = plan_trainer(NORMALS[:1], [100], ScriptedRandom(ints=(0, 0, 0, 1)))
    assert high is not None and high.members[0][2] == 100


def test_favourite_types_go_first_and_the_rest_fills_the_team() -> None:
    pool = [
        candidate("goldeen", "water"),
        candidate("charmander", "fire"),
        candidate("rattata"),
        candidate("magikarp", "water"),
    ]
    # Elegibles por orden: Nadadora, Pescador, Joven Guay, Chica Guay.
    rng = ScriptedRandom(floats=(0.9,), ints=(0, 0, 0, 0, 0, 0, 0, 0))
    plan = plan_trainer(pool, [20, 20, 20], rng)
    assert rng.exhausted
    assert plan is not None and plan.trainer_class == "Nadadora"
    assert [member[0] for member in plan.members] == ["goldeen", "magikarp", "charmander"]


def test_only_classes_with_matching_species_are_eligible() -> None:
    bugs = [candidate(name, "bug") for name in ("caterpie", "weedle", "venonat")]
    titles = {
        plan.trainer_class
        for seed in range(80)
        if (plan := plan_trainer(bugs, [12, 15], random.Random(seed))) is not None
    }
    assert titles == {"Cazabichos", "Joven Guay", "Chica Guay"}


def test_the_same_species_never_appears_twice() -> None:
    pool = [
        candidate("rotom", "electric", "ghost"),
        candidate("rotom", "electric", "water", form="rotom-wash"),
        candidate("rotom", "electric", "fire", form="rotom-heat"),
        candidate("pikachu", "electric"),
    ]
    for seed in range(40):
        plan = plan_trainer(pool, [30] * 6, random.Random(seed))
        assert plan is not None
        species = [member[0] for member in plan.members]
        assert len(species) == len(set(species)) <= 2


def test_the_plan_depends_on_the_pool_contents_not_its_order() -> None:
    pool = NORMALS + [candidate("squirtle", "water"), candidate("onix", "rock", "ground")]
    forward = plan_trainer(pool, [25, 30], random.Random(5))
    backward = plan_trainer(list(reversed(pool)), [25, 30], random.Random(5))
    assert forward == backward


@settings(max_examples=60, deadline=None)
@given(
    pool=st.lists(
        st.builds(
            candidate,
            st.sampled_from(["a", "b", "c", "d", "e", "f", "g"]),
            st.sampled_from(TYPES),
        ),
        min_size=1,
        max_size=10,
    ),
    levels=st.lists(st.integers(min_value=1, max_value=100), min_size=1, max_size=6),
    seed=st.integers(min_value=0, max_value=10_000),
)
def test_any_plan_is_well_formed(
    pool: list[SpeciesCandidate], levels: list[int], seed: int
) -> None:
    plan = plan_trainer(pool, levels, random.Random(seed))
    assert plan is not None
    distinct = {c.species for c in pool}
    wanted = min(len(levels), 6)
    assert len(plan.members) in {min(wanted, len(distinct)), min(wanted - 1, len(distinct))}
    assert len(plan.members) >= 1
    species = [member[0] for member in plan.members]
    assert len(set(species)) == len(species) and set(species) <= distinct
    average = (2 * sum(levels) + len(levels)) // (2 * len(levels))
    for _, _, level in plan.members:
        assert max(2, min(100, average - 3)) <= level <= max(2, min(100, average + 1))
