"""Movesets de las capturas: reglas compartidas con el combate y gestión del propio."""

import json

import pytest

from pokedex_cli.application.movesets import (
    LearnableMove,
    ManageMoveset,
    MovesetStatus,
    capture_moves,
    level_moves,
    species_with_learnset,
)
from pokedex_cli.domain.battle.model import DamageClass, LearnsetEntry, Move, StatChange
from pokedex_cli.domain.battle.moves import FALLBACK_BY_TYPE, TACKLE
from pokedex_cli.domain.battle.movesets import smart_moveset


def status_move(slug: str, name: str, **extra: object) -> Move:
    return Move(
        slug=slug,
        name=name,
        type="normal",
        damage_class=DamageClass.STATUS,
        power=None,
        accuracy=100,
        pp=20,
        **extra,
    )


SCRATCH = Move("scratch", "Arañazo", "normal", DamageClass.PHYSICAL, 40, 100, 35)
EMBER = FALLBACK_BY_TYPE["fire"]
FLAMETHROWER = Move("flamethrower", "Lanzallamas", "fire", DamageClass.SPECIAL, 90, 100, 15)
GROWL = status_move(
    "growl",
    "Gruñido",
    target="all-opponents",
    category="net-good-stats",
    stat_changes=(StatChange("atk", -1),),
)
SMOKESCREEN = status_move(
    "smokescreen",
    "Pantalla de Humo",
    category="net-good-stats",
    stat_changes=(StatChange("accuracy", -1),),
)
TRANSFORM = status_move("transform", "Transformación", category="unique")
LEARNSET = [["scratch", 1], ["growl", 1], ["ember", 7], ["smokescreen", 13], ["flamethrower", 31]]
CATALOG = (SCRATCH, EMBER, FLAMETHROWER, GROWL, SMOKESCREEN, TRANSFORM)


class Captures:
    def __init__(self, *rows: dict) -> None:
        self.rows = list(rows)

    def resolve(self, identifier, form):
        if identifier.isdigit():
            return next((row for row in self.rows if row["id"] == int(identifier)), None)
        return next((row for row in self.rows if row["species"] == identifier), None)


class Species:
    def __init__(self, data, refreshed=None) -> None:
        self.data = data
        self.refreshed = refreshed
        self.calls: list[tuple[str, str, bool]] = []

    def execute(self, species, form, *, refresh=False):
        self.calls.append((species, form, refresh))
        return self.refreshed if refresh else self.data


class Moves:
    def __init__(self, *moves: Move) -> None:
        self.catalog = {move.slug: move for move in moves}

    def execute_many(self, slugs):
        return {slug: self.catalog[slug] for slug in slugs if slug in self.catalog}


class Store:
    def __init__(self, movesets=None) -> None:
        self.movesets = dict(movesets or {})
        self.replaced: list[tuple[int, tuple[str, ...]]] = []

    def get(self, capture_id):
        return tuple(self.movesets.get(capture_id, ()))

    def replace(self, capture_id, slugs):
        self.replaced.append((capture_id, tuple(slugs)))
        self.movesets[capture_id] = tuple(slugs)


def charmander(**overrides: object) -> dict:
    row = {
        "id": 7,
        "species": "charmander",
        "form": "regular",
        "level": 15,
        "nickname": "Chispa",
        "types": ["fire"],
    }
    row.update(overrides)
    return row


def species_data(learnset=LEARNSET) -> dict:
    return {"types": '["fire"]', "level_moves": json.dumps(learnset)}


def manager(*, store=None, data=None, catalog=CATALOG, rows=None) -> ManageMoveset:
    return ManageMoveset(
        captures=Captures(*(rows or [charmander()])),
        species=Species(species_data() if data is None else data),
        moves=Moves(*catalog),
        movesets=store or Store(),
    )


# --- Reglas compartidas --------------------------------------------------------


