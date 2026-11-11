"""Casos de uso del combate con dobles: preparación, inicio, turnos y sesiones.

La integración con SQLite real vive en ``test_battle_sqlite.py``; aquí se fijan
las reglas de la aplicación sin BD: qué se prepara, qué se valida, qué se
guarda y en qué orden.
"""

import json
import random
from dataclasses import asdict, replace
from datetime import datetime, timezone

import pytest

from pokedex_cli.application.battle import (
    Auto,
    BattleSetup,
    BattleStatus,
    BattleView,
    CaptureContext,
    DescribeBattle,
    ExhibitionSession,
    Fight,
    Flee,
    PersistedSession,
    PrepareBattle,
    Replace,
    StartBattle,
    Switch,
    TakeBattleTurn,
    ThrowBall,
    TurnOutcome,
)
from pokedex_cli.application.capture import CaptureResult, CaptureStatus
from pokedex_cli.domain.battle import codec
from pokedex_cli.domain.battle.combatants import build_combatant, build_stats
from pokedex_cli.domain.battle.mechanics import battle_catch_multiplier
from pokedex_cli.domain.battle.model import (
    BallThrown,
    BattleEnded,
    BattleKind,
    BattleStarted,
    BattleState,
    DamageClass,
    EscapeAttempted,
    ExperienceGained,
    LeveledUp,
    Move,
    MoveUsed,
    OpponentFled,
    Outcome,
    Phase,
    SentOut,
    Side,
    Stats,
    TurnStarted,
    Volatile,
)
from pokedex_cli.domain.battle.moves import FALLBACK_BY_TYPE, TACKLE
from pokedex_cli.domain.battle.movesets import fallback_moveset, smart_moveset, wild_moveset
from pokedex_cli.domain.battle.trainers import SpeciesCandidate, plan_trainer
from pokedex_cli.domain.individuality import (
    derive_ability,
    derive_gender,
    derive_ivs_nature,
    nature_by_name,
)
from pokedex_cli.domain.progression import MAX_LEVEL, experience_for_level, level_for_experience

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
SEEN_AT = "2026-11-11T08:00:00+00:00"
ZERO_IVS = {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
CHARMANDER = {"hp": 39, "atk": 52, "def": 43, "spa": 60, "spd": 50, "spe": 65}
SHEDINJA = {"hp": 1, "atk": 90, "def": 45, "spa": 30, "spd": 30, "spe": 1}
MAGIKARP = {"hp": 20, "atk": 10, "def": 55, "spa": 15, "spd": 20, "spe": 80}
EMBER = FALLBACK_BY_TYPE["fire"]
SCRATCH = Move("scratch", "Arañazo", "normal", DamageClass.PHYSICAL, 40, 100, 35)
GROWL = Move(
    "growl",
    "Gruñido",
    "normal",
    DamageClass.STATUS,
    None,
    100,
    40,
    target="all-opponents",
    category="net-good-stats",
)
TRANSFORM = Move(
    "transform", "Transformación", "normal", DamageClass.STATUS, None, None, 10, category="unique"
)
SOLAR_BEAM = Move("solar-beam", "Rayo Solar", "grass", DamageClass.SPECIAL, 120, 100, 10)
SPLASH = Move(
    "splash", "Salpicadura", "normal", DamageClass.STATUS, None, None, 40, category="unique"
)
CATALOG = {
    move.slug: move for move in (EMBER, SCRATCH, GROWL, TACKLE, TRANSFORM, SOLAR_BEAM, SPLASH)
}
CHARMANDER_LEARNSET = [["scratch", 1], ["growl", 1], ["ember", 7]]


# --- Dobles de preparación ---------------------------------------------------------


class Collection:
    def __init__(self, team=(), others=()) -> None:
        self.team_rows = list(team)
        self.rows = [*team, *others]

    def team(self):
        return [dict(row) for row in self.team_rows]

    def resolve(self, identifier, form):
        if not identifier.isdigit():
            return None
        return next((dict(row) for row in self.rows if row["id"] == int(identifier)), None)


class Species:
    def __init__(self, profiles=None, refreshed=None) -> None:
        self.profiles = dict(profiles or {})
        self.refreshed = dict(refreshed or {})
        self.calls: list[tuple[str, bool]] = []

    def execute(self, species, form, *, refresh=False):
        self.calls.append((species, refresh))
        return (self.refreshed if refresh else self.profiles).get(species)


class Moves:
    def __init__(self, catalog=CATALOG) -> None:
        self.catalog = dict(catalog)
        self.requests: list[list[str]] = []

    def execute_many(self, slugs):
        wanted = list(slugs)
        self.requests.append(wanted)
        return {slug: self.catalog[slug] for slug in wanted if slug in self.catalog}


class Movesets:
    def __init__(self, movesets=None) -> None:
        self.movesets = dict(movesets or {})

    def get(self, capture_id):
        return tuple(self.movesets.get(capture_id, ()))


class Pool:
    def __init__(self, *entries: dict) -> None:
        self.entries = list(entries)

    def battle_pool(self):
        return [dict(entry) for entry in self.entries]


def capture_row(capture_id, species, *, level, base=CHARMANDER, types=("fire",), **extra):
    row = {
        "id": capture_id,
        "species": species,
        "form": "regular",
        "level": level,
        "nickname": None,
        "experience": experience_for_level("medium", level),
        "shiny": 0,
        "gender": "male",
        "ability": "blaze",
        "ivs": dict(ZERO_IVS),
        "nature": nature_by_name("hardy"),
        "types": None if types is None else list(types),
        "growth_rate": "medium",
        "base_experience": 62,
        **(base or dict.fromkeys(CHARMANDER)),
    }
    row.update(extra)
    return row


def profile(types, base, learnset, **extra):
    data = {
        "types": json.dumps(list(types)),
        **base,
        "level_moves": None if learnset is None else json.dumps(learnset),
        "growth_rate": "medium",
        "base_experience": 64,
        "capture_rate": 45,
        "encounter_level": 5,
        "is_legendary": 0,
        "is_mythical": 0,
        "gender_rate": 4,
        "abilities": json.dumps(["blaze"]),
    }
    data.update(extra)
    return data


def pool_entry(species, types, *, form="regular", legendary=False, mythical=False):
    return {
        "species": species,
        "form": form,
        "types": list(types),
        "is_legendary": legendary,
        "is_mythical": mythical,
    }


def encounter(species="magikarp", **overrides):
    data = {
        "species": species,
        "form": "regular",
        "shiny": False,
        "seen_at": SEEN_AT,
        "captured": False,
    }
    data.update(overrides)
    return data


class Preparation:
    """``PrepareBattle`` con sus dobles a mano para mirar qué se pidió."""

    def __init__(self, *, team, others, profiles, refreshed, movesets, pool, rng) -> None:
        self.progress: list[str] = []
        self.species = Species(profiles, refreshed)
        self.moves = Moves()
        self.use_case = PrepareBattle(
            collection=Collection(team, others),
            species=self.species,
            moves=self.moves,
            movesets=Movesets(movesets),
            pool=Pool(*pool),
            rng=random.Random(rng),
            on_progress=self.progress.append,
        )


def preparation(
    *, team=(), others=(), profiles=None, refreshed=None, movesets=None, pool=(), rng=1
):
    return Preparation(
        team=team,
        others=others,
        profiles=profiles,
        refreshed=refreshed,
        movesets=movesets,
        pool=pool,
        rng=rng,
    )


def preparer(**kwargs):
    prepared = preparation(**kwargs)
    return prepared.use_case, prepared.progress


# --- Preparar: equipo ------------------------------------------------------------


def test_player_side_has_one_combatant_per_member_in_team_order() -> None:
    team = [
        capture_row(7, "charmander", level=15, nickname="Chispa"),
        capture_row(3, "mr-mime", level=12, types=("psychic", "fairy")),
    ]
    prepared = preparation(
        team=team,
        profiles={
            "charmander": profile(["fire"], CHARMANDER, CHARMANDER_LEARNSET),
            "mr-mime": profile(["psychic", "fairy"], CHARMANDER, []),
        },
    )

    first, second = prepared.use_case.player_side()

    assert [(c.key, c.name, c.capture_id, c.level) for c in (first, second)] == [
        ("p0", "Chispa", 7, 15),
        ("p1", "Mr Mime", 3, 12),
    ]
    zero = Stats(0, 0, 0, 0, 0, 0)
    assert first.stats == build_stats(Stats.from_mapping(CHARMANDER), zero, 15, "hardy")
    assert first.hp == first.max_hp
    assert prepared.progress == [
        "Preparando a Chispa…",
        "Preparando a Mr Mime…",
        "Preparando los movimientos…",
    ]
    # Todos los movimientos del combate se piden de una vez (descargas en paralelo).
    assert prepared.moves.requests == [["scratch", "growl", "ember"]]


def test_automatic_moveset_is_smart_with_learnset_and_a_custom_one_is_kept() -> None:
    learnset = CHARMANDER_LEARNSET + [["solar-beam", 40]]
    team = [capture_row(7, "charmander", level=15), capture_row(8, "charmander", level=15)]
    use_case, _ = preparer(
        team=team,
        profiles={"charmander": profile(["fire"], CHARMANDER, learnset)},
        movesets={8: ("growl", "scratch")},
    )

    automatic, custom = use_case.player_side()

    assert automatic.custom_moves is False
    assert [slot.move for slot in automatic.moves] == list(
        smart_moveset([SCRATCH, GROWL, EMBER], ("fire",))
    )
    assert SOLAR_BEAM in [entry.move for entry in automatic.learnset]
    assert custom.custom_moves is True
    assert [slot.move for slot in custom.moves] == [GROWL, SCRATCH]
    assert custom.learnset == ()


def test_a_profile_without_learnset_is_refreshed_once() -> None:
    old = profile(["fire"], CHARMANDER, None)
    fresh = profile(["fire"], CHARMANDER, CHARMANDER_LEARNSET)
    prepared = preparation(
        team=[capture_row(7, "charmander", level=15)],
        profiles={"charmander": old},
        refreshed={"charmander": fresh},
    )

    (combatant,) = prepared.use_case.player_side()

    assert prepared.species.calls == [("charmander", False), ("charmander", True)]
    assert EMBER in [slot.move for slot in combatant.moves]


def test_without_species_data_a_capture_fights_with_base_50_as_normal_type() -> None:
    use_case, _ = preparer(team=[capture_row(5, "missingno", level=10, base=None, types=None)])

    (combatant,) = use_case.player_side()

    assert combatant.base_stats == Stats(50, 50, 50, 50, 50, 50)
    assert combatant.types == ("normal",)
    assert [slot.move for slot in combatant.moves] == list(fallback_moveset(("normal",)))


def test_experience_never_starts_below_the_floor_of_its_level() -> None:
    use_case, _ = preparer(team=[capture_row(7, "charmander", level=20, experience=0)])

    (combatant,) = use_case.player_side()

    assert combatant.experience == experience_for_level("medium", 20)


# --- Preparar: salvaje ---------------------------------------------------------------


def wild_profiles(**magikarp_extra):
    return {
        "charmander": profile(["fire"], CHARMANDER, CHARMANDER_LEARNSET),
        "magikarp": profile(
            ["water"],
            MAGIKARP,
            [["splash", 1], ["tackle", 15]],
            capture_rate=255,
            gender_rate=4,
            abilities=json.dumps(["swift-swim", "rattled"]),
            **magikarp_extra,
        ),
    }


def test_wild_reports_why_it_cannot_start() -> None:
    team = [capture_row(7, "charmander", level=15)]
    use_case, _ = preparer(team=team, profiles=wild_profiles())
    without_team, _ = preparer(profiles=wild_profiles())
    without_data, _ = preparer(team=team, profiles={})
    incomplete = wild_profiles()
    incomplete["magikarp"]["spe"] = None
    without_stats, _ = preparer(team=team, profiles=incomplete)

    assert use_case.wild(None) is BattleStatus.NO_ENCOUNTER
    assert use_case.wild(encounter(captured=True)) is BattleStatus.ALREADY_CAPTURED
    assert without_team.wild(encounter()) is BattleStatus.NO_TEAM
    assert without_data.wild(encounter()) is BattleStatus.NO_SPECIES_DATA
    assert without_stats.wild(encounter()) is BattleStatus.NO_SPECIES_DATA


def test_wild_rival_matches_the_capture_level_and_a_fixed_individual() -> None:
    team = [capture_row(7, "charmander", level=15)]
    use_case, progress = preparer(team=team, profiles=wild_profiles(encounter_level=12))

    setup = use_case.wild(encounter())
    again = use_case.wild(encounter())

    assert isinstance(setup, BattleSetup)
    assert setup == again  # misma semilla, mismo individuo
    seed = f"wild:magikarp:regular:{SEEN_AT}"
    ivs, nature = derive_ivs_nature(seed)
    wild = setup.state.opponent.current
    assert (wild.key, wild.name, wild.level, wild.types) == ("o0", "Magikarp", 12, ("water",))
    assert wild.ivs == Stats.from_mapping(ivs)
    assert wild.nature == nature.name
    assert wild.gender == derive_gender(seed, 4)
    assert wild.ability == derive_ability(seed, ("swift-swim", "rattled"))
    assert wild.experience == experience_for_level("medium", 12)
    assert setup.kind is BattleKind.WILD
    assert setup.state.award_experience is True
    assert setup.encounter_seen_at == SEEN_AT
    assert setup.capture == CaptureContext(
        capture_rate=255,
        speed=80,
        is_legendary=False,
        is_mythical=False,
        growth_rate="medium",
        gender_rate=4,
        abilities=("swift-swim", "rattled"),
        encounter_level=12,
    )
    assert progress[0] == "Preparando a Magikarp salvaje…"


def test_a_legendary_wild_appears_at_least_at_level_50() -> None:
    team = [capture_row(7, "charmander", level=15)]
    use_case, _ = preparer(team=team, profiles=wild_profiles(is_legendary=1, encounter_level=5))

    setup = use_case.wild(encounter())

    assert isinstance(setup, BattleSetup)
    assert setup.state.opponent.current.level == 50


def test_wild_moveset_is_the_last_learned_or_the_type_reserve() -> None:
    team = [capture_row(7, "charmander", level=15)]
    profiles = wild_profiles()
    profiles["ditto"] = profile(["normal"], MAGIKARP, [["transform", 1]])
    use_case, _ = preparer(team=team, profiles=profiles)

    magikarp = use_case.wild(encounter())
    ditto = use_case.wild(encounter("ditto"))

    assert isinstance(magikarp, BattleSetup) and isinstance(ditto, BattleSetup)
    # A nivel 5 solo sabe Salpicadura: Placaje ocupa el hueco del dañino (Gen 3).
    expected = wild_moveset([("splash", 1), ("tackle", 15)], 5, CATALOG)
    assert expected == (TACKLE, SPLASH)
    assert tuple(slot.move for slot in magikarp.state.opponent.current.moves) == expected
    # Solo con movimientos sin soporte, la reserva por tipo: nunca sin atacar.
    assert [slot.move for slot in ditto.state.opponent.current.moves] == [TACKLE]


# --- Preparar: entrenador ------------------------------------------------------------


def test_trainer_needs_a_team_and_an_ordinary_pool() -> None:
    team = [capture_row(7, "charmander", level=15)]
    without_team, _ = preparer(pool=[pool_entry("rattata", ["normal"])])
    special_only, _ = preparer(
        team=team,
        pool=[
            pool_entry("mewtwo", ["psychic"], legendary=True),
            pool_entry("mew", ["psychic"], mythical=True),
            pool_entry("charizard", ["fire", "flying"], form="mega-y"),
            pool_entry("pikachu", ["electric"], form="gmax"),
        ],
    )

    assert without_team.trainer() is BattleStatus.NO_TEAM
    assert special_only.trainer() is BattleStatus.NO_TRAINER_POOL


def test_trainer_brings_pool_species_at_the_team_level_under_its_name() -> None:
    team = [capture_row(7, "charmander", level=20), capture_row(8, "charmander", level=20)]
    rattata = {"hp": 30, "atk": 56, "def": 35, "spa": 25, "spd": 35, "spe": 72}
    pool = [pool_entry("rattata", ["normal"]), pool_entry("mewtwo", ["psychic"], legendary=True)]
    profiles = {
        "charmander": profile(["fire"], CHARMANDER, CHARMANDER_LEARNSET),
        "rattata": profile(["normal"], rattata, [["tackle", 1]]),
    }
    use_case, progress = preparer(team=team, profiles=profiles, pool=pool, rng=5)

    setup = use_case.trainer()

    plan = plan_trainer(
        [SpeciesCandidate("rattata", "regular", ("normal",))], [20, 20], random.Random(5)
    )
    assert plan is not None
    assert isinstance(setup, BattleSetup)
    assert setup.kind is BattleKind.TRAINER
    assert setup.trainer == plan.display_name
    assert setup.capture is None and setup.encounter_seen_at is None
    members = setup.state.opponent.team
    assert [(m.key, m.species, m.level) for m in members] == [
        (f"o{index}", species, level) for index, (species, _, level) in enumerate(plan.members)
    ]
    assert all(0 <= value <= 31 for m in members for value in m.ivs.as_mapping().values())
    assert all([slot.move for slot in m.moves] == [TACKLE] for m in members)
    assert progress[0] == f"Preparando a {plan.display_name}…"


# --- Preparar: exhibición ----------------------------------------------------------


def exhibition_preparer(**extra):
    team = [capture_row(7, "charmander", level=15, nickname="Chispa")]
    others = [capture_row(9, "magikarp", level=8, base=MAGIKARP, types=("water",))]
    profiles = wild_profiles()
    return preparer(team=team, others=others, profiles=profiles, **extra)


def test_exhibition_pits_real_captures_or_fixed_species_without_experience() -> None:
    use_case, _ = exhibition_preparer()

    captures = use_case.exhibition("7", "#9")
    species = use_case.exhibition("7", "Magikarp")
    leveled = use_case.exhibition("9", "magikarp", level=30)

    assert isinstance(captures, BattleState)
    assert captures.kind is BattleKind.EXHIBITION
    assert captures.award_experience is False
    assert (captures.player.current.name, captures.player.current.level) == ("Chispa", 15)
    assert (captures.opponent.current.key, captures.opponent.current.level) == ("o0", 8)
    assert isinstance(species, BattleState)
    ivs, nature = derive_ivs_nature("exhibition:magikarp:regular:50")
    assert species.opponent.current.level == 50
    assert species.opponent.current.ivs == Stats.from_mapping(ivs)
    assert species.opponent.current.nature == nature.name
    assert isinstance(leveled, BattleState)
    assert leveled.player.current.level == leveled.opponent.current.level == 30
    assert leveled.player.current.experience == experience_for_level("medium", 30)


def test_exhibition_defaults_to_the_team_leader_against_a_random_cached_species() -> None:
    use_case, _ = exhibition_preparer(pool=[pool_entry("magikarp", ["water"])])

    state = use_case.exhibition()

    assert isinstance(state, BattleState)
    assert state.player.current.capture_id == 7
    assert state.opponent.current.species == "magikarp"


def test_exhibition_reports_unknown_sides() -> None:
    use_case, _ = exhibition_preparer()
    lonely, _ = preparer(profiles=wild_profiles())

    assert use_case.exhibition("42", "magikarp") is BattleStatus.NO_CAPTURE
    assert use_case.exhibition("7", "agumon") is BattleStatus.NO_SPECIES_DATA
    assert use_case.exhibition("7", None) is BattleStatus.NO_TRAINER_POOL
    assert lonely.exhibition(None, "magikarp") is BattleStatus.NO_TEAM
    # «²» pasa str.isdigit pero no int(): es un nombre que no existe, no una traza.
    assert use_case.exhibition("²", "magikarp") is BattleStatus.NO_SPECIES_DATA


# --- Dobles de persistencia ----------------------------------------------------------


class Connection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, sql, *args):
        self.statements.append(sql)
        return self

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class Battles:
    def __init__(self, row=None) -> None:
        self.row = row
        self.saved: list[dict] = []
        self.cleared = 0

    def load_in_transaction(self, connection):
        return None if self.row is None else dict(self.row)

    def save_in_transaction(self, connection, row):
        self.row = dict(row)
        self.saved.append(dict(row))

    def clear_in_transaction(self, connection):
        self.row = None
        self.cleared += 1


