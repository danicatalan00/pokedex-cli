import argparse
import os
import select
import sys
import termios
import tty
from datetime import datetime, timezone

from rich.console import Console
from rich.live import Live
from rich.table import Table

import json

from pokedex_cli import (
    animation, capture, display, inventory, krabby_bridge, paths, pokeapi,
    progression, storage,
)

console = Console()
_display_name = display.display_name


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_with_cache(conn, capture_row) -> dict:
    row = dict(capture_row)
    cache = storage.get_species_cache(conn, row["species"], row["form"])
    if cache is not None:
        row["types"] = json.loads(cache["types"]) if cache["types"] else []
        row["hp"] = cache["hp"]
        row["atk"] = cache["atk"]
        row["def"] = cache["def"]
        row["spa"] = cache["spa"]
        row["spd"] = cache["spd"]
        row["spe"] = cache["spe"]
        row["is_legendary"] = cache["is_legendary"]
        row["is_mythical"] = cache["is_mythical"]
        row["pokedex_id"] = cache["pokedex_id"]
        row["generation"] = cache["generation"]
        row["flavor_text"] = cache["flavor_text"]
        row["capture_rate"] = cache["capture_rate"]
        row["form_data_exact"] = cache["form_data_exact"]
        row["growth_rate"] = cache["growth_rate"]
        row["base_experience"] = cache["base_experience"]
    else:
        row["types"] = None
        row["is_legendary"] = 0
        row["is_mythical"] = 0
        row["pokedex_id"] = None
        row["generation"] = None
        row["flavor_text"] = None
        row["capture_rate"] = None
        row["form_data_exact"] = 1
        row["growth_rate"] = "medium"
        row["base_experience"] = progression.DEFAULT_BASE_EXPERIENCE
    return row


def _print_training(results: tuple[progression.TrainingResult, ...]) -> None:
    for result in results:
        name = _display_name(result.species, "regular")
        message = f"[cyan]{name}[/] gana [bold]+{result.experience} EXP[/]"
        if result.changed_lines:
            message += f" [dim]({result.changed_lines} líneas cambiadas)[/]"
        if result.new_level > result.old_level:
            message += f" · [bold yellow]¡sube al nivel {result.new_level}![/]"
        console.print(message)


def _sync_training(*, force_repo_scan: bool = False):
    result = inventory.sync_activity(force_repo_scan=force_repo_scan)
    conn = storage.get_connection()
    # Bases anteriores a la progresion ya tienen ficha, pero no los campos de
    # crecimiento/evolucion. Se enriquecen una sola vez y se conserva fallback
    # offline si PokeAPI no esta disponible.
    for member in conn.execute("SELECT * FROM captures WHERE in_team = 1").fetchall():
        cache = storage.get_species_cache(conn, member["species"], member["form"])
        if cache is None or not cache["growth_rate"]:
            data = pokeapi.fetch_species_data(member["species"], member["form"])
            if data is not None:
                storage.upsert_species_cache(
                    conn, member["species"], member["form"], data, _now_iso()
                )
    workload = result.commits if result.commits else result.new_work_commits
    training = progression.apply_commit_experience(conn, workload)
    return result, training


def cmd_hook(args: argparse.Namespace) -> int:
    def write_last_seen(species: str, form: str, shiny: bool) -> None:
        paths.write_last_seen(species, form, shiny, _now_iso())

    conn = storage.get_connection()
    # Una evolucion ya pendiente pertenece a la apertura anterior. La actividad
    # nueva se sincroniza ahora, pero cualquier nueva evolucion esperara a la
    # proxima terminal tal como en el bucle del juego.
    pending = storage.list_pending_evolutions(conn)
    _, training = _sync_training()
    _print_training(training)
    if pending:
        for pokemon in pending:
            target_species = pokemon["pending_evolution_species"]
            target_form = pokemon["pending_evolution_form"] or "regular"
            data = pokeapi.fetch_species_data(target_species, target_form)
            if data is not None:
                storage.upsert_species_cache(
                    conn, target_species, target_form, data, _now_iso()
                )
            animation.play_evolution_animation(
                console, pokemon["species"], pokemon["form"], target_species,
                target_form, bool(pokemon["shiny"]),
            )
            storage.complete_evolution(conn, pokemon["id"])
            progression.queue_current_evolution(conn, pokemon["id"])
        paths.clear_last_seen()
        return 0
    krabby_bridge.run_hook(args.generations, write_last_seen)
    return 0


