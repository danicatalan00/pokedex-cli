"""Escena de combate: sprites, disposición, cajas, barras y menús."""

from __future__ import annotations

import io

import pytest
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from pokedex_cli.domain.battle import model as m
from pokedex_cli.presentation.battle_scene import (
    FLASH_RGB,
    Hud,
    Particle,
    SceneModel,
    grid_size,
    hp_bar,
    hp_color,
    hp_units,
    layout,
    menu_text,
    moves_panel,
    prepare_sprite,
    render_scene,
    replace_side,
    scene_from_state,
    sprite_box,
)

RED = (220, 40, 40)
BLUE = (40, 60, 220)
GREEN = (40, 180, 60)
SIZES = [(100, 30), (80, 24), (60, 18)]


def ansi_sprite(cols: int, rows: int, left=RED, right=BLUE, pad: int = 3) -> str:
    """Sprite sintético con la forma de krabby: medios bloques truecolor,
    aire alrededor y la mitad izquierda de un color distinto a la derecha."""
    blank = " " * (cols + 2 * pad)
    lines = [blank]
    for _ in range(rows):
        cells = []
        for col in range(cols):
            r, g, b = left if col < cols // 2 else right
            cells.append(f"\x1b[38;2;{r};{g};{b}m\x1b[48;2;{r};{g};{b}m▀\x1b[0m")
        lines.append(" " * pad + "".join(cells) + " " * pad)
    return "\n".join(lines + [blank])


def pixels(grid) -> list[list[tuple]]:
    """Colores (arriba, abajo) de cada celda de una rejilla de sprite."""
    out = []
    for row in grid:
        line = []
        for cell in row:
            if cell.ch == "▀":
                line.append((cell.fg, cell.bg))
            elif cell.ch == "▄":
                line.append((cell.bg, cell.fg))
            else:
                line.append((cell.bg, cell.bg))
        out.append(line)
    return out


def styled(text: Text) -> list[list[tuple[str, tuple | None, tuple | None]]]:
    """(glifo, tinta, fondo) de cada celda de un Text renderizado."""
    styles = [None] * len(text.plain)
    for span in text.spans:
        for index in range(span.start, span.end):
            styles[index] = span.style
    rows: list[list[tuple[str, tuple | None, tuple | None]]] = [[]]
    for ch, style in zip(text.plain, styles):
        if ch == "\n":
            rows.append([])
            continue
        fg = tuple(style.color.get_truecolor()) if style and style.color else None
        bg = tuple(style.bgcolor.get_truecolor()) if style and style.bgcolor else None
        rows[-1].append((ch, fg, bg))
    return rows


def assert_canvas(text: Text, width: int, height: int) -> None:
    lines = text.plain.split("\n")
    assert len(lines) == height
    assert all(cell_len(line) == width for line in lines)


def combatant(key, species, name, *, hp=150, max_hp=200, level=36, status=m.Status.NONE, **extra):
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
        status=status,
        **extra,
    )


def battle(player: m.Combatant, opponent: m.Combatant, **extra) -> m.BattleState:
    return m.BattleState(
        kind=m.BattleKind.WILD,
        player=m.Side(team=(player,)),
        opponent=m.Side(team=(opponent,)),
        **extra,
    )


SPRITES = {"charizard": ansi_sprite(44, 22), "pidgey": ansi_sprite(18, 11, GREEN, RED)}


def lookup(species: str, form: str, shiny: bool) -> str | None:
    return SPRITES.get(species)


def full_scene(width: int, height: int) -> SceneModel:
    state = battle(
        combatant("p0", "charizard", "Charizard", gender="male", experience=50_000),
        combatant("o0", "pidgey", "Pidgey", hp=30, max_hp=40, level=12, status=m.Status.PARALYSIS),
    )
    return scene_from_state(state, lookup, width, height)


# --- Sprites ------------------------------------------------------------------


@pytest.mark.parametrize("ansi", [None, "", "   \n  "])
def test_missing_sprites_give_an_empty_grid(ansi):
    assert prepare_sprite(ansi, 10, 10, mirror=False) == []


@pytest.mark.parametrize("garbage", ["\x1b[38;2;1m▀", "\x1b[38;2m▀▀", "hola\x1b[48;2;9;9mx"])
def test_unreadable_sprites_never_raise(garbage):
    assert isinstance(prepare_sprite(garbage, 10, 10, mirror=True), list)


