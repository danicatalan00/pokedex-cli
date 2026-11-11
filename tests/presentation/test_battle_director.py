"""Director de animaciones: guiones completos, coherentes y de tamaño fijo."""

from __future__ import annotations

import math
import typing
from dataclasses import replace

import pytest
from rich.cells import cell_len

from pokedex_cli.domain.battle import model as m
from pokedex_cli.domain.battle.typechart import TYPES
from pokedex_cli.presentation.battle_director import Beat, Director
from pokedex_cli.presentation.battle_scene import (
    layout,
    prepare_sprite,
    render_scene,
    scene_from_state,
    side_view,
)
from pokedex_cli.presentation.battle_text import messages_for

WILD = m.BattleKind.WILD
TRAINER = m.BattleKind.TRAINER
SPECIAL = m.DamageClass.SPECIAL
PHYSICAL = m.DamageClass.PHYSICAL
STATUS = m.DamageClass.STATUS
START_EXP = 36**3 + 1000


def ansi_sprite(cols: int, rows: int, left: tuple, right: tuple) -> str:
    lines = [" " * (cols + 4)]
    for _ in range(rows):
        cells = "".join(
            "\x1b[38;2;{0};{1};{2}m\x1b[48;2;{0};{1};{2}m▀\x1b[0m".format(
                *(left if col < cols // 2 else right)
            )
            for col in range(cols)
        )
        lines.append("  " + cells + "  ")
    return "\n".join(lines + [" " * (cols + 4)])


SPRITES = {
    "charizard": ansi_sprite(44, 22, (240, 120, 40), (200, 60, 50)),
    "pidgey": ansi_sprite(18, 11, (170, 120, 70), (230, 210, 150)),
    "onix": ansi_sprite(33, 23, (120, 120, 120), (180, 180, 180)),
    "noctowl": ansi_sprite(20, 12, (120, 80, 40), (60, 40, 20)),
}


def lookup(species: str, form: str, shiny: bool) -> str | None:
    return SPRITES.get(species)


def combatant(key, species, name, *, level, hp, max_hp, **extra) -> m.Combatant:
    stats = m.Stats(max_hp, 50, 50, 50, 50, 50)
    return m.Combatant(
        key=key,
        species=species,
        form="regular",
        name=name,
        level=level,
        types=("normal",),
        base_stats=stats,
        ivs=stats,
        nature="hardy",
        stats=stats,
        hp=hp,
        moves=(),
        gender="male",
        **extra,
    )


def charizard(**changes) -> m.Combatant:
    values = {"level": 36, "hp": 120, "max_hp": 120, "experience": START_EXP} | changes
    return combatant("p0", "charizard", "Charizard", **values)


def pidgey(**changes) -> m.Combatant:
    return combatant("o0", "pidgey", "Pidgey", **({"level": 12, "hp": 40, "max_hp": 40} | changes))


def battle(player=None, opponent=None, *, kind=WILD, **extra) -> m.BattleState:
    opponents = opponent if isinstance(opponent, tuple) else (opponent or pidgey(),)
    return m.BattleState(
        kind=kind,
        player=m.Side(team=(player or charizard(),)),
        opponent=m.Side(team=opponents),
        **extra,
    )


OPENING = [
    m.BattleStarted(WILD, None),
    m.SentOut("opponent", "o0", "Pidgey", "pidgey", "regular", False, 12),
    m.SentOut("player", "p0", "Charizard", "charizard", "regular", False, 36),
]
FIRST_TURN = [
    m.TurnStarted(1),
    m.MoveUsed("player", "p0", "Charizard", "flamethrower", "Lanzallamas", "fire", SPECIAL),
    m.Damaged("opponent", "o0", "Pidgey", 25, 40, 15, 40, effectiveness=2.0, critical=True),
    m.StatusInflicted("opponent", "o0", "Pidgey", m.Status.BURN),
    m.MoveUsed("opponent", "o0", "Pidgey", "growl", "Gruñido", "normal", STATUS),
    m.StatChanged("player", "p0", "Charizard", "atk", -1),
    m.Damaged("opponent", "o0", "Pidgey", 5, 15, 10, 40, cause=m.DamageCause.BURN),
]
LAST_TURN = [
    m.TurnStarted(2),
    m.MoveUsed("player", "p0", "Charizard", "wing-attack", "Ataque Ala", "flying", PHYSICAL),
    m.Damaged("opponent", "o0", "Pidgey", 10, 10, 0, 40),
    m.Fainted("opponent", "o0", "Pidgey"),
    m.ExperienceGained("p0", "Charizard", 4000),
    m.LeveledUp("p0", "Charizard", 37, (("hp", 3), ("atk", 2))),
    m.MoveLearned("p0", "Charizard", "Garra Dragón", forgot="Arañazo"),
    m.BattleEnded(m.Outcome.WON),
]
SCRIPT = OPENING + FIRST_TURN + LAST_TURN
FINAL = battle(
    charizard(level=37, hp=123, max_hp=123, experience=START_EXP + 4000),
    pidgey(hp=0, status=m.Status.BURN),
    phase=m.Phase.FINISHED,
    outcome=m.Outcome.WON,
)


def pixels(grid) -> list[list[tuple]]:
    return [[(cell.ch, cell.fg, cell.bg) for cell in row] for row in grid]


def play(
    state=None, events=SCRIPT, size=(100, 30), kind=WILD, trainer=None
) -> tuple[Director, list[Beat]]:
    director = Director(state or battle(), lookup, *size)
    return director, director.direct(events, kind=kind, trainer=trainer)


def rest_fields(scene) -> dict[str, object]:
    return {
        side + "_" + name: value
        for side in ("player", "opponent")
        for name, value in side_view(scene, side)._asdict().items()
        if name != "sprite"
    } | {"particles": scene.particles, "shake": scene.shake}


# --- Contrato -----------------------------------------------------------------


def samples() -> list[m.BattleEvent]:
    """Un ejemplar de cada evento, con las claves del combate de prueba."""
    p, o = ("player", "p0", "Charizard"), ("opponent", "o0", "Pidgey")
    return [
        m.BattleStarted(TRAINER, "Domadora Celia"),
        m.TurnStarted(3),
        m.SentOut("opponent", "o1", "Onix", "onix", "regular", True, 14),
        m.Recalled(*p),
        m.MoveUsed(*p, "hypnosis", "Hipnosis", "psychic", STATUS),
        m.NoMovesLeft(*p),
        m.MoveMissed(*o),
        m.MoveFailed(*p, m.FailReason.UNSUPPORTED),
        m.NoEffect(*o),
        m.ProtectionRaised(*p),
        m.Protected(*p),
        m.Damaged(*o, 30, 40, 10, 40, effectiveness=0.5, critical=True),
        m.HitCount(*o, 3),
        m.OneHitKO(*o),
        m.Healed(*p, 20, 60, 80, 120, cause=m.HealCause.DRAIN),
        m.StatusInflicted(*o, m.Status.SLEEP),
        m.StatusCured(*o, m.Status.FREEZE),
        m.StatusBlocked(*o, m.Status.PARALYSIS, already=False),
        m.Immobilized(*o, m.ImmobileReason.PARALYZED),
        m.Confused(*o),
        m.ConfusionCheck(*o),
        m.ConfusionHurt(*o),
        m.ConfusionEnded(*o),
        m.StatChanged(*p, "spe", 2),
        m.StatUnchanged(*p, "atk", rising=False),
        m.StatsReset(),
        m.PumpedUp(*p),
        m.Charging(*p, "fly", "Vuelo"),
        m.ScreenRaised("opponent", "light-screen"),
        m.ScreenFaded("player", "reflect"),
        m.Seeded(*o),
        m.Trapped(*o, "Constricción"),
        m.Released(*o, "Constricción"),
        m.Teleported(*o),
        m.ForcedOut(*o),
        m.Fainted(*p),
        m.ExperienceGained("p0", "Charizard", 300),
        m.LeveledUp("p0", "Charizard", 37, (("hp", 3),)),
        m.MoveLearned("p0", "Charizard", "Lanzallamas"),
        m.EscapeAttempted(success=True),
        m.BallThrown("ultraball", "Ultraball", 3, caught=True),
        m.OpponentFled("Pidgey"),
        m.ReplacementNeeded("player"),
        m.BattleEnded(m.Outcome.LOST),
    ]


def test_samples_cover_every_event_of_the_model():
    assert {type(event) for event in samples()} == set(typing.get_args(m.BattleEvent))


@pytest.mark.parametrize("event", samples(), ids=lambda event: type(event).__name__)
def test_director_accepts_every_event_and_narrates_its_messages(event):
    director, beats = play(events=[event], size=(80, 24))
    expected = messages_for(event, kind=WILD, trainer=None, opponent="Pidgey")
    if isinstance(event, m.TurnStarted):
        assert beats == []  # el turno no se escribe en la caja de texto
        return
    assert [beat.message for beat in beats if beat.message is not None] == expected
    assert beats, "cada evento produce al menos un beat"
    for beat in beats:
        assert beat.delay > 0 and math.isfinite(beat.delay)
        assert (beat.scene.width, beat.scene.height) == (80, 24)
        assert all(cell_len(particle.glyph) == 1 for particle in beat.scene.particles)
    assert director.scene is beats[-1].scene


def test_the_whole_script_is_narrated_in_order():
    _, beats = play()
    following = [*SCRIPT[1:], None]
    expected = [
        line
        for event, upcoming in zip(SCRIPT, following)
        if not isinstance(event, m.TurnStarted)
        for line in messages_for(
            event, kind=WILD, trainer=None, opponent="Pidgey", upcoming=upcoming
        )
    ]
    assert [beat.message for beat in beats if beat.message] == expected


def test_multi_hit_moves_announce_effectiveness_once_after_the_last_hit():
    events = [
        m.MoveUsed(
            "player", "p0", "Charizard", "double-kick", "Doble Patada", "fighting", PHYSICAL
        ),
        m.Damaged("opponent", "o0", "Pidgey", 15, 40, 25, 40, effectiveness=2.0),
        m.Damaged("opponent", "o0", "Pidgey", 25, 25, 0, 40, effectiveness=2.0, critical=True),
        m.HitCount("opponent", "o0", "Pidgey", 2),
        m.Fainted("opponent", "o0", "Pidgey"),
    ]
    _, beats = play(events=events)
    # Gen 3: crítico en cada golpe; la eficacia, una vez y antes del recuento.
    assert [beat.message for beat in beats if beat.message] == [
        "¡Charizard usó Doble Patada!",
        "¡Un golpe crítico!",
        "¡Es muy eficaz!",
        "¡Alcanzó 2 veces!",
        "¡Pidgey salvaje se debilitó!",
    ]


@pytest.mark.parametrize("size", [(100, 30), (80, 24), (60, 18)])
def test_no_frame_ever_changes_size(size):
    width, height = size
    _, beats = play(size=size)
    seen: set[int] = set()
    for beat in beats:
        if id(beat.scene) in seen:
            continue
        seen.add(id(beat.scene))
        lines = render_scene(beat.scene).plain.split("\n")
        assert len(lines) == height
        assert all(cell_len(line) == width for line in lines)


def test_hp_bars_only_move_in_one_direction():
    _, beats = play()
    opponent_hp = [beat.scene.opponent_hud.hp for beat in beats if beat.scene.opponent_hud]
    assert opponent_hp == sorted(opponent_hp, reverse=True)
    assert opponent_hp[0] == 40 and opponent_hp[-1] == 0
    assert len(set(opponent_hp)) > 6  # baja en varios fotogramas, no de golpe

    healing = [m.Healed("player", "p0", "Charizard", 50, 40, 90, 120)]
    _, heal_beats = play(battle(charizard(hp=40)), healing)
    player_hp = [beat.scene.player_hud.hp for beat in heal_beats if beat.scene.player_hud]
    assert player_hp == sorted(player_hp) and player_hp[-1] == 90


def test_fainting_sinks_and_hides_the_sprite():
    _, beats = play(events=[m.Fainted("opponent", "o0", "Pidgey")])
    sinks = [beat.scene.opponent_sink for beat in beats if beat.scene.opponent_visible]
    assert sinks == sorted(sinks) and max(sinks) > 0
    last = beats[-1].scene
    assert not last.opponent_visible and last.opponent_hud is None
    assert beats[-1].message == "¡Pidgey salvaje se debilitó!"


@pytest.mark.parametrize("size", [(100, 30), (60, 18)])
def test_script_ends_coherent_with_the_final_state(size):
    director, beats = play(size=size)
    expected = scene_from_state(FINAL, lookup, *size)
    last = beats[-1].scene
    assert rest_fields(last) == rest_fields(expected)
    assert pixels(last.player_sprite) == pixels(expected.player_sprite)
    resynced = director.scene_for(FINAL)
    assert rest_fields(resynced) == rest_fields(last)


def test_sent_out_swaps_sprite_and_box_and_mirrors_the_player():
    state = battle(
        opponent=(pidgey(), combatant("o1", "onix", "Onix", level=14, hp=50, max_hp=50)),
        kind=TRAINER,
    )
    director = Director(state, lookup, 100, 30)
    beats = director.direct(
        [
            m.Recalled("opponent", "o0", "Pidgey"),
            m.SentOut("opponent", "o1", "Onix", "onix", "regular", False, 14),
        ],
        kind=TRAINER,
        trainer="Domadora Celia",
    )
    assert [beat.message for beat in beats if beat.message] == [
        "¡Domadora Celia retiró a Pidgey!",
        "¡Domadora Celia sacó a Onix!",
    ]
    assert any(not beat.scene.opponent_visible for beat in beats)
    assert any(beat.scene.opponent_flash for beat in beats)  # destello de la Poké Ball
    final = director.scene
    assert final.opponent_visible and final.opponent_hud is not None
    assert final.opponent_hud.name == "Onix" and final.opponent_hud.hp == 50
    area = layout(100, 30).opponent_area
    assert pixels(final.opponent_sprite) == pixels(
        prepare_sprite(SPRITES["onix"], area.height, area.width, mirror=False)
    )
    player_area = layout(100, 30).player_area
    assert pixels(final.player_sprite) == pixels(
        prepare_sprite(SPRITES["charizard"], player_area.height, player_area.width, mirror=True)
    )


@pytest.mark.parametrize("move_type", TYPES)
@pytest.mark.parametrize("damage_class", [PHYSICAL, SPECIAL])
def test_every_type_has_its_own_attack_animation(move_type, damage_class):
    events = [
        m.MoveUsed("player", "p0", "Charizard", "x", "Golpe", move_type, damage_class),
        m.Damaged("opponent", "o0", "Pidgey", 10, 40, 30, 40),
    ]
    _, beats = play(events=events, size=(80, 24))
    animated = [beat for beat in beats if beat.scene.particles]
    assert len(animated) >= 3
    for beat in animated:
        assert all(cell_len(particle.glyph) == 1 for particle in beat.scene.particles)
        lines = render_scene(beat.scene).plain.split("\n")
        assert len(lines) == 24 and all(cell_len(line) == 80 for line in lines)


def test_physical_moves_lunge_and_misses_skip_the_animation():
    lunge = [
        m.MoveUsed("player", "p0", "Charizard", "slash", "Cuchillada", "normal", PHYSICAL),
        m.Damaged("opponent", "o0", "Pidgey", 10, 40, 30, 40),
    ]
    _, beats = play(events=lunge)
    offsets = [beat.scene.player_offset for beat in beats]
    assert any(col > 0 and row <= 0 for row, col in offsets)  # embiste hacia arriba a la derecha
    assert beats[-1].scene.player_offset == (0, 0)

    missed = [lunge[0], m.MoveMissed("player", "p0", "Charizard")]
    _, missed_beats = play(events=missed)
    assert all(not beat.scene.particles for beat in missed_beats)
    assert [beat.message for beat in missed_beats] == [
        "¡Charizard usó Cuchillada!",
        "¡El ataque de Charizard falló!",
    ]


def test_fly_hides_the_user_until_it_strikes():
    director, beats = play(
        events=[
            m.MoveUsed("player", "p0", "Charizard", "fly", "Vuelo", "flying", PHYSICAL),
            m.Charging("player", "p0", "Charizard", "fly", "Vuelo"),
        ]
    )
    assert not beats[-1].scene.player_visible
    strike = director.direct(
        [
            m.MoveUsed("player", "p0", "Charizard", "fly", "Vuelo", "flying", PHYSICAL),
            m.Damaged("opponent", "o0", "Pidgey", 12, 40, 28, 40),
        ],
        kind=WILD,
        trainer=None,
    )
    assert strike[-1].scene.player_visible


def test_capture_hides_the_opponent_and_a_breakout_brings_it_back():
    caught = [m.BallThrown("ultraball", "Ultraball", 3, caught=True)]
    _, beats = play(events=caught)
    # La Ultra Ball se dibuja con sus colores: negra con franjas amarillas.
    ball_colors = {particle.rgb for beat in beats for particle in beat.scene.particles}
    assert {(40, 40, 40), (250, 197, 35)} <= ball_colors
    assert not beats[-1].scene.opponent_visible and beats[-1].scene.opponent_hud is None
    assert beats[-1].message == "¡Ya está! ¡Has capturado a Pidgey!"

    escaped = [m.BallThrown("pokeball", "Pokeball", 1, caught=False)]
    _, beats = play(events=escaped)
    assert any(not beat.scene.opponent_visible for beat in beats)
    assert beats[-1].scene.opponent_visible and beats[-1].scene.opponent_hud is not None


def test_catching_a_pokemon_hidden_mid_fly_removes_its_box():
    # La bola no mira si el salvaje está en el aire: si lo atrapa, su caja se va.
    flying = m.Volatile(semi_invulnerable=True, charging="fly")
    state = battle(opponent=pidgey(volatile=flying))
    caught = [
        m.BallThrown("pokeball", "Poké Ball", 3, caught=True),
        m.BattleEnded(m.Outcome.CAPTURED),
    ]
    _, beats = play(state, caught)
    final = replace(state, phase=m.Phase.FINISHED, outcome=m.Outcome.CAPTURED)
    assert rest_fields(beats[-1].scene) == rest_fields(scene_from_state(final, lookup, 100, 30))
    assert [beat.message for beat in beats if beat.message] == [
        "¡Lanzaste una Poké Ball!",
        "¡Ya está! ¡Has capturado a Pidgey!",
    ]


@pytest.mark.parametrize(
    ("forced", "outcome"),
    [
        ("opponent", m.Outcome.OPPONENT_FLED),
        ("opponent", m.Outcome.ESCAPED),
        ("player", m.Outcome.ESCAPED),
        ("player", m.Outcome.OPPONENT_FLED),
    ],
)
def test_scene_for_keeps_on_the_field_whoever_did_not_leave(forced, outcome):
    # Un Rugido en salvaje acaba el combate y el resultado puede nombrar a quien
    # usó el movimiento: tras resincronizar, sigue en el campo quien se quedó.
    who = {"player": ("player", "p0", "Charizard"), "opponent": ("opponent", "o0", "Pidgey")}
    director, beats = play(events=[m.ForcedOut(*who[forced]), m.BattleEnded(outcome)])
    synced = director.scene_for(battle(phase=m.Phase.FINISHED, outcome=outcome))
    assert rest_fields(synced) == rest_fields(beats[-1].scene)
    assert not side_view(synced, forced).visible
    assert side_view(synced, m.other_side(forced)).visible


def test_experience_fills_the_bar_before_the_level_up():
    _, beats = play(events=[m.ExperienceGained("p0", "Charizard", 2000)])
    fractions = [beat.scene.player_hud.exp_fraction for beat in beats if beat.scene.player_hud]
    assert fractions == sorted(fractions) and fractions[-1] > fractions[0]


def test_unknown_keys_and_lone_turn_markers_are_tolerated():
    director, beats = play(
        events=[
            m.TurnStarted(9),
            m.Damaged("opponent", "zz", "Fantasma", 5, 10, 5, 10),
            m.Fainted("player", "nobody", "Nadie"),
            m.SentOut("opponent", "o9", "Missingno", "missingno", "regular", False, 1),
        ]
    )
    assert director.scene.opponent_visible and director.scene.opponent_hud is not None
    lines = render_scene(director.scene).plain.split("\n")
    assert len(lines) == 30


def test_directing_is_deterministic():
    def film() -> list[tuple[str | None, float, str]]:
        _, beats = play(size=(80, 24))
        return [(beat.message, beat.delay, render_scene(beat.scene).plain) for beat in beats]

    assert film() == film()