def cmd_ver(args: argparse.Namespace) -> int:
    last_seen = paths.read_last_seen()
    if last_seen is None:
        print("No hay ningún Pokémon a la vista. Abre una terminal nueva primero.")
        return 1
    name = _display_name(last_seen["species"], last_seen["form"])
    if last_seen["shiny"]:
        name += " ✨shiny✨"
    estado = "ya capturado" if last_seen["captured"] else "sin capturar"
    print(f"{name} — {estado}")
    return 0


def _print_activity_rewards(result: inventory.SyncResult) -> None:
    for reward in result.rewards:
        ball = inventory.BALLS[reward.slug]
        reason = "por el paso del tiempo" if reward.source == "tiempo" else "por tus commits"
        console.print(f"[green]+{reward.count} {ball.name}[/] {reason}.")


def _choose_ball(args: argparse.Namespace, current_inventory: dict) -> inventory.Ball:
    if args.bola:
        ball = inventory.resolve_ball(args.bola)
        if ball is None:
            valid = ", ".join(inventory.BALLS)
            raise ValueError(f"Pokeball desconocida: {args.bola}. Opciones: {valid}.")
        if not ball.unlimited and inventory.stock_count(current_inventory, ball.slug) == 0:
            raise ValueError(f"No te queda ninguna {ball.name}.")
        return ball

    available = [inventory.BALLS["pokeball"]]
    available.extend(
        ball for slug, ball in inventory.BALLS.items()
        if slug != "pokeball" and (inventory.stock_count(current_inventory, slug) or 0) > 0
    )
    if len(available) == 1:
        return available[0]

    console.print("¿Qué Pokeball quieres lanzar?")
    for number, ball in enumerate(available, 1):
        if ball.unlimited:
            stock = "∞"
        else:
            stock = str(inventory.stock_count(current_inventory, ball.slug))
        effect = "captura garantizada" if ball.guaranteed else f"{ball.multiplier:g}×"
        console.print(f"  [{number}] {ball.name:<11} ×{stock:<2}  ({effect})")

    while True:
        try:
            answer = input(f"Elige [1-{len(available)}] o Enter para Pokeball: ").strip()
        except EOFError:
            answer = ""
        if not answer:
            return available[0]
        if answer.isdigit() and 1 <= int(answer) <= len(available):
            return available[int(answer) - 1]
        print("Opción no válida.")


def cmd_capturar(args: argparse.Namespace) -> int:
    last_seen = paths.read_last_seen()
    if last_seen is None:
        print("No hay ningún Pokémon a la vista. Abre una terminal nueva primero.")
        return 1
    if last_seen["captured"]:
        print("Ya capturaste a este Pokémon. Espera a que aparezca otro (abre otra terminal).")
        return 0

    species, form, shiny = last_seen["species"], last_seen["form"], last_seen["shiny"]

    activity, training = _sync_training()
    _print_activity_rewards(activity)
    _print_training(training)
    try:
        ball = _choose_ball(args, activity.inventory)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        return 2

    conn = storage.get_connection()
    cache = storage.get_species_cache(conn, species, form)
    if cache is None:
        data = pokeapi.fetch_species_data(species, form)
        if data is not None:
            storage.upsert_species_cache(conn, species, form, data, _now_iso())
        cache = storage.get_species_cache(conn, species, form)

    # La bola se gasta al lanzarla, acierte o no. La normal es infinita.
    try:
        inventory.consume_ball(ball.slug)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        return 2

    # La captura no está garantizada: capture_rate real × multiplicador de bola.
    chance = capture.catch_chance(
        cache["capture_rate"] if cache is not None else None,
        is_legendary=bool(cache["is_legendary"]) if cache is not None else False,
        is_mythical=bool(cache["is_mythical"]) if cache is not None else False,
        shiny=shiny,
        ball_multiplier=ball.multiplier,
    )
    if args.debug:
        chance_label = "garantizada" if ball.guaranteed else f"{chance:.1%}"
        console.print(f"[dim]{ball.name} · probabilidad de captura: {chance_label}[/]")
    caught = capture.roll_capture(chance)

    name = _display_name(species, form)
    if shiny:
        name += " ✨shiny✨"

    if caught:
        capture_id = storage.insert_capture(
            conn, species, form, shiny, _now_iso(), ball.slug,
            experience=progression.experience_for_level(
                (
                    cache.get("growth_rate", "medium")
                    if isinstance(cache, dict)
                    else (cache["growth_rate"] or "medium") if cache is not None
                    else "medium"
                ),
                progression.STARTING_LEVEL,
            ),
        )
        paths.mark_last_seen_captured()
        fled = False
        attempts = escape_after = 0
    else:
        escape_after = capture.escape_after_attempts(
            cache["capture_rate"] if cache is not None else None,
            speed=cache["spe"] if cache is not None else None,
            is_legendary=bool(cache["is_legendary"]) if cache is not None else False,
            is_mythical=bool(cache["is_mythical"]) if cache is not None else False,
            shiny=shiny,
        )
        attempts, escape_after = paths.record_last_seen_failed_capture(escape_after)
        fled = attempts >= escape_after
        if fled:
            paths.clear_last_seen()

    animation.play_capture_animation(
        console, species, form, shiny, caught, ball_slug=ball.slug
    )

    if caught:
        pokedex_id = cache["pokedex_id"] if cache is not None else None
        dex = f"N.º Pokédex #{pokedex_id:03d}" if pokedex_id else "N.º Pokédex #???"
        console.print(f"¡Genial! Capturaste a [bold]{name}[/] — {dex} · captura #{capture_id}")
        if cache is None:
            print("(sin datos de tipos/stats: sin conexión por ahora)")
    else:
        breakout = capture.breakout_message()
        if fled:
            console.print(
                f"[yellow]{breakout}[/] "
                "¡El Pokémon salvaje ha huido! "
                "Ya no hay ningún Pokémon a la vista."
            )
        else:
            console.print(
                f"[yellow]{breakout}[/] "
            )
    return 0