class Encounters:
    def __init__(self, current=None) -> None:
        self.current = current
        self.cleared = 0

    def load_in_transaction(self, connection):
        return None if self.current is None else dict(self.current)

    def clear_in_transaction(self, connection):
        self.current = None
        self.cleared += 1


class Capture:
    def __init__(self, result=None) -> None:
        self.result = result
        self.commands: list = []

    def attempt_in_transaction(self, connection, command):
        self.commands.append(command)
        return self.result


class Progress:
    def __init__(self, stored=None) -> None:
        self.stored = dict(stored or {})
        self.applied: list[tuple[int, int, int]] = []

    def progress_in_transaction(self, connection, capture_id):
        return self.stored.get(capture_id)

    def apply_experience_in_transaction(self, connection, capture_id, experience, level):
        self.applied.append((capture_id, experience, level))
        self.stored[capture_id] = (experience, level)
        return True


class Records:
    def __init__(self, fail=False) -> None:
        self.recorded: list[tuple[str, str, str]] = []
        self.fail = fail

    def record_in_transaction(self, connection, *, kind, outcome, at):
        if self.fail:
            raise RuntimeError("disco lleno")
        self.recorded.append((kind, outcome, at))


class Script:
    """Azar guionizado: ``random()`` devuelve la lista y luego 0,99; ``randint``, el máximo."""

    def __init__(self, *randoms: float) -> None:
        self.randoms = list(randoms)

    def random(self) -> float:
        return self.randoms.pop(0) if self.randoms else 0.99

    def randint(self, low: int, high: int) -> int:
        return high


