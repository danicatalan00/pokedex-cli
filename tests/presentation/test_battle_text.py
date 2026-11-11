"""Contrato de los textos del combate: cada evento se cuenta en buen español."""

from __future__ import annotations

import typing

import pytest
from rich.color import Color

from pokedex_cli.domain.battle import model as m
from pokedex_cli.presentation.battle_text import (
    STAT_NAMES_ES,
    STATUS_BADGES,
    display_label,
    effectiveness_hint,
    messages_for,
    move_summary,
    stat_with_article,
)

WILD = m.BattleKind.WILD
TRAINER = m.BattleKind.TRAINER
EXHIBITION = m.BattleKind.EXHIBITION
TRAINER_NAME = "Montañero Julián"


def sample_events() -> dict[type, m.BattleEvent]:
    """Un ejemplar narrable de cada tipo de evento del modelo."""
    return {
        m.BattleStarted: m.BattleStarted(TRAINER, TRAINER_NAME),
        m.TurnStarted: m.TurnStarted(3),
        m.SentOut: m.SentOut("opponent", "o0", "Onix", "onix", "regular", False, 14),
        m.Recalled: m.Recalled("player", "p0", "Noctowl"),
        m.MoveUsed: m.MoveUsed(
            "player", "p0", "Noctowl", "hypnosis", "Hipnosis", "psychic", m.DamageClass.STATUS
        ),
        m.NoMovesLeft: m.NoMovesLeft("player", "p0", "Noctowl"),
        m.MoveMissed: m.MoveMissed("opponent", "o0", "Onix"),
        m.MoveFailed: m.MoveFailed("player", "p0", "Noctowl", m.FailReason.GENERIC),
        m.NoEffect: m.NoEffect("opponent", "o0", "Onix"),
        m.ProtectionRaised: m.ProtectionRaised("player", "p0", "Noctowl"),
        m.Protected: m.Protected("player", "p0", "Noctowl"),
        m.Damaged: m.Damaged(
            "opponent", "o0", "Onix", 30, 80, 50, 80, effectiveness=2.0, critical=True
        ),
        m.HitCount: m.HitCount("opponent", "o0", "Onix", 3),
        m.OneHitKO: m.OneHitKO("opponent", "o0", "Onix"),
        m.Healed: m.Healed("player", "p0", "Noctowl", 20, 30, 50, 70),
        m.StatusInflicted: m.StatusInflicted("opponent", "o0", "Onix", m.Status.PARALYSIS),
        m.StatusCured: m.StatusCured("opponent", "o0", "Onix", m.Status.SLEEP),
        m.StatusBlocked: m.StatusBlocked("opponent", "o0", "Onix", m.Status.BURN, already=True),
        m.Immobilized: m.Immobilized("opponent", "o0", "Onix", m.ImmobileReason.ASLEEP),
        m.Confused: m.Confused("opponent", "o0", "Onix"),
        m.ConfusionCheck: m.ConfusionCheck("opponent", "o0", "Onix"),
        m.ConfusionHurt: m.ConfusionHurt("opponent", "o0", "Onix"),
        m.ConfusionEnded: m.ConfusionEnded("opponent", "o0", "Onix"),
        m.StatChanged: m.StatChanged("opponent", "o0", "Onix", "atk", -1),
        m.StatUnchanged: m.StatUnchanged("opponent", "o0", "Onix", "def", rising=True),
        m.StatsReset: m.StatsReset(),
        m.PumpedUp: m.PumpedUp("player", "p0", "Noctowl"),
        m.Charging: m.Charging("player", "p0", "Noctowl", "sky-attack", "Ataque Aéreo"),
        m.ScreenRaised: m.ScreenRaised("player", "reflect"),
        m.ScreenFaded: m.ScreenFaded("opponent", "light-screen"),
        m.Seeded: m.Seeded("opponent", "o0", "Onix"),
        m.Trapped: m.Trapped("opponent", "o0", "Onix", "Constricción"),
        m.Released: m.Released("opponent", "o0", "Onix", "Constricción"),
        m.Teleported: m.Teleported("player", "p0", "Noctowl"),
        m.ForcedOut: m.ForcedOut("opponent", "o0", "Onix"),
        m.Fainted: m.Fainted("opponent", "o0", "Onix"),
        m.ExperienceGained: m.ExperienceGained("p0", "Noctowl", 120),
        m.LeveledUp: m.LeveledUp("p0", "Noctowl", 37, (("hp", 3), ("atk", 2))),
        m.MoveLearned: m.MoveLearned("p0", "Noctowl", "Psicorrayo", forgot="Placaje"),
        m.EscapeAttempted: m.EscapeAttempted(success=False, forbidden=True),
        m.BallThrown: m.BallThrown("superball", "Superball", 2, caught=False),
        m.OpponentFled: m.OpponentFled("Onix"),
        m.ReplacementNeeded: m.ReplacementNeeded("player"),
        m.BattleEnded: m.BattleEnded(m.Outcome.WON),
    }