def test_level_moves_reads_cached_json_and_skips_malformed_entries() -> None:
    raw = '[["scratch", 1], ["ember", 0], ["bad"], [3, 4], ["growl", true], ["leer", 5]]'

    assert level_moves({"level_moves": raw}) == (("scratch", 1), ("ember", 1), ("leer", 5))
    assert level_moves({"level_moves": [["tackle", 1]]}) == (("tackle", 1),)
    assert level_moves({"level_moves": None}) == ()
    assert level_moves({"level_moves": "{no es json"}) == ()
    assert level_moves(None) == ()


def test_a_profile_from_before_battles_is_refreshed_once_for_its_learnset() -> None:
    species = Species({"level_moves": None, "hp": 39}, refreshed={"level_moves": "[]", "hp": 39})

    data = species_with_learnset(species, "charmander", "regular")

    assert data == {"level_moves": "[]", "hp": 39}
    assert species.calls == [("charmander", "regular", False), ("charmander", "regular", True)]


def test_offline_refresh_keeps_the_profile_that_was_cached() -> None:
    species = Species({"level_moves": None, "hp": 39}, refreshed=None)

    assert species_with_learnset(species, "charmander", "regular") == {
        "level_moves": None,
        "hp": 39,
    }


def test_a_known_empty_learnset_is_not_refreshed_again() -> None:
    species = Species({"level_moves": "[]"})

    species_with_learnset(species, "ditto", "regular")

    assert species.calls == [("ditto", "regular", False)]


def test_custom_moveset_wins_and_carries_no_learnset() -> None:
    plan = capture_moves(
        custom=("growl", "ember"),
        entries=level_moves(species_data()),
        level=15,
        types=("fire",),
        catalog={move.slug: move for move in CATALOG},
    )

    assert plan.custom is True
    assert plan.moves == (GROWL, EMBER)
    assert plan.learnset == ()


def test_automatic_moveset_is_smart_and_keeps_the_whole_known_learnset() -> None:
    catalog = {move.slug: move for move in CATALOG}

    plan = capture_moves(
        custom=(), entries=level_moves(species_data()), level=15, types=("fire",), catalog=catalog
    )

    assert plan.custom is False
    assert plan.moves == smart_moveset([SCRATCH, GROWL, EMBER, SMOKESCREEN], ("fire",))
    # Lo que aprenderá más adelante también viaja: el motor lo usa al subir de nivel.
    assert LearnsetEntry(31, FLAMETHROWER) in plan.learnset
    assert len(plan.learnset) == len(LEARNSET)


def test_without_move_data_the_type_reserve_is_used() -> None:
    plan = capture_moves(
        custom=(), entries=level_moves(species_data()), level=15, types=("fire",), catalog={}
    )

    assert plan.moves == (EMBER, TACKLE)
    assert plan.learnset == ()


def test_a_custom_moveset_left_without_damaging_moves_leads_with_tackle() -> None:
    plan = capture_moves(
        custom=("growl", "smokescreen", "ember"),
        entries=(),
        level=15,
        types=("fire",),
        catalog={"growl": GROWL, "smokescreen": SMOKESCREEN},
    )

    assert plan.moves == (TACKLE, GROWL, SMOKESCREEN)


# --- Ver ---------------------------------------------------------------------------


def test_view_shows_current_moves_learnable_ones_and_the_next_with_levels() -> None:
    view = manager().view("7")

    assert view is not None
    assert (view.capture_id, view.name, view.level, view.types) == (7, "Chispa", 15, ("fire",))
    assert view.custom is False
    assert view.moves == smart_moveset([SCRATCH, GROWL, EMBER, SMOKESCREEN], ("fire",))
    assert view.learnable == (
        LearnableMove("scratch", 1, SCRATCH),
        LearnableMove("growl", 1, GROWL),
        LearnableMove("ember", 7, EMBER),
        LearnableMove("smokescreen", 13, SMOKESCREEN),
    )
    assert view.upcoming == (LearnableMove("flamethrower", 31, FLAMETHROWER),)


def test_view_resolves_by_species_and_reports_a_custom_moveset() -> None:
    view = manager(store=Store({7: ("ember", "growl")})).view("charmander")

    assert view is not None
    assert view.custom is True
    assert view.moves == (EMBER, GROWL)


