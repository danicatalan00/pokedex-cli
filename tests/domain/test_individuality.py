from hypothesis import given
from hypothesis import strategies as st

from pokedex_cli.domain.individuality import (
    NATURES,
    STAT_KEYS,
    apply_nature,
    compute_stats,
    derive_ability,
    derive_gender,
    derive_ivs_nature,
    gender_from_roll,
    hp_stat,
    nature_by_name,
    other_stat,
    roll_ability,
    roll_ivs,
    roll_nature,
)


class FixedRandom:
    def __init__(self, *, ints: list[int] | None = None, floats: list[float] | None = None) -> None:
        self._ints = list(ints or [])
        self._floats = list(floats or [])

    def randint(self, low: int, high: int) -> int:
        value = self._ints.pop(0)
        assert low <= value <= high
        return value

    def random(self) -> float:
        return self._floats.pop(0)


def test_natures_are_25_unique_with_neutrals_at_the_canonical_indices() -> None:
    assert len(NATURES) == 25
    assert len({nature.name for nature in NATURES}) == 25
    neutral_indices = {nature.index for nature in NATURES if nature.plus is None}
    assert neutral_indices == {0, 6, 12, 18, 24}
    for nature in NATURES:
        if nature.index in neutral_indices:
            assert nature.plus is None and nature.minus is None
        else:
            assert nature.plus is not None and nature.minus is not None
            assert nature.plus != "hp"
            assert nature.minus != "hp"


def test_adamant_and_modest_spot_checks() -> None:
    adamant = NATURES[3]
    assert (adamant.name, adamant.plus, adamant.minus) == ("adamant", "atk", "spa")
    modest = NATURES[15]
    assert (modest.name, modest.plus, modest.minus) == ("modest", "spa", "atk")


def test_nature_by_name_is_case_insensitive_and_unknown_is_none() -> None:
    assert nature_by_name("Adamant") is NATURES[3]
    assert nature_by_name("ADAMANT") is NATURES[3]
    assert nature_by_name(None) is None
    assert nature_by_name("not-a-nature") is None


def test_hp_stat_matches_the_official_formula_by_hand() -> None:
    # (2*45 + 31 + 0) * 5 // 100 + 5 + 10 = 121*5//100 + 15 = 6 + 15 = 21
    assert hp_stat(45, 31, 5) == 21
    # (2*45 + 31) * 100 // 100 + 100 + 10 = 121 + 110 = 231
    assert hp_stat(45, 31, 100) == 231


def test_shedinja_always_has_one_hp() -> None:
    for iv in (0, 15, 31):
        for level in (1, 50, 100):
            assert hp_stat(1, iv, level) == 1


def test_other_stat_applies_nature_multiplier_with_floor_division() -> None:
    adamant = NATURES[3]  # +atk -spa
    # raw = (2*40 + 10 + 0) * 50 // 100 + 5 = 90*50//100 + 5 = 45 + 5 = 50
    assert other_stat(40, 10, 50, NATURES[0], "def") == 50
    # boosted stat: floor(50 * 1.1) = 55
    assert other_stat(40, 10, 50, adamant, "atk") == 55
    # lowered stat: floor(50 * 0.9) = 45
    assert other_stat(40, 10, 50, adamant, "spa") == 45


def test_apply_nature_floors_exactly() -> None:
    nature = NATURES[3]  # +atk -spa
    assert apply_nature(57, nature, "atk") == 62  # floor(57*1.1) = floor(62.7) = 62
    assert apply_nature(57, nature, "spa") == 51  # floor(57*0.9) = floor(51.3) = 51
    assert apply_nature(57, nature, "def") == 57


def test_compute_stats_returns_none_for_missing_bases() -> None:
    bases = {"hp": 45, "atk": None, "def": 49, "spa": 65, "spd": 65, "spe": 45}
    ivs = {key: 31 for key in STAT_KEYS}
    stats = compute_stats(bases, ivs, 50, NATURES[0])
    assert stats["atk"] is None
    assert stats["hp"] is not None


def test_gender_from_roll_semantics() -> None:
    assert gender_from_roll(None, 0.0) is None
    assert gender_from_roll(-1, 0.9) == "genderless"
    assert gender_from_roll(0, 0.999) == "male"
    assert gender_from_roll(8, 0.0) == "female"
    # threshold is exact: rate=1 -> female below 1/8, male at or above 1/8.
    assert gender_from_roll(1, 0.124) == "female"
    assert gender_from_roll(1, 0.125) == "male"


def test_roll_ivs_consumes_randint_in_stat_key_order() -> None:
    rng = FixedRandom(ints=[1, 2, 3, 4, 5, 6])
    assert roll_ivs(rng) == {"hp": 1, "atk": 2, "def": 3, "spa": 4, "spd": 5, "spe": 6}


def test_roll_nature_indexes_into_natures() -> None:
    rng = FixedRandom(ints=[3])
    assert roll_nature(rng) is NATURES[3]


def test_roll_ability_handles_zero_one_and_two_choices() -> None:
    assert roll_ability((), FixedRandom()) is None
    assert roll_ability(("static",), FixedRandom()) == "static"
    rng = FixedRandom(ints=[1])
    assert roll_ability(("static", "lightning-rod"), rng) == "lightning-rod"


def test_derivation_is_deterministic_and_varies_with_seed() -> None:
    ivs_a, nature_a = derive_ivs_nature("7:2026-07-15T10:00:00+00:00")
    ivs_a2, nature_a2 = derive_ivs_nature("7:2026-07-15T10:00:00+00:00")
    assert ivs_a == ivs_a2
    assert nature_a is nature_a2
    for value in ivs_a.values():
        assert 0 <= value <= 31

    ivs_b, nature_b = derive_ivs_nature("8:2026-07-15T10:00:00+00:00")
    assert ivs_a != ivs_b or nature_a is not nature_b


def test_derive_gender_and_ability_examples() -> None:
    assert derive_gender("1:now", None) is None
    assert derive_gender("1:now", -1) == "genderless"
    assert derive_gender("1:now", 8) == "female"
    assert derive_gender("1:now", 0) == "male"

    assert derive_ability("1:now", ()) is None
    assert derive_ability("1:now", ("static",)) == "static"
    result = derive_ability("1:now", ("static", "lightning-rod"))
    assert result in ("static", "lightning-rod")
    assert derive_ability("1:now", ("static", "lightning-rod")) == result


@given(
    base=st.integers(min_value=1, max_value=255),
    iv=st.integers(min_value=0, max_value=31),
    level=st.integers(min_value=1, max_value=100),
    nature_index=st.integers(min_value=0, max_value=24),
)
def test_stats_are_positive_and_monotonic_with_level(
    base: int, iv: int, level: int, nature_index: int
) -> None:
    nature = NATURES[nature_index]
    bases = {key: base for key in STAT_KEYS}
    ivs = {key: iv for key in STAT_KEYS}
    stats_now = compute_stats(bases, ivs, level, nature)
    for value in stats_now.values():
        assert value is not None and value > 0
    if level < 100:
        stats_next = compute_stats(bases, ivs, level + 1, nature)
        for key in STAT_KEYS:
            assert stats_next[key] is not None and stats_now[key] is not None
            assert stats_next[key] >= stats_now[key]