def say(event: m.BattleEvent, kind: m.BattleKind = WILD, **context: object) -> list[str]:
    trainer = context.pop("trainer", TRAINER_NAME if kind is TRAINER else None)
    return messages_for(event, kind=kind, trainer=trainer, **context)  # type: ignore[arg-type]


def test_every_event_of_the_model_has_a_narrated_sample():
    samples = sample_events()
    assert set(samples) == set(typing.get_args(m.BattleEvent))
    for event_type, event in samples.items():
        lines = say(event, TRAINER, opponent="Onix")
        assert lines, event_type.__name__
        assert all(line.strip() == line and line for line in lines), event_type.__name__


@pytest.mark.parametrize("kind", list(m.BattleKind))
@pytest.mark.parametrize("trainer", [None, TRAINER_NAME])
def test_no_event_ever_raises_and_punctuation_is_balanced(kind, trainer):
    for event in sample_events().values():
        for line in messages_for(event, kind=kind, trainer=trainer):
            assert line.count("¡") == line.count("!"), line
            assert line.count("¿") == line.count("?"), line
            assert "None" not in line, line


PIDGEY = ("opponent", "o0", "Pidgey")
CHARIZARD = ("player", "p0", "Charizard")


@pytest.mark.parametrize(
    ("event", "kind", "expected"),
    [
        (
            m.SentOut("opponent", "o0", "Pidgey", "pidgey", "", False, 3),
            WILD,
            "¡Un Pidgey salvaje apareció!",
        ),
        (m.BattleStarted(TRAINER, TRAINER_NAME), TRAINER, "¡Montañero Julián quiere luchar!"),
        (
            m.SentOut("opponent", "o0", "Onix", "onix", "", False, 14),
            TRAINER,
            "¡Montañero Julián sacó a Onix!",
        ),
        (
            m.SentOut("player", "p0", "Noctowl", "noctowl", "", False, 20),
            WILD,
            "¡Adelante, Noctowl!",
        ),
        (m.Recalled("player", "p0", "Noctowl"), WILD, "¡Noctowl, vuelve!"),
        (
            m.MoveUsed(
                "player", "p0", "Noctowl", "hypnosis", "Hipnosis", "psychic", m.DamageClass.STATUS
            ),
            WILD,
            "¡Noctowl usó Hipnosis!",
        ),
        (m.NoEffect("opponent", "o0", "Gastly"), WILD, "No afecta a Gastly salvaje…"),
        (m.MoveMissed(*PIDGEY), TRAINER, "¡El ataque de Pidgey enemigo falló!"),
        (m.MoveFailed(*CHARIZARD, m.FailReason.GENERIC), WILD, "¡Pero falló!"),
        (m.MoveFailed(*CHARIZARD, m.FailReason.NOTHING_HAPPENED), WILD, "¡Pero no pasó nada!"),
        (m.HitCount(*PIDGEY, 3), WILD, "¡Alcanzó 3 veces!"),
        (m.HitCount(*PIDGEY, 1), WILD, "¡Alcanzó 1 vez!"),
        (m.StatusInflicted(*PIDGEY, m.Status.BURN), WILD, "¡Pidgey salvaje fue quemado!"),
        (
            m.StatusInflicted(*PIDGEY, m.Status.PARALYSIS),
            WILD,
            "¡Pidgey salvaje está paralizado! ¡Quizás no pueda moverse!",
        ),
        (m.StatusInflicted(*PIDGEY, m.Status.POISON), WILD, "¡Pidgey salvaje fue envenenado!"),
        (
            m.StatusInflicted(*PIDGEY, m.Status.TOXIC),
            WILD,
            "¡Pidgey salvaje fue gravemente envenenado!",
        ),
        (m.StatusInflicted(*PIDGEY, m.Status.FREEZE), WILD, "¡Pidgey salvaje se congeló!"),
        (
            m.Immobilized(*PIDGEY, m.ImmobileReason.ASLEEP),
            WILD,
            "Pidgey salvaje está dormido como un tronco.",
        ),
        (m.Immobilized(*PIDGEY, m.ImmobileReason.FROZEN), WILD, "Pidgey salvaje está congelado."),
        (m.Immobilized(*PIDGEY, m.ImmobileReason.FLINCHED), WILD, "¡Pidgey salvaje retrocedió!"),
        (
            m.Immobilized(*CHARIZARD, m.ImmobileReason.RECHARGING),
            WILD,
            "Charizard necesita recuperarse.",
        ),
        (m.StatusCured(*PIDGEY, m.Status.SLEEP), WILD, "¡Pidgey salvaje se despertó!"),
        (m.StatusCured(*PIDGEY, m.Status.FREEZE), WILD, "¡Pidgey salvaje se descongeló!"),
        (
            m.Damaged(*PIDGEY, 5, 20, 15, 20, cause=m.DamageCause.BURN),
            WILD,
            "¡Pidgey salvaje se hizo daño por la quemadura!",
        ),
        (m.Confused(*PIDGEY), WILD, "¡Pidgey salvaje se ha confundido!"),
        (m.ConfusionCheck(*PIDGEY), WILD, "¡Pidgey salvaje está confuso!"),
        (m.ConfusionHurt(*PIDGEY), WILD, "¡Está tan confuso que se hirió a sí mismo!"),
        (m.ConfusionEnded(*PIDGEY), WILD, "¡Pidgey salvaje ya no está confuso!"),
        (m.ProtectionRaised(*CHARIZARD), WILD, "¡Charizard se está protegiendo!"),
        (m.Protected(*CHARIZARD), WILD, "¡Charizard se ha protegido!"),
        (
            m.Trapped(*PIDGEY, "Constricción"),
            WILD,
            "¡Pidgey salvaje ha sido atrapado por Constricción!",
        ),
        (m.Released(*PIDGEY, "Constricción"), WILD, "¡Pidgey salvaje se liberó de Constricción!"),
        (
            m.Charging(*CHARIZARD, "solar-beam", "Rayo Solar"),
            WILD,
            "¡Charizard absorbió luz solar!",
        ),
        (m.Charging(*CHARIZARD, "fly", "Vuelo"), WILD, "¡Charizard voló muy alto!"),
        (m.Charging(*CHARIZARD, "dig", "Excavar"), WILD, "¡Charizard se ocultó bajo tierra!"),
        (
            m.Charging(*CHARIZARD, "freeze-shock", "Rayo Gélido"),
            WILD,
            "¡Charizard se está preparando!",
        ),
        (m.Fainted(*PIDGEY), WILD, "¡Pidgey salvaje se debilitó!"),
        (m.StatsReset(), WILD, "¡Se anularon los cambios de características!"),
        (m.EscapeAttempted(success=True), WILD, "¡Escapaste sin problemas!"),
        (m.EscapeAttempted(success=False), WILD, "¡No puedes escapar!"),
        (
            m.EscapeAttempted(success=False, forbidden=True),
            TRAINER,
            "¡No puedes huir de un combate contra un entrenador!",
        ),
        (m.OpponentFled("Pidgey"), WILD, "¡Pidgey salvaje ha huido!"),
        (m.BattleEnded(m.Outcome.LOST), WILD, "¡No te quedan Pokémon en condiciones de luchar!"),
        (m.BattleEnded(m.Outcome.WON), TRAINER, "¡Has derrotado a Montañero Julián!"),
        (
            m.BattleEnded(m.Outcome.DRAW),
            WILD,
            "¡Las dos partes están agotadas! El combate acaba en tablas.",
        ),
    ],
)
def test_classic_phrases(event, kind, expected):
    assert expected in say(event, kind)