def fighter(key, species, base, *, level, moves, types, capture_id=None, **extra):
    return build_combatant(
        key=key,
        species=species,
        form="regular",
        name=extra.pop("name", species.title()),
        level=level,
        types=types,
        base_stats=Stats.from_mapping(base),
        ivs=Stats(0, 0, 0, 0, 0, 0),
        nature="hardy",
        moves=moves,
        capture_id=capture_id,
        experience=experience_for_level("medium", level),
        **extra,
    )


def chispa(**extra):
    return fighter(
        "p0",
        "charmander",
        CHARMANDER,
        level=15,
        moves=(EMBER, SCRATCH),
        types=("fire",),
        capture_id=7,
        name="Chispa",
        **extra,
    )


def shedinja(level=30):
    """Siempre 1 PS: cualquier golpe que le afecte lo debilita."""
    return fighter(
        "o0",
        "shedinja",
        SHEDINJA,
        level=level,
        moves=(SCRATCH,),
        types=("bug", "ghost"),
        base_experience=236,
        gender="genderless",
        ability="wonder-guard",
    )


def magikarp():
    """Tan débil que su Placaje nunca debilita a Chispa."""
    return fighter("o0", "magikarp", MAGIKARP, level=5, moves=(TACKLE,), types=("water",))


CONTEXT = CaptureContext(45, 40, False, False, "medium", -1, ("wonder-guard",), 30)