def cmd_bolsas(args: argparse.Namespace) -> int:
    result, training = _sync_training(force_repo_scan=True)
    _print_activity_rewards(result)
    _print_training(training)

    table = Table(title="Bolsa", box=None, pad_edge=False)
    table.add_column("Pokeball", style="bold")
    table.add_column("Stock", justify="right")
    table.add_column("Siguiente", justify="right")
    for ball in inventory.BALLS.values():
        count = "∞" if ball.unlimited else str(inventory.stock_count(result.inventory, ball.slug))
        missing = inventory.commits_until_next(result.inventory, ball.slug)
        if missing is None:
            next_reward = "siempre disponible"
        else:
            unit = "commit" if missing == 1 else "commits"
            next_reward = f"faltan {missing} {unit}"
        table.add_row(ball.name, count, next_reward)
    console.print(table)

    if args.info:
        details = Table(title="Información", box=None, pad_edge=False)
        details.add_column("Pokeball", style="bold")
        details.add_column("Efecto")
        details.add_column("Máximo", justify="right")
        for ball in inventory.BALLS.values():
            effect = "captura garantizada" if ball.guaranteed else f"captura ×{ball.multiplier:g}"
            maximum = "∞" if ball.unlimited else str(ball.max_stock)
            details.add_row(ball.name, effect, maximum)
        console.print()
        console.print(details)

        work_commits = result.inventory["activity"]["work_commits"]
        console.print(
            f"\n[bold]Actividad[/]\n"
            f"{work_commits} commits laborales registrados en {result.repositories} repos Git.\n"
            "Se cuentan commits propios de lunes a viernes, de 08:00 a 19:00.\n"
            "Además, el taller repone 1 Superball cada 24 horas."
        )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    conn = storage.get_connection()
    rows = [_row_with_cache(conn, r) for r in storage.list_captures(conn)]
    display.render_list_table(console, rows)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    conn = storage.get_connection()
    species, form = args.nombre, args.form
    cache = storage.get_species_cache(conn, species, form)
    if cache is None:
        data = pokeapi.fetch_species_data(species, form)
        if data is not None:
            storage.upsert_species_cache(conn, species, form, data, _now_iso())
            cache = storage.get_species_cache(conn, species, form)
    display.render_search_card(console, species, form, cache)
    return 0


def _resolve_capture(conn, ident: str, form: str | None):
    """Encuentra una captura por id numérico o por nombre de especie.
    Si hay varias del mismo nombre, devuelve la más reciente (list_captures
    ya viene ordenada por caught_at DESC)."""
    if ident.isdigit():
        return storage.get_capture(conn, int(ident))
    species = ident.lower()
    for row in storage.list_captures(conn):
        if row["species"] == species and (form is None or row["form"] == form):
            return row
    return None