def test_a_relearned_move_counts_at_its_latest_reached_level_and_is_not_upcoming() -> None:
    learnset = [["ember", 7], ["scratch", 1], ["ember", 12], ["ember", 40]]

    view = manager(data=species_data(learnset)).view("7")

    assert view is not None
    assert [(entry.slug, entry.level) for entry in view.learnable] == [
        ("scratch", 1),
        ("ember", 12),
    ]
    assert view.upcoming == ()


def test_unknown_capture_has_no_view() -> None:
    assert manager().view("99") is None


def test_learnable_moves_tell_whether_the_engine_supports_them() -> None:
    assert LearnableMove("ember", 7, EMBER).supported is True
    assert LearnableMove("transform", 1, TRANSFORM).supported is False
    assert LearnableMove("ember", 7, None).supported is False


# --- Fijar y quitar ------------------------------------------------------------------


def test_set_saves_moves_given_by_slug_or_by_spanish_name() -> None:
    store = Store()

    result = manager(store=store).set(7, ["ascuas", " Arañazo ", "pantalla de humo"])

    assert result.status is MovesetStatus.SAVED
    assert result.ok
    assert result.moves == (EMBER, SCRATCH, SMOKESCREEN)
    assert store.replaced == [(7, ("ember", "scratch", "smokescreen"))]


@pytest.mark.parametrize(
    ("chosen", "status", "reason"),
    [
        ([], MovesetStatus.WRONG_COUNT, "Elige entre 1 y 4 movimientos."),
        (
            ["scratch", "growl", "ember", "smokescreen", "tackle"],
            MovesetStatus.WRONG_COUNT,
            "Elige entre 1 y 4 movimientos.",
        ),
        (
            ["flamethrower"],
            MovesetStatus.NOT_LEARNABLE,
            "Chispa no puede aprender «flamethrower» a su nivel.",
        ),
        (["ember", "Ascuas"], MovesetStatus.REPEATED, "No repitas movimientos."),
        (
            ["growl", "smokescreen"],
            MovesetStatus.NO_DAMAGING_MOVE,
            "Elige al menos un movimiento que haga daño.",
        ),
    ],
)
def test_set_rejects_invalid_movesets_without_touching_the_saved_one(
    chosen, status, reason
) -> None:
    store = Store({7: ("ember",)})

    result = manager(store=store).set(7, chosen)

    assert (result.status, result.reason) == (status, reason)
    assert not result.ok
    assert store.replaced == []


def test_set_rejects_moves_the_engine_cannot_play() -> None:
    data = species_data([*LEARNSET, ["transform", 1]])
    store = Store()

    result = manager(store=store, data=data).set(7, ["ember", "transform"])

    assert result.status is MovesetStatus.UNSUPPORTED
    assert result.reason == "Transformación aún no funciona en combate."
    assert store.replaced == []


def test_set_needs_the_move_data_to_validate_it() -> None:
    store = Store()
    catalog = tuple(move for move in CATALOG if move.slug != "smokescreen")

    result = manager(store=store, catalog=catalog).set(7, ["ember", "smokescreen"])

    assert result.status is MovesetStatus.NO_MOVE_DATA
    assert store.replaced == []


def test_a_move_it_already_knows_stays_valid_outside_the_learnset() -> None:
    # Sin learnset lucha con la reserva por tipo, Placaje incluido.
    store = Store()
    data = {"types": '["fire"]', "level_moves": "[]"}

    result = manager(store=store, data=data).set(7, ["Placaje"])

    assert result.status is MovesetStatus.SAVED
    assert store.replaced == [(7, ("tackle",))]


def test_set_and_reset_report_unknown_captures() -> None:
    store = Store()
    use_case = manager(store=store)

    assert use_case.set(99, ["ember"]).status is MovesetStatus.NOT_FOUND
    assert use_case.reset(99).status is MovesetStatus.NOT_FOUND
    assert store.replaced == []


def test_reset_goes_back_to_the_automatic_moveset() -> None:
    store = Store({7: ("ember",)})

    result = manager(store=store).reset(7)

    assert result.status is MovesetStatus.RESET
    assert store.replaced == [(7, ())]