def test_air_around_the_sprite_is_trimmed_and_small_sprites_keep_their_size():
    grid = prepare_sprite(ansi_sprite(18, 11), 30, 40, mirror=False)
    assert grid_size(grid) == (11, 18)


@pytest.mark.parametrize(("max_rows", "max_cols"), [(15, 40), (11, 22), (9, 18), (12, 60), (3, 5)])
def test_big_sprites_shrink_to_fit_keeping_the_proportion(max_rows, max_cols):
    rows, cols = grid_size(prepare_sprite(ansi_sprite(44, 22), max_rows, max_cols, mirror=False))
    assert 0 < rows <= max_rows and 0 < cols <= max_cols
    # Aprovecha el sitio: toca el límite en alguna dimensión (salvo redondeo a medio bloque).
    assert rows >= max_rows - 1 or cols >= max_cols - 1
    assert abs(cols / (2 * rows) - 1.0) < 0.35  # el original es cuadrado en píxeles


def test_mirror_flips_columns_so_the_player_faces_the_opponent():
    original = pixels(prepare_sprite(ansi_sprite(10, 4), 10, 20, mirror=False))
    mirrored = pixels(prepare_sprite(ansi_sprite(10, 4), 10, 20, mirror=True))
    assert mirrored == [row[::-1] for row in original]
    assert original[0][0] == (RED, RED) and mirrored[0][0] == (BLUE, BLUE)


# --- Lienzo ---------------------------------------------------------------------


@pytest.mark.parametrize(("width", "height"), SIZES + [(40, 12), (24, 8), (1, 1)])
def test_render_always_fills_exactly_the_canvas(width, height):
    scene = full_scene(width, height)
    assert_canvas(render_scene(scene), width, height)
    # Ni efectos exagerados ni partículas fuera del lienzo cambian el tamaño.
    wild = replace_side(scene, "player", offset=(-50, 300), sink=4)
    wild = replace_side(wild, "opponent", offset=(40, -200), flash=True)
    wild = SceneModel(**{**wild.__dict__, "shake": 7, "particles": (Particle(-3, 999, "✦", RED),)})
    assert_canvas(render_scene(wild), width, height)


def test_empty_cells_carry_no_colour():
    hidden = replace_side(
        replace_side(full_scene(100, 30), "player", visible=False, hud=None),
        "opponent",
        visible=False,
        hud=None,
    )
    cells = styled(render_scene(hidden))
    assert cells[0][0] == (" ", None, None)
    assert cells[0][99] == (" ", None, None)


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_layout_keeps_boxes_inside_and_apart(width, height):
    plan = layout(width, height)
    boxes = {
        "opponent_hud": plan.opponent_hud,
        "player_hud": plan.player_hud,
        "opponent_area": plan.opponent_area,
        "player_area": plan.player_area,
    }
    for name, box in boxes.items():
        assert box.row >= 0 and box.col >= 0, name
        assert box.bottom < height and box.right < width, name

    def overlap(a, b) -> bool:
        return not (a.right < b.col or b.right < a.col or a.bottom < b.row or b.bottom < a.row)

    names = list(boxes)
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            assert not overlap(boxes[first], boxes[second]), (first, second)
    # Clásico de GBA: rival arriba a la derecha y jugador abajo a la izquierda.
    assert plan.opponent_area.col > plan.player_area.col
    assert plan.opponent_area.row < plan.player_area.bottom
    assert plan.opponent_hud.col < plan.opponent_area.col
    assert plan.player_hud.col > plan.player_area.right


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_hud_text_shows_name_level_badge_and_player_numbers(width, height):
    plain = render_scene(full_scene(width, height)).plain
    for fragment in ("Pidgey", "Nv.12", "PAR", "Charizard", "Nv.36", "150/200"):
        assert fragment in plain, fragment
    assert "30/ 40" not in plain  # el rival no enseña sus números


def test_long_names_are_truncated_inside_their_box():
    state = battle(
        combatant("p0", "charizard", "Supercalifragilístico"),
        combatant("o0", "pidgey", "Ultramegaplumífero", status=m.Status.TOXIC, shiny=True),
    )
    text = render_scene(scene_from_state(state, lookup, 60, 18))
    assert_canvas(text, 60, 18)
    assert "…" in text.plain


def test_particles_draw_over_sprites_without_punching_holes():
    scene = full_scene(100, 30)
    box = sprite_box(scene, "opponent")
    row, col = box.row + box.height // 2, box.col + box.width // 2
    sparkled = SceneModel(**{**scene.__dict__, "particles": (Particle(row, col, "✦", BLUE),)})
    glyph, fg, bg = styled(render_scene(sparkled))[row][col]
    assert (glyph, fg) == ("✦", BLUE)
    assert bg is not None  # hereda el color del sprite que tapa


