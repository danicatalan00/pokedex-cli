import argparse
import sys
from datetime import datetime, timezone

from rich.console import Console

import json

from pokedex_cli import animation, display, krabby_bridge, paths, pokeapi, storage

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
    else:
        row["types"] = None
        row["is_legendary"] = 0
        row["is_mythical"] = 0
    return row


def cmd_hook(args: argparse.Namespace) -> int:
    def write_last_seen(species: str, form: str, shiny: bool) -> None:
        paths.write_last_seen(species, form, shiny, _now_iso())

    krabby_bridge.run_hook(args.generations, write_last_seen)
    return 0


def cmd_ver(args: argparse.Namespace) -> int:
    last_seen = paths.read_last_seen()
    if last_seen is None:
        print("No hay ningún Pokémon esperando. Abre una terminal nueva primero.")
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
        print("No hay ningún Pokémon esperando. Abre una terminal nueva primero.")
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

    capture_id = storage.insert_capture(conn, species, form, shiny, _now_iso())
    paths.mark_last_seen_captured()

    animation.play_capture_animation(console, species, form, shiny)

    name = _display_name(species, form)
    if shiny:
        name += " ✨shiny✨"
    print(f"¡Genial! Capturaste a {name} (#{capture_id})")
    if cache is None and storage.get_species_cache(conn, species, form) is None:
        print("(sin datos de tipos/stats: sin conexión por ahora)")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pokedex")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hook_parser = subparsers.add_parser(
        "hook", help="Uso interno desde .zshrc: pinta un pokemon y recuerda cual fue"
    )
    hook_parser.add_argument("generations", nargs="?", default="1-9")
    hook_parser.set_defaults(func=cmd_hook)

    ver_parser = subparsers.add_parser("ver", help="Muestra qué Pokémon está esperando")
    ver_parser.set_defaults(func=cmd_ver)

    capturar_parser = subparsers.add_parser("capturar", help="Captura el Pokémon que está esperando")
    capturar_parser.set_defaults(func=cmd_capturar)

    list_parser = subparsers.add_parser("list", help="Lista tus capturas")
    list_parser.set_defaults(func=cmd_list)

    search_parser = subparsers.add_parser("search", help="Busca info de cualquier Pokémon")
    search_parser.add_argument("nombre")
    search_parser.add_argument("-f", "--form", default="regular")
    search_parser.set_defaults(func=cmd_search)

    equipo_parser = subparsers.add_parser("equipo", help="Ve o gestiona tu equipo (máx. 6)")
    equipo_parser.add_argument("accion", nargs="?", choices=["add", "remove"])
    equipo_parser.add_argument("id", nargs="?", type=int)
    equipo_parser.set_defaults(func=cmd_equipo)

    tipos_parser = subparsers.add_parser("tipos", help="Desglose de tus capturas por tipo")
    tipos_parser.set_defaults(func=cmd_tipos)

    ranking_parser = subparsers.add_parser("ranking", help="Ranking por suma de stats base")
    ranking_parser.set_defaults(func=cmd_ranking)

    legendarios_parser = subparsers.add_parser("legendarios", help="Tus legendarios y singulares")
    legendarios_parser.set_defaults(func=cmd_legendarios)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