def wild_setup(*team, opponent=None):
    state = BattleState(
        kind=BattleKind.WILD,
        player=Side(tuple(team) or (chispa(),)),
        opponent=Side((opponent or shedinja(),)),
    )
    return BattleSetup(state=state, encounter_seen_at=SEEN_AT, capture=CONTEXT)


def trainer_setup(*team):
    state = BattleState(
        kind=BattleKind.TRAINER,
        player=Side(tuple(team) or (chispa(),)),
        opponent=Side((shedinja(level=12),), trainer="Montañero Anselmo"),
    )
    return BattleSetup(state=state)


class Arena:
    """Casos de uso del combate sobre dobles en memoria."""

    def __init__(self, *, current=None, capture=None, progress=None, records=None, rng=None):
        self.connection = Connection()
        self.battles = Battles()
        self.encounters = Encounters(encounter() if current is None else current)
        self.capture = Capture(capture)
        self.progress = Progress(progress)
        self.records = records or Records()
        factory = lambda: self.connection  # noqa: E731
        self.start = StartBattle(
            connection_factory=factory, battles=self.battles, encounters=self.encounters
        )
        self.turn = TakeBattleTurn(
            connection_factory=factory,
            battles=self.battles,
            encounters=self.encounters,
            capture=self.capture,
            progress=self.progress,
            records=self.records,
            rng=rng or random.Random(3),
        )
        self.describe = DescribeBattle(
            connection_factory=factory, battles=self.battles, encounters=self.encounters
        )

    def begin(self, setup):
        outcome = self.start.execute(setup, now=NOW)
        assert outcome.status is BattleStatus.STARTED
        return outcome

    def play(self, command):
        return self.turn.execute(command, now=NOW)

    def store(self, state, context=CONTEXT):
        """Guarda un estado tal cual, con el envoltorio documentado."""
        envelope = {
            "v": 1,
            "state": codec.state_to_dict(state),
            "context": asdict(context) | {"abilities": list(context.abilities)},
        }
        self.battles.row = {
            "kind": state.kind.value,
            "state_json": json.dumps(envelope),
            "encounter_seen_at": SEEN_AT,
            "started_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        }


def slot_of(state, slug):
    return next(i for i, slot in enumerate(state.player.current.moves) if slot.move.slug == slug)


# --- Empezar ---------------------------------------------------------------------------


def test_start_saves_the_opening_inside_one_transaction() -> None:
    arena = Arena()

    outcome = arena.begin(wild_setup())

    assert [type(event) for event in outcome.events] == [BattleStarted, SentOut, SentOut]
    assert arena.connection.statements[0] == "BEGIN IMMEDIATE"
    assert (arena.connection.commits, arena.connection.closed) == (1, True)
    row = arena.battles.row
    assert (row["kind"], row["encounter_seen_at"]) == ("wild", SEEN_AT)
    assert row["started_at"] == row["updated_at"] == NOW.isoformat()
    envelope = json.loads(row["state_json"])
    assert envelope["v"] == 1
    assert codec.state_from_dict(envelope["state"]) == outcome.state
    assert envelope["context"]["abilities"] == ["wonder-guard"]


def test_start_refuses_a_second_battle_and_revalidates_the_encounter() -> None:
    arena = Arena()
    arena.begin(wild_setup())

    assert arena.start.execute(trainer_setup(), now=NOW).status is BattleStatus.BATTLE_IN_PROGRESS
    assert (
        Arena(current=encounter(seen_at="otro")).start.execute(wild_setup(), now=NOW).status
        is BattleStatus.STALE
    )
    assert (
        Arena(current=encounter(captured=True)).start.execute(wild_setup(), now=NOW).status
        is BattleStatus.ALREADY_CAPTURED
    )
    empty = Arena()
    empty.encounters.current = None
    assert empty.start.execute(wild_setup(), now=NOW).status is BattleStatus.NO_ENCOUNTER


def test_start_discards_a_stale_battle_before_opening_another() -> None:
    arena = Arena()
    arena.begin(wild_setup())
    arena.encounters.current = encounter(seen_at="2026-11-11T09:00:00+00:00")

    outcome = arena.start.execute(trainer_setup(), now=NOW)

    assert outcome.status is BattleStatus.STARTED
    assert arena.battles.row["kind"] == "trainer"


def test_exhibitions_are_never_saved() -> None:
    state = BattleState(BattleKind.EXHIBITION, Side((chispa(),)), Side((shedinja(),)))

    with pytest.raises(ValueError):
        Arena().start.execute(BattleSetup(state), now=NOW)


def test_the_time_may_also_arrive_already_in_iso() -> None:
    arena = Arena()

    arena.start.execute(trainer_setup(), now="2026-11-11T13:00:00+00:00")

    assert arena.battles.row["started_at"] == "2026-11-11T13:00:00+00:00"


# --- Turnos: carga y validación ----------------------------------------------------


def test_a_turn_without_a_valid_battle_reports_it_and_cleans_up() -> None:
    arena = Arena()
    assert arena.play(Fight(0)) == TurnOutcome(BattleStatus.NO_BATTLE, (), None)

    arena.begin(wild_setup())
    arena.battles.row["state_json"] = "{roto"
    assert arena.play(Fight(0)).status is BattleStatus.NO_BATTLE
    assert arena.battles.row is None

    arena.begin(wild_setup())
    arena.encounters.current = encounter(seen_at="2026-11-11T09:00:00+00:00")
    stale = arena.play(Fight(0))
    assert (stale.status, stale.state) == (BattleStatus.STALE, None)
    assert stale.reason
    assert arena.battles.row is None


@pytest.mark.parametrize(
    "context",
    [None, {"capture_rate": 45}, asdict(CONTEXT) | {"abilities": "blaze"}],
)
def test_a_wild_battle_without_a_valid_capture_context_is_dropped(context) -> None:
    arena = Arena()
    arena.encounters.current = encounter()
    arena.begin(wild_setup())
    envelope = json.loads(arena.battles.row["state_json"])
    arena.battles.row["state_json"] = json.dumps(envelope | {"context": context})

    assert arena.play(Fight(0)).status is BattleStatus.NO_BATTLE
    assert arena.battles.row is None


def test_a_trainer_battle_leaves_the_wild_encounter_alone() -> None:
    arena = Arena(progress={7: (3375, 15)})
    started = arena.begin(trainer_setup())

    outcome = arena.play(Fight(slot_of(started.state, "ember")))

    assert outcome.state.outcome is Outcome.WON
    assert arena.records.recorded == [("trainer", "won", NOW.isoformat())]
    assert arena.encounters.current == encounter()
    assert arena.encounters.cleared == 0


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        (Switch(0), "¡Chispa ya está combatiendo!"),
        (Switch(5), "Ese Pokémon no está en el equipo."),
        (Fight(9), "Ese movimiento no existe."),
        (Replace(0), "Ahora no hace falta sacar a otro Pokémon."),
    ],
)
def test_invalid_actions_neither_spend_the_turn_nor_write(command, reason) -> None:
    arena = Arena()
    started = arena.begin(wild_setup())

    outcome = arena.play(command)

    assert (outcome.status, outcome.reason) == (BattleStatus.INVALID_ACTION, reason)
    assert outcome.state == started.state
    assert outcome.events == ()
    assert len(arena.battles.saved) == 1  # solo el de la apertura


