"""Learnset por nivel a partir del ``/pokemon/{nombre}`` de PokeAPI.

Las fixtures son recortes de payloads reales (Noctowl, Electrode de Hisui):
solo los campos que el parser lee, en el mismo formato.
"""

import pytest

from pokedex_cli.infrastructure.pokeapi_parsing import (
    LEARNSET_VERSION_PREFERENCE,
    _level_up_learnset,
)


def learned(group: str, level: int = 0, *, method: str = "level-up", order=None) -> dict:
    return {
        "level_learned_at": level,
        "move_learn_method": {
            "name": method,
            "url": "https://pokeapi.co/api/v2/move-learn-method/1/",
        },
        "order": order,
        "version_group": {"name": group, "url": "https://pokeapi.co/api/v2/version-group/6/"},
    }


def move(slug: str, *details: dict) -> dict:
    return {
        "move": {"name": slug, "url": f"https://pokeapi.co/api/v2/move/{slug}/"},
        "version_group_details": list(details),
    }


def pokemon(*moves: dict) -> dict:
    return {"name": "noctowl", "moves": list(moves)}


def level_moves(group: str, count: int, *, prefix: str = "move") -> list[dict]:
    return [move(f"{prefix}-{index}", learned(group, index + 1)) for index in range(count)]


def test_prefers_classic_emerald_and_sorts_by_level_then_in_game_order() -> None:
    # Noctowl: Esmeralda tiene menos movimientos que Escarlata/Púrpura, pero
    # manda la preferencia clásica. Los de nivel 1 siguen el ``order`` de
    # PokeAPI aunque la lista venga desordenada, y los repetidos se conservan
    # (el dominio decide con la última aparición).
    payload = pokemon(
        move("wing-attack", learned("emerald", method="egg")),
        move("take-down", learned("emerald", 33), learned("scarlet-violet", 28)),
        move("peck", learned("emerald", 1, order=4), learned("emerald", 11)),
        move("tackle", learned("emerald", 1, order=1), learned("scarlet-violet", 1, order=1)),
        move("hypnosis", learned("emerald", 16), learned("scarlet-violet", 48)),
        move("foresight", learned("emerald", 6), learned("emerald", 1, order=3)),
        move("growl", learned("emerald", 1, order=2)),
        move("psychic", learned("emerald", method="machine")),
        move("dream-eater", learned("emerald", 57)),
        *level_moves("scarlet-violet", 12, prefix="modern"),
    )

    assert _level_up_learnset(payload) == (
        "emerald",
        [
            ["tackle", 1],
            ["growl", 1],
            ["foresight", 1],
            ["peck", 1],
            ["foresight", 6],
            ["peck", 11],
            ["hypnosis", 16],
            ["take-down", 33],
            ["dream-eater", 57],
        ],
    )


def test_evolution_moves_count_as_level_one_after_the_base_moves() -> None:
    # Electrode de Hisui en Escarlata/Púrpura: Clorofiláser se aprende al
    # evolucionar (nivel 0) y va detrás de los de nivel 1, que sí tienen
    # ``order``. Escarlata/Púrpura precede a Leyendas: Arceus en la preferencia.
    payload = pokemon(
        move("tackle", learned("legends-arceus", 5), learned("scarlet-violet", 1, order=2)),
        move("stun-spore", learned("scarlet-violet", 6)),
        move("thunder-shock", learned("legends-arceus", 1), learned("scarlet-violet", 4)),
        move("charge", learned("scarlet-violet", 1, order=1)),
        move("chloroblast", learned("scarlet-violet", 0), learned("legends-arceus", 47)),
        move("spark", learned("legends-arceus", 15)),
        move("thunder", learned("legends-arceus", 37)),
    )

    assert _level_up_learnset(payload) == (
        "scarlet-violet",
        [
            ["charge", 1],
            ["tackle", 1],
            ["chloroblast", 1],
            ["thunder-shock", 4],
            ["stun-spore", 6],
        ],
    )


