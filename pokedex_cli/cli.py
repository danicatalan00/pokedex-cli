import argparse
import sys
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

import json

from pokedex_cli import animation, capture, display, inventory, krabby_bridge, paths, pokeapi, storage

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
    else:
        row["types"] = None
        row["is_legendary"] = 0
        row["is_mythical"] = 0
        row["pokedex_id"] = None
        row["generation"] = None
        row["flavor_text"] = None
        row["capture_rate"] = None
        row["form_data_exact"] = 1
    return row


def cmd_hook(args: argparse.Namespace) -> int:
    def write_last_seen(species: str, form: str, shiny: bool) -> None:
        paths.write_last_seen(species, form, shiny, _now_iso())

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
            raise ValueError(f"Pokébola desconocida: {args.bola}. Opciones: {valid}.")
        if not ball.unlimited and inventory.stock_count(current_inventory, ball.slug) == 0:
            raise ValueError(f"No te queda ninguna {ball.name}.")
        return ball

    available = [inventory.BALLS["pokebola"]]
    available.extend(
        ball for slug, ball in inventory.BALLS.items()
        if slug != "pokebola" and (inventory.stock_count(current_inventory, slug) or 0) > 0
    )
    if len(available) == 1:
        return available[0]

    console.print("¿Qué Pokébola quieres lanzar?")
    for number, ball in enumerate(available, 1):
        if ball.unlimited:
            stock = "∞"
        else:
            stock = str(inventory.stock_count(current_inventory, ball.slug))
        effect = "captura garantizada" if ball.guaranteed else f"{ball.multiplier:g}×"
        console.print(f"  [{number}] {ball.name:<11} ×{stock:<2}  ({effect})")

    while True:
        try:
            answer = input(f"Elige [1-{len(available)}] o Enter para Pokébola: ").strip()
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

    activity = inventory.sync_activity()
    _print_activity_rewards(activity)
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
        capture_id = storage.insert_capture(conn, species, form, shiny, _now_iso())
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

    animation.play_capture_animation(console, species, form, shiny, caught)

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
    result = inventory.sync_activity(force_repo_scan=True)
    _print_activity_rewards(result)

    table = Table(title="Bolsa", box=None, pad_edge=False)
    table.add_column("Pokébola", style="bold")
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
        details.add_column("Pokébola", style="bold")
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
            "Además, el taller repone 1 Superbola cada 24 horas."
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
    if args.accion in ("add", "remove") and args.id is None:
        print(f"Uso: pokedex equipo {args.accion} <id>")
        return 1
    if args.accion == "add":
        if storage.count_team(conn) >= 6:
            print("Tu equipo ya tiene 6 Pokémon. Quita uno con `pokedex equipo remove <id>`.")
            return 1
        if storage.get_capture(conn, args.id) is None:
            print(f"No existe ninguna captura con id {args.id}.")
            return 1
        storage.set_team(conn, args.id, True)
        print(f"Añadido al equipo (#{args.id}).")
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

    if args.result == "catch":
        caught = True
    elif args.result == "escape":
        caught = False
    else:
        caught = capture.roll_capture(0.6)

    name = _display_name(species, form)
    if shiny:
        name += " ✨shiny✨"
    console.print(
        f"[dim]· demo ·[/] lanzando Pokébola a [bold]{name}[/] "
        f"→ resultado: {'captura' if caught else 'fuga'} "
        "[dim](no se guarda nada)[/]"
    )
    animation.play_capture_animation(console, species, form, shiny, caught)
    if not caught:
        console.print("[yellow]¡Se soltó![/] (era solo una demo)")
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
            "  pokedex capturar --bola ultra  intenta capturarlo con una Ultrabola\n"
            "  pokedex bolsas                  consulta y repone tus bolas especiales\n"
            "  pokedex list                    tus capturas\n"
            "  pokedex search charizard -f mega-x     ficha de una forma concreta\n"
            "  pokedex equipo add 3            mete la captura #3 en tu equipo\n"
            "  pokedex demo                    prueba la animación sin capturar\n"
            "  pokedex demo -L                 pruébala contra un legendario al azar\n"
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
        description="Elige una Pokébola y lánzala al Pokémon que está esperando. La "
        "Pokébola normal es infinita; las especiales aumentan la probabilidad y se "
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
        "bolsas", help="muestra y actualiza tus Pokébolas",
        description="Muestra el stock. La Pokébola normal es infinita; las especiales "
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
        description="Sin argumentos muestra tu equipo. Con add/remove <id> lo gestiona.",
    )
    equipo_parser.add_argument(
        "accion", nargs="?", choices=["add", "remove"], metavar="{add,remove}",
        help="añade o quita una captura del equipo",
    )
    equipo_parser.add_argument(
        "id", nargs="?", type=int, help="id de captura (el que sale en `pokedex list`)",
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
    demo_parser.set_defaults(func=cmd_demo)

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