def test_a_ball_against_a_trainer_is_invalid_and_never_reaches_the_capture() -> None:
    arena = Arena()
    arena.begin(trainer_setup())

    outcome = arena.play(ThrowBall("ultraball", "Ultraball", 2.0))

    assert outcome.status is BattleStatus.INVALID_ACTION
    assert outcome.reason == "¡No puedes capturar el Pokémon de otro entrenador!"
    assert arena.capture.commands == []


def test_no_ball_and_no_switch_while_the_turn_is_locked_in() -> None:
    charging = replace(chispa(), volatile=Volatile(charging="solar-beam"))
    arena = Arena()
    arena.begin(
        wild_setup(
            charging,
            fighter("p1", "pidgey", MAGIKARP, level=10, moves=(TACKLE,), types=("normal",)),
        )
    )

    ball = arena.play(ThrowBall("pokeball", "Pokeball", 1.0))
    switch = arena.play(Switch(1))

    assert ball.status is switch.status is BattleStatus.INVALID_ACTION
    assert ball.reason == "¡Chispa no puede hacer otra cosa este turno!"
    assert arena.capture.commands == []


def test_fleeing_from_a_trainer_spends_no_turn() -> None:
    arena = Arena()
    started = arena.begin(trainer_setup())

    outcome = arena.play(Flee())

    assert outcome.status is BattleStatus.APPLIED
    assert outcome.events == (EscapeAttempted(success=False, forbidden=True),)
    assert outcome.state.turn == started.state.turn == 0