@pytest.mark.parametrize(
    ("stat", "delta", "expected"),
    [
        ("atk", 1, "¡El Ataque de Pidgey salvaje subió!"),
        ("atk", 2, "¡El Ataque de Pidgey salvaje subió mucho!"),
        ("atk", 3, "¡El Ataque de Pidgey salvaje subió muchísimo!"),
        ("def", -1, "¡La Defensa de Pidgey salvaje bajó!"),
        ("def", -2, "¡La Defensa de Pidgey salvaje bajó mucho!"),
        ("spa", 1, "¡El Ataque Especial de Pidgey salvaje subió!"),
        ("spd", -3, "¡La Defensa Especial de Pidgey salvaje bajó muchísimo!"),
        ("spe", 1, "¡La Velocidad de Pidgey salvaje subió!"),
        ("accuracy", -1, "¡La Precisión de Pidgey salvaje bajó!"),
        ("evasion", 1, "¡La Evasión de Pidgey salvaje subió!"),
    ],
)
def test_stat_changes_use_the_right_article_and_intensity(stat, delta, expected):
    assert say(m.StatChanged(*PIDGEY, stat, delta)) == [expected]


def test_stat_limits_and_articles():
    assert say(m.StatUnchanged(*PIDGEY, "atk", rising=True)) == [
        "¡El Ataque de Pidgey salvaje no puede subir más!"
    ]
    assert say(m.StatUnchanged(*PIDGEY, "def", rising=False)) == [
        "¡La Defensa de Pidgey salvaje no puede bajar más!"
    ]
    assert stat_with_article("atk") == "el Ataque"
    assert stat_with_article("def") == "la Defensa"
    assert stat_with_article("hp") == "los PS"
    assert STAT_NAMES_ES["spa"] == "Ataque Especial"
    assert STAT_NAMES_ES["accuracy"] == "Precisión"