def _read_menu_key() -> str:
    """Lee una tecla sin requerir Enter y normaliza flechas/vi/escape."""
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        key = os.read(fd, 1).decode(errors="ignore")
        if key == "\x1b":
            # Una flecha llega como ESC + "[" + letra. Los tres bytes pueden
            # atravesar el PTY en paquetes distintos, por eso damos a cada uno
            # un margen breve antes de tratar ESC como cancelacion aislada.
            for _ in range(2):
                if not select.select([sys.stdin], [], [], 0.1)[0]:
                    break
                key += os.read(fd, 1).decode(errors="ignore")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
    return {
        "\x1b[A": "up", "k": "up", "K": "up",
        "\x1b[B": "down", "j": "down", "J": "down",
        "\r": "enter", "\n": "enter",
        "q": "cancel", "Q": "cancel", "\x1b": "cancel",
    }.get(key, "other")


def _team_picker_table(rows: list[dict], selected: int, window_size: int = 10) -> Table:
    start = max(0, min(selected - window_size // 2, len(rows) - window_size))
    visible = rows[start:start + window_size]
    table = Table(
        title="Elige un Pokémon · ↑/↓ mover · Enter añadir · q cancelar",
        box=None,
    )
    table.add_column("", width=2)
    table.add_column("ID", justify="right")
    table.add_column("Pokémon")
    table.add_column("Nivel", justify="right")
    table.add_column("Tipos")
    for offset, row in enumerate(visible):
        index = start + offset
        style = "bold reverse cyan" if index == selected else None
        name = _display_name(row["species"], row["form"])
        if row["shiny"]:
            name += " ✨"
        table.add_row(
            "▶" if index == selected else "",
            str(row["id"]),
            name,
            str(row["level"]),
            display.type_badges(row["types"]) if row["types"] else "?",
            style=style,
        )
    if len(rows) > window_size:
        table.caption = f"{selected + 1}/{len(rows)}"
    return table


def _select_capture_for_team(conn) -> int | None:
    rows = [
        _row_with_cache(conn, row)
        for row in storage.list_captures(conn)
        if not row["in_team"]
    ]
    if not rows:
        console.print("No tienes más Pokémon fuera del equipo para añadir.")
        return None

    # Si stdin no es interactivo (script/pipe), conserva una alternativa que
    # no depende de secuencias ANSI ni modo raw.
    if not sys.stdin.isatty():
        display.render_list_table(console, rows)
        try:
            answer = input("ID para añadir (vacío cancela): ").strip()
        except EOFError:
            return None
        valid = {str(row["id"]): row["id"] for row in rows}
        return valid.get(answer)

    selected = 0
    with Live(
        _team_picker_table(rows, selected), console=console,
        auto_refresh=False, transient=True,
    ) as live:
        while True:
            key = _read_menu_key()
            if key == "up":
                selected = (selected - 1) % len(rows)
            elif key == "down":
                selected = (selected + 1) % len(rows)
            elif key == "enter":
                return int(rows[selected]["id"])
            elif key == "cancel":
                return None
            live.update(_team_picker_table(rows, selected), refresh=True)


def cmd_vision(args: argparse.Namespace) -> int:
    """Vista enriquecida de un Pokémon que YA has capturado: sprite de krabby
    + ficha completa de PokeAPI."""
    conn = storage.get_connection()
    cap = _resolve_capture(conn, args.pokemon, args.form)
    if cap is None:
        console.print(
            f"No tienes ninguna captura que encaje con [bold]{args.pokemon}[/]. "
            "Mira `pokedex list` para ver tus capturas "
            "(o usa `pokedex search` para consultar sin capturar)."
        )
        return 1

    row = _row_with_cache(conn, cap)
    # Si se capturó sin conexión, intenta enriquecer ahora.
    if row["types"] is None:
        data = pokeapi.fetch_species_data(row["species"], row["form"])
        if data is not None:
            storage.upsert_species_cache(conn, row["species"], row["form"], data, _now_iso())
            row = _row_with_cache(conn, cap)

    sprite = krabby_bridge.capture_sprite(row["species"], row["form"], bool(row["shiny"]))
    display.render_vision_card(console, row, sprite)
    return 0


def cmd_equipo(args: argparse.Namespace) -> int:
    conn = storage.get_connection()
    if args.accion == "remove" and args.id is None:
        print("Uso: pokedex equipo remove <id>")
        return 1
    if args.accion == "add":
        if storage.count_team(conn) >= 6:
            print("Tu equipo ya tiene 6 Pokémon. Quita uno con `pokedex equipo remove <id>`.")
            return 1
        capture_id = args.id if args.id is not None else _select_capture_for_team(conn)
        if capture_id is None:
            return 0
        selected = storage.get_capture(conn, capture_id)
        if selected is None:
            print(f"No existe ninguna captura con id {capture_id}.")
            return 1
        if selected["in_team"]:
            print(f"La captura #{capture_id} ya está en el equipo.")
            return 0
        storage.set_team(conn, capture_id, True)
        print(f"Añadido al equipo (#{capture_id}).")
        return 0
    if args.accion == "remove":
        storage.set_team(conn, args.id, False)
        print(f"Quitado del equipo (#{args.id}).")
        return 0
    rows = [_row_with_cache(conn, r) for r in storage.list_captures(conn) if r["in_team"]]
    display.render_team_panel(console, rows)
    return 0


def cmd_tipos(args: argparse.Namespace) -> int:
    conn = storage.get_connection()
    counts: dict[str, int] = {}
    for r in storage.list_captures(conn):
        row = _row_with_cache(conn, r)
        for t in row["types"] or []:
            counts[t] = counts.get(t, 0) + 1
    display.render_tipos_breakdown(console, counts)
    return 0


def cmd_ranking(args: argparse.Namespace) -> int:
    conn = storage.get_connection()
    ranked, missing = [], 0
    for r in storage.list_captures(conn):
        row = _row_with_cache(conn, r)
        if row["types"] is None:
            missing += 1
            continue
        row["total"] = row["hp"] + row["atk"] + row["def"] + row["spa"] + row["spd"] + row["spe"]
        ranked.append(row)
    ranked.sort(key=lambda r: -r["total"])
    display.render_ranking_table(console, ranked, missing)
    return 0


def cmd_legendarios(args: argparse.Namespace) -> int:
    conn = storage.get_connection()
    rows = []
    for r in storage.list_captures(conn):
        row = _row_with_cache(conn, r)
        if row["is_legendary"] or row["is_mythical"]:
            rows.append(row)
    display.render_legendarios_panel(console, rows)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Prueba la animación de captura sin tocar la base de datos ni last_seen."""
    if args.nombre:
        species, form, shiny = args.nombre, args.form, args.shiny
    elif args.legendary:
        species, form, shiny = capture.random_legendary(), args.form, args.shiny
    else:
        species, form, shiny = krabby_bridge.pick_species_form_shiny(args.generations)
        if args.shiny:
            shiny = True

    ball = inventory.resolve_ball(args.bola) or inventory.BALLS["pokeball"]

    if args.result == "catch":
        caught = True
    elif args.result == "escape":
        caught = False
    else:
        caught = True if ball.guaranteed else capture.roll_capture(0.6)

    name = _display_name(species, form)
    if shiny:
        name += " ✨shiny✨"
    console.print(
        f"[dim]· demo ·[/] lanzando {ball.name} a [bold]{name}[/] "
        f"→ resultado: {'captura' if caught else 'fuga'} "
        "[dim](no se guarda nada)[/]"
    )
    animation.play_capture_animation(
        console, species, form, shiny, caught, ball_slug=ball.slug
    )
    if not caught:
        console.print("[yellow]¡Se soltó![/] (era solo una demo)")
    return 0


def cmd_demo_evolucion(args: argparse.Namespace) -> int:
    """Prueba visual segura: no toca capturas, experiencia ni encuentros."""
    animation.play_evolution_animation(
        console, args.origen, args.form_origen, args.destino, args.form_destino,
        args.shiny, speed=args.speed,
    )
    return 0


def cmd_completion(args: argparse.Namespace) -> int:
    """Imprime el script de autocompletado para el shell indicado."""
    completion_file = paths.PROJECT_DIR / "completions" / f"_pokedex.{args.shell}"
    try:
        sys.stdout.write(completion_file.read_text())
    except FileNotFoundError:
        print(f"No hay autocompletado disponible para '{args.shell}'.", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pokedex",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Pokédex de terminal.\n\n"
            "Cada terminal nueva pinta un Pokémon al azar sin decir cuál es. Si te\n"
            "interesa, captúralo (¡no siempre sale a la primera!) y quedará guardado\n"
            "en tu Pokédex local, enriquecido con tipos, stats y datos de PokeAPI."
        ),
        epilog=(
            "Ejemplos:\n"
            "  pokedex ver                     ¿qué Pokémon está esperando?\n"
            "  pokedex capturar --bola ultra  intenta capturarlo con una Ultraball\n"
            "  pokedex bolsas                  consulta y repone tus Pokeballs especiales\n"
            "  pokedex list                    tus capturas\n"
            "  pokedex search charizard -f mega-x     ficha de una forma concreta\n"
            "  pokedex equipo add 3            mete la captura #3 en tu equipo\n"
            "  pokedex demo                    prueba la animación sin capturar\n"
            "  pokedex demo -L                 pruébala contra un legendario al azar\n"
            "  pokedex demo-evolucion bulbasaur ivysaur  prueba una evolución\n"
            "\nAutocompletado zsh:  pokedex completion zsh > ~/.zfunc/_pokedex"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<comando>", required=True)

    hook_parser = subparsers.add_parser(
        "hook", help="(interno) pinta un Pokémon y recuerda cuál fue",
        description="Uso interno desde ~/.zshrc: pinta un Pokémon al azar y guarda "
        "en silencio cuál fue, para poder capturarlo luego.",
    )
    hook_parser.add_argument(
        "generations", nargs="?", default="1-9", metavar="GENS",
        help="generaciones a incluir, p.ej. '1-3' o '1,3,6' (por defecto 1-9)",
    )
    hook_parser.set_defaults(func=cmd_hook)

    ver_parser = subparsers.add_parser(
        "ver", help="muestra qué Pokémon está esperando (sin capturarlo)",
        description="Muestra qué Pokémon está esperando ahora mismo y si ya lo capturaste.",
    )
    ver_parser.set_defaults(func=cmd_ver)

    capturar_parser = subparsers.add_parser(
        "capturar", help="intenta capturar el Pokémon que está esperando",
        description="Elige una Pokeball y lánzala al Pokémon que está esperando. La "
        "Pokeball normal es infinita; las especiales aumentan la probabilidad y se "
        "reponen con tiempo y actividad Git. Si se suelta, puedes reintentarlo.",
    )
    capturar_parser.add_argument(
        "-b", "--bola", metavar="BOLA",
        help="selección directa: poke, super, ultra o master (sin menú)",
    )
    capturar_parser.add_argument(
        "--debug", action="store_true",
        help="muestra la probabilidad exacta de captura",
    )
    capturar_parser.set_defaults(func=cmd_capturar)

    bolsas_parser = subparsers.add_parser(
        "bolsas", help="muestra y actualiza tus Pokeballs",
        description="Muestra el stock. La Pokeball normal es infinita; las especiales "
        "se reponen con tiempo y commits propios en horario laboral.",
    )
    bolsas_parser.add_argument(
        "--info", action="store_true",
        help="muestra efectividad, límites y detalles de la reposición",
    )
    bolsas_parser.set_defaults(func=cmd_bolsas)

    list_parser = subparsers.add_parser(
        "list", help="lista tus capturas",
        description="Lista todas tus capturas con su N.º de Pokédex y orden de captura.",
    )
    list_parser.set_defaults(func=cmd_list)

    search_parser = subparsers.add_parser(
        "search", help="ficha de cualquier Pokémon o forma",
        description="Muestra la ficha (tipos, stats, descripción) de cualquier Pokémon, "
        "aunque no lo hayas capturado.",
    )
    search_parser.add_argument("nombre", help="nombre del Pokémon (como en `krabby list`)")
    search_parser.add_argument(
        "-f", "--form", default="regular", metavar="FORMA",
        help="forma alternativa, p.ej. mega-x, gmax, alola (por defecto: regular)",
    )
    search_parser.set_defaults(func=cmd_search)

    vision_parser = subparsers.add_parser(
        "vision", help="vista enriquecida de un Pokémon capturado (sprite + ficha)",
        description="Muestra en grande el sprite de uno de tus Pokémon capturados junto "
        "a su ficha completa de PokeAPI: tipos, barras de stats, N.º de Pokédex, "
        "rareza, descripción y datos de la captura.",
    )
    vision_parser.add_argument(
        "pokemon", help="id de captura (como en `pokedex list`) o nombre de un Pokémon capturado",
    )
    vision_parser.add_argument(
        "-f", "--form", default=None, metavar="FORMA",
        help="desambigua la forma cuando buscas por nombre (p.ej. mega-x, alola)",
    )
    vision_parser.set_defaults(func=cmd_vision)

    equipo_parser = subparsers.add_parser(
        "equipo", help="ve o gestiona tu equipo (máx. 6)",
        description="Sin argumentos muestra tu equipo. `add` abre un selector; "
        "add/remove <id> permite gestionarlo directamente.",
    )
    equipo_parser.add_argument(
        "accion", nargs="?", choices=["add", "remove"], metavar="{add,remove}",
        help="añade o quita una captura del equipo",
    )
    equipo_parser.add_argument(
        "id", nargs="?", type=int,
        help="id de captura; si se omite en `add`, abre el selector interactivo",
    )
    equipo_parser.set_defaults(func=cmd_equipo)

    tipos_parser = subparsers.add_parser(
        "tipos", help="desglose de tus capturas por tipo",
        description="Cuenta tus capturas por tipo elemental.",
    )
    tipos_parser.set_defaults(func=cmd_tipos)

    ranking_parser = subparsers.add_parser(
        "ranking", help="ranking por suma de stats base",
        description="Ordena tus capturas por la suma de sus stats base, con medallas.",
    )
    ranking_parser.set_defaults(func=cmd_ranking)

    legendarios_parser = subparsers.add_parser(
        "legendarios", help="tu salón de la fama de legendarios y singulares",
        description="Muestra los legendarios y singulares que has capturado.",
    )
    legendarios_parser.set_defaults(func=cmd_legendarios)

    demo_parser = subparsers.add_parser(
        "demo", help="prueba la animación de captura sin guardar nada",
        description="Reproduce la animación de captura contra un Pokémon (al azar o el "
        "que indiques) SIN tocar la base de datos ni el Pokémon que espera. Ideal "
        "para probar la animación.",
    )
    demo_parser.add_argument(
        "nombre", nargs="?", help="Pokémon concreto (por defecto: uno al azar)",
    )
    demo_parser.add_argument(
        "-L", "--legendary", action="store_true",
        help="prueba contra un legendario/singular al azar",
    )
    demo_parser.add_argument("-f", "--form", default="regular", metavar="FORMA", help="forma alternativa")
    demo_parser.add_argument("-s", "--shiny", action="store_true", help="fuerza variante shiny")
    demo_parser.add_argument(
        "-g", "--generations", default="1-9", metavar="GENS",
        help="generaciones para el azar (por defecto 1-9)",
    )
    demo_parser.add_argument(
        "-r", "--result", choices=["random", "catch", "escape"], default="random",
        help="fuerza el resultado (por defecto: al azar)",
    )
    demo_parser.add_argument(
        "-b", "--bola", default="poke", choices=["poke", "super", "ultra", "master"],
        help="Pokeball cuya animación quieres probar (por defecto: poke)",
    )
    demo_parser.set_defaults(func=cmd_demo)

    evolution_demo = subparsers.add_parser(
        "demo-evolucion", help="prueba la animación de evolución sin guardar nada",
        description="Hace evolucionar visualmente dos especies sin tocar tu partida.",
    )
    evolution_demo.add_argument("origen", nargs="?", default="bulbasaur")
    evolution_demo.add_argument("destino", nargs="?", default="ivysaur")
    evolution_demo.add_argument("--form-origen", default="regular", metavar="FORMA")
    evolution_demo.add_argument("--form-destino", default="regular", metavar="FORMA")
    evolution_demo.add_argument("-s", "--shiny", action="store_true")
    evolution_demo.add_argument(
        "--speed", type=float, default=1.0, metavar="FACTOR",
        help="factor de duración: 0.7 más rápida, 1.4 más pausada",
    )
    evolution_demo.set_defaults(func=cmd_demo_evolucion)

    completion_parser = subparsers.add_parser(
        "completion", help="imprime el script de autocompletado del shell",
        description="Imprime por stdout el script de autocompletado. Para zsh:\n"
        "  pokedex completion zsh > ~/.zfunc/_pokedex\n"
        "y asegúrate de tener ~/.zfunc en tu fpath antes de compinit.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    completion_parser.add_argument("shell", choices=["zsh"], help="shell objetivo")
    completion_parser.set_defaults(func=cmd_completion)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