# --- Turnos: EXP --------------------------------------------------------------------


def test_a_ko_adds_only_this_turn_experience_to_what_the_capture_has_now() -> None:
    trained = experience_for_level("medium", 15) + 100  # los commits la entrenaron
    arena = Arena(progress={7: (trained, 15)})
    started = arena.begin(wild_setup())

    outcome = arena.play(Fight(slot_of(started.state, "ember")))

    assert outcome.status is BattleStatus.FINISHED
    assert outcome.state.outcome is Outcome.WON
    gained = next(event.amount for event in outcome.events if isinstance(event, ExperienceGained))
    assert gained == 236 * 30 // 7
    assert outcome.state.player.current.experience == experience_for_level("medium", 15) + gained
    total = trained + gained
    assert arena.progress.applied == [(7, total, level_for_experience("medium", total))]
    assert outcome.leveled == (7,)
    assert any(isinstance(event, LeveledUp) for event in outcome.events)
    assert arena.records.recorded == [("wild", "won", NOW.isoformat())]
    assert (arena.battles.row, arena.encounters.current) == (None, None)


def test_experience_is_capped_at_the_top_of_its_curve() -> None:
    top = experience_for_level("medium", MAX_LEVEL)
    arena = Arena(progress={7: (top - 10, 99)})
    started = arena.begin(wild_setup())

    outcome = arena.play(Fight(slot_of(started.state, "ember")))

    assert arena.progress.applied == [(7, top, MAX_LEVEL)]
    assert outcome.leveled == (7,)