def test_flash_paints_the_sprite_white():
    scene = full_scene(100, 30)
    box = sprite_box(scene, "opponent")
    cells = styled(render_scene(replace_side(scene, "opponent", flash=True)))
    center = cells[box.row + box.height // 2][box.col + box.width // 2]
    assert FLASH_RGB in (center[1], center[2])


def test_sinking_hides_the_sprite_behind_its_platform():
    scene = full_scene(100, 30)
    box = sprite_box(scene, "opponent")

    def painted(sink: int) -> int:
        cells = styled(render_scene(replace_side(scene, "opponent", sink=sink, hud=None)))
        return sum(
            1
            for row in cells[box.row : box.bottom + 1]
            for ch, fg, bg in row[box.col : box.right + 1]
            if RED in (fg, bg) or GREEN in (fg, bg)
        )

    counts = [painted(sink) for sink in range(0, box.height + 1, 2)] + [painted(box.height)]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] > 0 and counts[-1] == 0


def test_moving_down_never_crops_the_sprite_only_sinking_does():
    # El rival embiste hacia abajo, hacia el jugador: bajar una fila no debe
    # comerle los pies; solo ``sink`` esconde filas tras la plataforma.
    scene = replace_side(full_scene(100, 30), "player", visible=False, hud=None)
    scene = replace_side(scene, "opponent", hud=None)

    def sprite_cells(candidate: SceneModel) -> int:
        return sum(
            1
            for row in styled(render_scene(candidate))
            for _, fg, bg in row
            if GREEN in (fg, bg) or RED in (fg, bg)
        )

    at_rest = sprite_cells(scene)
    assert at_rest > 0
    assert sprite_cells(replace_side(scene, "opponent", offset=(1, 0))) == at_rest
    assert sprite_cells(replace_side(scene, "opponent", offset=(1, 0), sink=3)) < at_rest


def test_shake_moves_everything_sideways():
    scene = full_scene(80, 24)
    still = render_scene(scene).plain.split("\n")
    shaken = render_scene(SceneModel(**{**scene.__dict__, "shake": 2})).plain.split("\n")
    assert all(moved == "  " + line[:-2] for line, moved in zip(still, shaken))


# --- Estado → escena ------------------------------------------------------------


def test_scene_from_state_builds_huds_and_mirrors_the_player():
    scene = full_scene(100, 30)
    assert scene.player_hud == Hud(
        name="Charizard",
        level=36,
        gender="male",
        status=m.Status.NONE,
        hp=150,
        max_hp=200,
        exp_fraction=pytest.approx((50_000 - 36**3) / (37**3 - 36**3)),
        shiny=False,
        show_numbers=True,
    )
    assert scene.opponent_hud is not None
    assert scene.opponent_hud.show_numbers is False and scene.opponent_hud.exp_fraction is None
    assert pixels(scene.player_sprite)[0][0] == (BLUE, BLUE)  # espejado
    assert pixels(scene.opponent_sprite)[0][0] == (GREEN, GREEN)


def test_fainted_airborne_and_captured_pokemon_are_hidden():
    flying = m.Volatile(semi_invulnerable=True, charging="fly")
    state = battle(
        combatant("p0", "charizard", "Charizard", volatile=flying),
        combatant("o0", "pidgey", "Pidgey", hp=0, max_hp=40),
    )
    scene = scene_from_state(state, lookup, 80, 24)
    assert not scene.player_visible and scene.player_hud is not None
    assert not scene.opponent_visible and scene.opponent_hud is None
    captured = battle(
        combatant("p0", "charizard", "Charizard"),
        combatant("o0", "pidgey", "Pidgey"),
        phase=m.Phase.FINISHED,
        outcome=m.Outcome.CAPTURED,
    )
    after = scene_from_state(captured, lookup, 80, 24)
    assert not after.opponent_visible and after.opponent_hud is None and after.player_visible