def test_damage_results_follow_gen3_order():
    hit = m.Damaged(*PIDGEY, 30, 40, 10, 40, effectiveness=2.0, critical=True)
    assert say(hit) == ["¡Un golpe crítico!", "¡Es muy eficaz!"]
    weak = m.Damaged(*PIDGEY, 3, 40, 37, 40, effectiveness=0.5)
    assert say(weak) == ["No es muy eficaz…"]
    assert say(m.Damaged(*PIDGEY, 8, 40, 32, 40)) == []  # golpe neutro: solo la barra


def test_multi_hit_effectiveness_is_said_once_after_the_last_hit():
    # Gen 3: el crítico se canta en cada golpe; la eficacia, una sola vez tras
    # el último y antes de «¡Alcanzó N veces!».
    first = m.Damaged(*PIDGEY, 10, 40, 30, 40, effectiveness=0.5, critical=True)
    last = m.Damaged(*PIDGEY, 10, 30, 20, 40, effectiveness=0.5)
    assert say(first, upcoming=last) == ["¡Un golpe crítico!"]
    assert say(last, upcoming=m.HitCount(*PIDGEY, 2)) == ["No es muy eficaz…"]
    assert say(first) == ["¡Un golpe crítico!", "No es muy eficaz…"]  # sin contexto, completo
    # Otro objetivo u otra causa detrás no lo convierten en multigolpe.
    recoil = m.Damaged(*CHARIZARD, 3, 50, 47, 50, cause=m.DamageCause.RECOIL)
    assert say(first, upcoming=recoil) == ["¡Un golpe crítico!", "No es muy eficaz…"]


def test_experience_and_level_use_the_bare_name_even_in_wild_battles():
    assert say(m.ExperienceGained("p0", "Charizard", 120)) == [
        "¡Charizard ganó 120 puntos de experiencia!"
    ]
    assert say(m.ExperienceGained("p0", "Charizard", 1)) == [
        "¡Charizard ganó 1 punto de experiencia!"
    ]
    assert say(m.LeveledUp("p0", "Charizard", 37, ())) == ["¡Charizard subió al nivel 37!"]
    assert say(m.MoveLearned("p0", "Charizard", "Lanzallamas")) == [
        "¡Charizard aprendió Lanzallamas!"
    ]
    assert say(m.MoveLearned("p0", "Charizard", "Lanzallamas", forgot="Placaje")) == [
        "1, 2 y… ¡Puf!",
        "¡Charizard olvidó Placaje y aprendió Lanzallamas!",
    ]