def test_progress_of_a_capture_that_no_longer_exists_is_skipped() -> None:
    arena = Arena(progress={})
    started = arena.begin(wild_setup())

    outcome = arena.play(Fight(slot_of(started.state, "ember")))

    assert outcome.status is BattleStatus.FINISHED
    assert arena.progress.applied == []
    assert outcome.leveled == ()


def test_a_failure_rolls_the_whole_turn_back() -> None:
    arena = Arena(progress={7: (3375, 15)}, records=Records(fail=True))
    started = arena.begin(wild_setup())
    commits = arena.connection.commits

    with pytest.raises(RuntimeError):
        arena.play(Fight(slot_of(started.state, "ember")))

    assert arena.connection.commits == commits
    assert arena.connection.rollbacks == 1
    assert arena.connection.closed


# --- Turnos: Pokéballs --------------------------------------------------------------


def test_a_caught_ball_captures_the_fought_individual() -> None:
    arena = Arena(capture=CaptureResult(CaptureStatus.CAUGHT, chance=0.4, capture_id=42))
    started = arena.begin(wild_setup())

    outcome = arena.play(ThrowBall("ultraball", "Ultraball", 2.0))

    assert outcome.status is BattleStatus.FINISHED
    assert outcome.capture_id == 42
    assert outcome.events == (
        BallThrown("ultraball", "Ultraball", 3, caught=True),
        BattleEnded(Outcome.CAPTURED),
    )
    (command,) = arena.capture.commands
    wild = started.state.opponent.current
    assert (command.ball_slug, command.ball_multiplier, command.caught_at) == (
        "ultraball",
        2.0,
        NOW.isoformat(),
    )
    assert (command.capture_rate, command.speed, command.encounter_level) == (45, 40, 30)
    assert command.abilities == ("wonder-guard",) and command.gender_rate == -1
    assert command.battle_multiplier == battle_catch_multiplier(wild)
    individual = command.individual
    assert (individual.level, individual.nature, individual.gender, individual.ability) == (
        30,
        "hardy",
        "genderless",
        "wonder-guard",
    )
    assert individual.ivs == wild.ivs.as_mapping()
    assert individual.experience == experience_for_level("medium", 30)
    assert arena.records.recorded == [("wild", "captured", NOW.isoformat())]
    assert arena.battles.row is None
    assert arena.encounters.cleared == 0  # la captura lo marcó capturado; no se borra


def test_a_failed_ball_shakes_and_then_the_wild_acts() -> None:
    # Probabilidad 0,4096: cada sacudida sale con 0,8. Guion: dos sí y una no.
    arena = Arena(
        capture=CaptureResult(CaptureStatus.FAILED, chance=0.4096), rng=Script(0.5, 0.5, 0.95)
    )
    arena.begin(wild_setup(opponent=magikarp()))

    outcome = arena.play(ThrowBall("superball", "Superball", 1.5))

    assert outcome.status is BattleStatus.APPLIED
    assert outcome.events[:2] == (
        TurnStarted(1),
        BallThrown("superball", "Superball", 2, caught=False),
    )
    assert any(isinstance(e, MoveUsed) and e.side == "opponent" for e in outcome.events)
    assert outcome.state.turn == 1
    assert (
        codec.state_from_dict(json.loads(arena.battles.row["state_json"])["state"]) == outcome.state
    )
    assert arena.records.recorded == []


def test_running_out_of_patience_ends_the_battle_and_the_encounter() -> None:
    arena = Arena(capture=CaptureResult(CaptureStatus.FLED, chance=0.0))
    arena.begin(wild_setup())

    outcome = arena.play(ThrowBall("pokeball", "Pokeball", 1.0))

    assert outcome.status is BattleStatus.FINISHED
    assert outcome.events == (
        BallThrown("pokeball", "Pokeball", 0, caught=False),
        OpponentFled("Shedinja"),
        BattleEnded(Outcome.OPPONENT_FLED),
    )
    assert arena.records.recorded == [("wild", "opponent_fled", NOW.isoformat())]
    assert (arena.battles.row, arena.encounters.current) == (None, None)


def test_without_stock_no_turn_is_spent() -> None:
    arena = Arena(capture=CaptureResult(CaptureStatus.NO_STOCK))
    started = arena.begin(wild_setup())

    outcome = arena.play(ThrowBall("ultraball", "Ultraball", 2.0))

    assert (outcome.status, outcome.reason) == (
        BattleStatus.NO_STOCK,
        "No te queda ninguna Ultraball.",
    )
    assert outcome.state == started.state
    assert len(arena.battles.saved) == 1


