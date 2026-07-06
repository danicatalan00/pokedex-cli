import argparse
import sys
from datetime import datetime, timezone

from rich.console import Console

import json

from pokedex_cli import animation, capture, display, krabby_bridge, paths, pokeapi, storage

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
    else:
        row["types"] = None
        row["is_legendary"] = 0
        row["is_mythical"] = 0
        row["pokedex_id"] = None
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


def cmd_capturar(args: argparse.Namespace) -> int:
    last_seen = paths.read_last_seen()
    if last_seen is None:
        print("No hay ningún Pokémon a la vista. Abre una terminal nueva primero.")
        return 1
    if last_seen["captured"]:
        print("Ya capturaste a este Pokémon. Espera a que aparezca otro (abre otra terminal).")
        return 0

    species, form, shiny = last_seen["species"], last_seen["form"], last_seen["shiny"]

    conn = storage.get_connection()
    cache = storage.get_species_cache(conn, species, form)
    if cache is None:
        data = pokeapi.fetch_species_data(species, form)
        if data is not None:
            storage.upsert_species_cache(conn, species, form, data, _now_iso())
        cache = storage.get_species_cache(conn, species, form)

    # La captura no está garantizada: se tira el dado según el capture_rate real.
    chance = capture.catch_chance(
        cache["capture_rate"] if cache is not None else None,
        is_legendary=bool(cache["is_legendary"]) if cache is not None else False,
        is_mythical=bool(cache["is_mythical"]) if cache is not None else False,
        shiny=shiny,
    )
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
        if fled:
            console.print(
                f"[yellow]¡Oh no![/] El Pokémon se soltó de la Pokébola "
                f"([dim]{round(chance * 100)}% de captura[/]) y huyó entre la hierba. "
                "Ya no hay ningún Pokémon a la vista."
            )
        else:
            console.print(
                f"[yellow]¡Oh no![/] El Pokémon se soltó de la Pokébola "
                f"([dim]{round(chance * 100)}% de captura[/]). "
                "Sigue esperando: prueba otra vez con `pokedex capturar`."
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
            "  pokedex capturar                intenta capturarlo (con suerte)\n"
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
        description="Lanza una Pokébola al Pokémon que está esperando. La captura NO "
        "está garantizada: se tira el dado según su rareza (capture_rate). Si se "
        "suelta, sigue esperando y puedes reintentarlo.",
    )
    capturar_parser.set_defaults(func=cmd_capturar)

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