def test_a_mega_form_without_classic_learnsets_falls_into_a_modern_group() -> None:
    # Charizard Mega Y: PokeAPI solo le da learnset desde X/Y y Rubí Omega/Zafiro
    # Alfa es el primero de esos grupos en la preferencia.
    modern = ("x-y", "omega-ruby-alpha-sapphire", "sun-moon", "ultra-sun-ultra-moon")
    payload = pokemon(
        *(
            move(slug, *(learned(group, level) for group in modern))
            for slug, level in (("scratch", 1), ("ember", 7), ("smokescreen", 10), ("slash", 41))
        )
    )

    assert _level_up_learnset(payload) == (
        "omega-ruby-alpha-sapphire",
        [["scratch", 1], ["ember", 7], ["smokescreen", 10], ["slash", 41]],
    )


@pytest.mark.parametrize(
    ("emerald_moves", "expected_group"),
    [(3, "firered-leafgreen"), (4, "emerald")],
)
def test_a_group_needs_at_least_four_level_up_moves(
    emerald_moves: int, expected_group: str
) -> None:
    payload = pokemon(
        *level_moves("emerald", emerald_moves),
        *level_moves("firered-leafgreen", 4, prefix="kanto"),
    )

    group, entries = _level_up_learnset(payload)

    assert group == expected_group
    assert len(entries) == 4


def test_a_repeated_move_does_not_reach_the_minimum_by_itself() -> None:
    payload = pokemon(
        move("tackle", learned("emerald", 1, order=1)),
        move("growl", learned("emerald", 1, order=2)),
        move("foresight", learned("emerald", 1, order=3), learned("emerald", 6)),
        *level_moves("firered-leafgreen", 4, prefix="kanto"),
    )

    assert _level_up_learnset(payload)[0] == "firered-leafgreen"


def test_without_a_qualifying_preferred_group_the_largest_group_wins() -> None:
    # Colosseum no está en la preferencia, pero es el que más movimientos tiene.
    payload = pokemon(
        *level_moves("emerald", 2),
        *level_moves("colosseum", 3, prefix="orre"),
    )

    group, entries = _level_up_learnset(payload)

    assert group == "colosseum"
    assert [slug for slug, _ in entries] == ["orre-0", "orre-1", "orre-2"]


def test_ties_between_small_groups_keep_the_classic_preference() -> None:
    # Ditto: un único movimiento en todos los juegos.
    payload = pokemon(
        move(
            "transform",
            learned("xd", 1),
            learned("scarlet-violet", 1),
            learned("red-blue", 1),
            learned("emerald", 1),
        )
    )

    assert _level_up_learnset(payload) == ("emerald", [["transform", 1]])


def test_unknown_groups_tie_alphabetically_for_a_stable_choice() -> None:
    payload = pokemon(move("splash", learned("zz-future", 1), learned("aa-future", 1)))

    assert _level_up_learnset(payload) == ("aa-future", [["splash", 1]])


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"moves": None},
        {"moves": []},
        pokemon(move("psychic", learned("emerald", method="machine"))),
    ],
)
def test_without_level_up_moves_there_is_no_learnset(payload: dict) -> None:
    assert _level_up_learnset(payload) == (None, [])


def test_malformed_entries_are_skipped_instead_of_breaking_the_species() -> None:
    payload = pokemon(
        "not-a-move",
        {"move": None, "version_group_details": [learned("emerald", 1)]},
        {"move": {"name": "ghost-entry"}, "version_group_details": None},
        move(
            "bad-levels",
            learned("emerald", None),
            learned("emerald", "7"),
            learned("emerald", -1),
            learned("emerald", True),
            {"level_learned_at": 3, "move_learn_method": {"name": "level-up"}},
            "detail",
        ),
        *level_moves("emerald", 4),
    )

    assert _level_up_learnset(payload) == (
        "emerald",
        [["move-0", 1], ["move-1", 2], ["move-2", 3], ["move-3", 4]],
    )


def test_preference_is_classic_first() -> None:
    assert LEARNSET_VERSION_PREFERENCE == (
        "emerald",
        "firered-leafgreen",
        "ruby-sapphire",
        "platinum",
        "heartgold-soulsilver",
        "diamond-pearl",
        "black-2-white-2",
        "black-white",
        "omega-ruby-alpha-sapphire",
        "x-y",
        "ultra-sun-ultra-moon",
        "sun-moon",
        "sword-shield",
        "scarlet-violet",
        "brilliant-diamond-shining-pearl",
        "legends-arceus",
        "crystal",
        "gold-silver",
        "yellow",
        "red-blue",
        "lets-go-pikachu-lets-go-eevee",
    )