def test_a_capture_that_finds_no_encounter_turns_the_battle_stale() -> None:
    arena = Arena(capture=CaptureResult(CaptureStatus.ALREADY_CAPTURED))
    arena.begin(wild_setup())

    outcome = arena.play(ThrowBall("pokeball", "Pokeball", 1.0))

    assert outcome.status is BattleStatus.STALE
    assert arena.battles.row is None


# --- Turnos: relevos -------------------------------------------------------------------


def replacement_state():
    fainted = replace(chispa(), hp=0)
    pidgey = fighter(
        "p1", "pidgey", MAGIKARP, level=10, moves=(TACKLE,), types=("normal",), capture_id=8
    )
    return BattleState(
        kind=BattleKind.WILD,
        player=Side((fainted, pidgey)),
        opponent=Side((shedinja(),)),
        phase=Phase.CHOOSE_REPLACEMENT,
        turn=3,
    )


def test_after_a_faint_only_a_replacement_is_accepted() -> None:
    arena = Arena()
    arena.store(replacement_state())

    fight = arena.play(Fight(0))
    ball = arena.play(ThrowBall("pokeball", "Pokeball", 1.0))
    fainted = arena.play(Replace(0))
    sent = arena.play(Replace(1))

    assert fight.reason == ball.reason == "Antes tienes que sacar a otro Pokémon."
    assert fainted.reason == "¡Chispa no tiene fuerzas para luchar!"
    assert sent.status is BattleStatus.APPLIED
    assert [type(event) for event in sent.events] == [SentOut]
    assert sent.state.player.active == 1
    assert sent.state.phase is Phase.CHOOSE_ACTION


def test_auto_also_chooses_the_replacement() -> None:
    arena = Arena()
    arena.store(replacement_state())

    outcome = arena.play(Auto())

    assert outcome.status is BattleStatus.APPLIED
    assert outcome.state.player.active == 1


# --- Describir --------------------------------------------------------------------------


def test_describe_returns_the_live_battle_and_drops_obsolete_ones() -> None:
    arena = Arena()
    started = arena.begin(trainer_setup())

    view = arena.describe.execute()
    assert view == BattleView(
        BattleKind.TRAINER, started.state, "Montañero Anselmo", started_at=view.started_at
    )
    # La vista lleva la identidad del combate guardado para atar la sesión a él.
    assert view.started_at

    stale = Arena()
    stale.begin(wild_setup())
    stale.encounters.current = encounter(captured=True)
    assert stale.describe.execute() is None
    assert stale.battles.row is None


def test_active_wild_name_only_for_a_live_wild_battle_and_never_raises() -> None:
    live = Arena()
    live.begin(wild_setup())
    trainer = Arena()
    trainer.begin(trainer_setup())
    stale = Arena()
    stale.begin(wild_setup())
    stale.encounters.current = encounter(seen_at="2026-11-11T09:00:00+00:00")
    commits = stale.connection.commits

    def broken():
        raise OSError("sin disco")

    assert live.describe.active_wild_name() == "Shedinja"
    assert trainer.describe.active_wild_name() is None
    assert Arena().describe.active_wild_name() is None
    assert stale.describe.active_wild_name() is None
    # Solo consulta: el combate obsoleto lo limpia quien lo vaya a jugar.
    assert stale.battles.row is not None and stale.connection.commits == commits
    assert (
        DescribeBattle(
            connection_factory=broken, battles=Battles(), encounters=Encounters()
        ).active_wild_name()
        is None
    )


# --- Sesiones -----------------------------------------------------------------------------


def test_exhibition_session_plays_in_memory_until_the_end() -> None:
    state = BattleState(
        BattleKind.EXHIBITION,
        Side((chispa(),)),
        Side((fighter("o0", "magikarp", MAGIKARP, level=5, moves=(TACKLE,), types=("water",)),)),
        award_experience=False,
    )
    session = ExhibitionSession(state, rng=random.Random(11))

    assert [type(event) for event in session.opening] == [BattleStarted, SentOut, SentOut]
    assert session.kind is BattleKind.EXHIBITION and session.trainer is None
    ball = session.take(ThrowBall("pokeball", "Pokeball", 1.0))
    assert ball.status is BattleStatus.INVALID_ACTION
    outcomes = [session.take(Auto()) for _ in range(40) if not session.state.finished]
    assert outcomes[-1].status is BattleStatus.FINISHED
    assert all(not isinstance(e, ExperienceGained) for o in outcomes for e in o.events)
    assert session.take(Auto()).status is BattleStatus.NO_BATTLE


def test_persisted_session_plays_each_command_with_the_clock_time() -> None:
    state = wild_setup().state
    later = replace(state, turn=1)
    calls = []

    class Turns:
        def __init__(self, outcomes):
            self.outcomes = list(outcomes)

        def execute(self, command, *, now):
            calls.append((command, now))
            return self.outcomes.pop(0)

    session = PersistedSession(
        turns=Turns(
            [
                TurnOutcome(BattleStatus.APPLIED, (), later),
                TurnOutcome(BattleStatus.NO_BATTLE, (), None),
            ]
        ),
        view=BattleView(BattleKind.WILD, state, None),
        clock=lambda: NOW,
        opening=(BattleStarted(BattleKind.WILD, None),),
    )

    session.take(Fight(1))
    gone = session.take(Flee())

    assert calls == [(Fight(1), NOW), (Flee(), NOW)]
    assert session.state == later  # sin estado nuevo, se queda el último
    assert gone.status is BattleStatus.NO_BATTLE
    assert session.opening == (BattleStarted(BattleKind.WILD, None),)
    assert (session.kind, session.trainer) == (BattleKind.WILD, None)