def test_ball_messages_name_the_target_and_depend_on_the_shakes():
    caught = m.BallThrown("superball", "Superball", 3, caught=True)
    assert say(caught, opponent="Pidgey") == [
        "¡Lanzaste una Superball!",
        "¡Ya está! ¡Has capturado a Pidgey!",
    ]
    assert say(caught)[-1] == "¡Ya está! ¡Has capturado al Pokémon!"
    escapes = [
        say(m.BallThrown("pokeball", "Pokeball", shakes, caught=False))[-1] for shakes in range(4)
    ]
    assert escapes[0] == "¡Oh, no! ¡El Pokémon se ha escapado!"
    assert len(set(escapes)) == 4


def test_screens_name_the_team_with_the_right_article():
    assert say(m.ScreenRaised("player", "reflect")) == [
        "¡Reflejo protege a tu equipo de los ataques físicos!"
    ]
    assert say(m.ScreenFaded("opponent", "light-screen"), TRAINER) == [
        "¡La Pantalla Luz del equipo rival se agotó!"
    ]
    assert say(m.ScreenFaded("player", "reflect")) == ["¡El Reflejo de tu equipo se agotó!"]


def test_display_label_by_battle_kind():
    assert display_label(WILD, "opponent", "Pidgey") == "Pidgey salvaje"
    assert display_label(TRAINER, "opponent", "Pidgey") == "Pidgey enemigo"
    assert display_label(EXHIBITION, "opponent", "Pidgey") == "Pidgey enemigo"
    assert display_label(WILD, "player", "Pidgey") == "Pidgey"


def test_status_badges_are_spanish_and_valid_rich_colors():
    labels = {status: badge[0] for status, badge in STATUS_BADGES.items()}
    assert labels == {
        m.Status.BURN: "QUE",
        m.Status.PARALYSIS: "PAR",
        m.Status.POISON: "ENV",
        m.Status.TOXIC: "ENV",
        m.Status.SLEEP: "DOR",
        m.Status.FREEZE: "CON",
    }
    for _, color in STATUS_BADGES.values():
        Color.parse(color)
    assert m.Status.NONE not in STATUS_BADGES


def _move(type_: str, cls: m.DamageClass, power: int | None, accuracy: int | None) -> m.Move:
    return m.Move("x", "X", type_, cls, power, accuracy, 10)


def test_move_summary_reads_like_the_game_menu():
    assert move_summary(_move("fire", m.DamageClass.SPECIAL, 90, 100)) == (
        "Fuego · Especial · Pot 90 · Prec 100"
    )
    assert move_summary(_move("electric", m.DamageClass.STATUS, None, 90)) == (
        "Eléctrico · Estado · Pot — · Prec 90"
    )
    assert move_summary(_move("psychic", m.DamageClass.PHYSICAL, 70, None)) == (
        "Psíquico · Físico · Pot 70 · Prec —"
    )


def test_effectiveness_hint_only_for_damaging_moves():
    assert (
        effectiveness_hint(_move("fire", m.DamageClass.SPECIAL, 90, 100), ("grass", "steel"))
        == "Muy eficaz"
    )
    assert (
        effectiveness_hint(_move("water", m.DamageClass.SPECIAL, 40, 100), ("water",))
        == "Poco eficaz"
    )
    assert (
        effectiveness_hint(_move("normal", m.DamageClass.PHYSICAL, 40, 100), ("ghost",))
        == "No afecta"
    )
    assert effectiveness_hint(_move("normal", m.DamageClass.PHYSICAL, 40, 100), ("fire",)) is None
    assert (
        effectiveness_hint(_move("electric", m.DamageClass.STATUS, None, 90), ("ground",)) is None
    )
    # Daño fijo (Sísmico): solo avisa de la inmunidad, no de la tabla.
    assert (
        effectiveness_hint(_move("fighting", m.DamageClass.PHYSICAL, None, 100), ("normal",))
        is None
    )
    assert (
        effectiveness_hint(_move("fighting", m.DamageClass.PHYSICAL, None, 100), ("ghost",))
        == "No afecta"
    )