def test_exhibitions_hide_experience_and_failing_lookups_use_a_placeholder():
    def broken(species: str, form: str, shiny: bool) -> str | None:
        raise RuntimeError("krabby no está")

    state = battle(
        combatant("p0", "charizard", "Charizard"),
        combatant("o0", "pidgey", "Pidgey"),
        award_experience=False,
    )
    scene = scene_from_state(state, broken, 60, 18)
    assert scene.player_sprite == [] and scene.opponent_sprite == []
    assert scene.player_hud is not None and scene.player_hud.exp_fraction is None
    text = render_scene(scene)
    assert_canvas(text, 60, 18)
    box = sprite_box(scene, "opponent")
    assert any(ch != " " for ch in text.plain.split("\n")[box.row + 1][box.col : box.right + 1])


# --- Barras y menús ---------------------------------------------------------------


def test_hp_units_are_monotone_and_honest_at_the_extremes():
    width = 24
    units = [hp_units(hp, 200, width) for hp in range(0, 201)]
    assert units == sorted(units)
    assert units[0] == 0 and units[1] >= 1
    assert units[199] < 2 * width and units[200] == 2 * width


@pytest.mark.parametrize(
    ("hp", "expected"),
    [(200, "green"), (101, "green"), (100, "yellow"), (41, "yellow"), (40, "red"), (1, "red")],
)
def test_hp_bar_colour_thresholds(hp, expected):
    color = hp_color(hp, 200)
    dominant = {"green": 1, "yellow": None, "red": 0}[expected]
    if dominant is None:
        assert color[0] > 150 and color[1] > 150 and color[2] < 100
    else:
        assert color.index(max(color)) == dominant
    bar = hp_bar(hp, 200, 12)
    assert cell_len(bar.plain) == 12
    assert any(color in (fg, bg) for _, fg, bg in styled(bar)[0])


def test_hp_bar_uses_half_cells_for_precision():
    cells = styled(hp_bar(1, 48, 24))[0]
    assert cells[0][0] == "▌"
    assert all(ch == " " for ch, _, _ in cells[1:])


def test_menu_marks_the_selected_option_in_a_two_column_grid():
    text = menu_text(["LUCHAR", "BOLSA", "POKÉMON", "HUIR"], 2)
    first, second = text.plain.split("\n")
    assert "LUCHAR" in first and "BOLSA" in first
    assert "POKÉMON" in second and "HUIR" in second
    assert "▶ POKÉMON" in second and "▶" not in first


def _slot(slug, name, type_, cls, power, accuracy, pp, max_pp):
    return m.MoveSlot(m.Move(slug, name, type_, cls, power, accuracy, max_pp), pp, max_pp)


def test_moves_panel_shows_a_grid_and_the_detail_of_the_selected_move():
    slots = [
        _slot("flamethrower", "Lanzallamas", "fire", m.DamageClass.SPECIAL, 90, 100, 15, 15),
        _slot("growl", "Gruñido", "normal", m.DamageClass.STATUS, None, 100, 0, 40),
        _slot("wing-attack", "Ataque Ala", "flying", m.DamageClass.PHYSICAL, 60, 100, 35, 35),
    ]
    text = moves_panel(slots, 0, ("grass", "steel"))
    lines = text.plain.split("\n")
    assert len(lines) == 3
    assert "▶" in lines[0] and "Lanzallamas" in lines[0] and "Gruñido" in lines[0]
    assert "Ataque Ala" in lines[1] and "—" in lines[1]
    assert lines[2] == "PP 15/15 · Fuego · Especial · Pot 90 · Prec 100 · Muy eficaz"
    empty = moves_panel(slots, 1, ("normal",)).plain.split("\n")[2]
    assert empty.startswith("PP 0/40 · Normal · Estado")
    assert moves_panel([], 0, ()).plain == "Sin movimientos."


def test_panels_render_through_rich_with_every_style():
    # rich solo valida los estilos con nombre al pintar: se pinta cada variante.
    console = Console(file=io.StringIO(), color_system="truecolor", width=100)
    slots = [
        _slot("water-gun", "Pistola Agua", "water", m.DamageClass.SPECIAL, 40, 100, 25, 25),
        _slot("ember", "Ascuas", "fire", m.DamageClass.SPECIAL, 40, 100, 2, 25),
        _slot("tackle", "Placaje", "normal", m.DamageClass.PHYSICAL, 40, 100, 0, 35),
    ]
    for defender in (("fire",), ("water",), ("ghost",), ("normal",)):
        for selected in range(len(slots)):
            console.print(moves_panel(slots, selected, defender))
    console.print(menu_text(["LUCHAR", "BOLSA", "POKÉMON", "HUIR"], 3))
    output = console.file.getvalue()
    assert "Muy eficaz" in output and "Poco eficaz" in output and "No afecta" in output
