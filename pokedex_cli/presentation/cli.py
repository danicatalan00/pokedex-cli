import argparse
import json
import os
import select
import sys
import termios
import tty
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.table import Table

from pokedex_cli import (
    animation,
    capture,
    composition,
    display,
    progression,
)
from pokedex_cli.application import activity as activity_application
from pokedex_cli.application import capture as capture_application
from pokedex_cli.application import collection as collection_application
from pokedex_cli.application import evolutions as evolution_application
from pokedex_cli.application import hook as hook_application
from pokedex_cli.application import species as species_application
from pokedex_cli.application import team as team_application
from pokedex_cli.domain.identity import normalize_species
from pokedex_cli.domain.models import Ball

console = Console()
_display_name = display.display_name


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    return composition.sync_training(force_repo_scan=force_repo_scan)


def cmd_hook(args: argparse.Namespace) -> int:
    def write_last_seen(species: str, form: str, shiny: bool) -> None:
        composition.write_encounter(species, form, shiny, _now_iso())

    result = _open_terminal_use_case(write_last_seen).execute(args.generations)
    _print_training(result.training)
    if result.evolutions:
        species_data = _species_data_use_case()
        for pokemon in result.evolutions:
            target_species = pokemon.target_species
            target_form = pokemon.target_form
            species_data.execute(target_species, target_form)
            animation.play_evolution_animation(
                console,
                pokemon.species,
                pokemon.form,
                target_species,
                target_form,
                pokemon.shiny,
                sprite_renderer=_sprite_renderer(),
            )
        composition.clear_encounter()
        return 0
    return 0


def cmd_ver(args: argparse.Namespace) -> int:
    last_seen = composition.read_encounter()
    if last_seen is None:
        print("No hay ningún Pokémon a la vista. Abre una terminal nueva primero.")
        return 1
    name = _display_name(last_seen["species"], last_seen["form"])
    if last_seen["shiny"]:
        name += " ✨shiny✨"
    estado = "ya capturado" if last_seen["captured"] else "sin capturar"
    print(f"{name} — {estado}")
    return 0


def _print_activity_rewards(result: activity_application.SyncActivityResult) -> None:
    for reward in result.rewards:
        ball = composition.ball_catalog()[reward.slug]
        reason = "por el paso del tiempo" if reward.source == "tiempo" else "por tus commits"
        console.print(f"[green]+{reward.count} {ball.name}[/] {reason}.")


def _choose_ball(args: argparse.Namespace, current_inventory: dict) -> Ball:
    if args.bola:
        ball = composition.resolve_ball(args.bola)
        if ball is None:
            valid = ", ".join(composition.ball_catalog())
            raise ValueError(f"Pokeball desconocida: {args.bola}. Opciones: {valid}.")
        if not ball.unlimited and composition.stock_count(current_inventory, ball.slug) == 0:
            raise ValueError(f"No te queda ninguna {ball.name}.")
        return ball

    catalog = composition.ball_catalog()
    available = [catalog["pokeball"]]
    available.extend(
        ball
        for slug, ball in catalog.items()
        if slug != "pokeball" and (composition.stock_count(current_inventory, slug) or 0) > 0
    )
    if len(available) == 1:
        return available[0]

    console.print("¿Qué Pokeball quieres lanzar?")
    for number, ball in enumerate(available, 1):
        if ball.unlimited:
            stock = "∞"
        else:
            stock = str(composition.stock_count(current_inventory, ball.slug))
        effect = "captura garantizada" if ball.guaranteed else f"{ball.multiplier:g}×"
        console.print(f"  [{number}] {ball.name:<11} ×{stock:<2}  ({effect})")

    while True:
        try:
            answer = input(f"Elige [1-{len(available)}] o Enter para Pokeball: ").strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if not answer:
            return available[0]
        if answer.isdigit() and 1 <= int(answer) <= len(available):
            return available[int(answer) - 1]
        print("Opción no válida.")


def cmd_capturar(args: argparse.Namespace) -> int:
    last_seen = composition.read_encounter()
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

    cache = _species_data_use_case().execute(species, form)

    result = _capture_encounter_use_case().execute(
        capture_application.CaptureCommand(
            ball_slug=ball.slug,
            ball_multiplier=ball.multiplier,
            caught_at=_now_iso(),
            capture_rate=cache["capture_rate"] if cache is not None else None,
            speed=cache["spe"] if cache is not None else None,
            is_legendary=bool(cache["is_legendary"]) if cache is not None else False,
            is_mythical=bool(cache["is_mythical"]) if cache is not None else False,
            growth_rate=_cache_value(cache, "growth_rate", "medium"),
            gender_rate=_cache_gender_rate(cache),
            abilities=_cache_abilities(cache),
        )
    )
    if result.status is capture_application.CaptureStatus.NO_STOCK:
        console.print(f"[red]No te queda ninguna {ball.name}.[/]")
        return 2
    if result.status is capture_application.CaptureStatus.NO_ENCOUNTER:
        print("No hay ningún Pokémon a la vista. Abre una terminal nueva primero.")
        return 1
    if result.status is capture_application.CaptureStatus.ALREADY_CAPTURED:
        print("Ya capturaste a este Pokémon. Espera a que aparezca otro (abre otra terminal).")
        return 0

    chance = result.chance
    if args.debug:
        chance_label = "garantizada" if ball.guaranteed else f"{chance:.1%}"
        console.print(f"[dim]{ball.name} · probabilidad de captura: {chance_label}[/]")
    caught = result.status is capture_application.CaptureStatus.CAUGHT

    name = _display_name(species, form)
    if shiny:
        name += " ✨shiny✨"

    if caught:
        capture_id = result.capture_id
        fled = False
    else:
        fled = result.status is capture_application.CaptureStatus.FLED

    animation.play_capture_animation(
        console,
        species,
        form,
        shiny,
        caught,
        ball_slug=ball.slug,
        sprite_renderer=_sprite_renderer(),
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
            console.print(f"[yellow]{breakout}[/] ")
    return 0


def _capture_encounter_use_case() -> capture_application.CaptureEncounter:
    return composition.capture_encounter()


def _cache_value(cache, key: str, default):
    if cache is None:
        return default
    if isinstance(cache, dict):
        return cache.get(key, default) or default
    return cache[key] if key in cache.keys() and cache[key] is not None else default


def _cache_field(cache, key: str):
    """Read a cache field verbatim: unlike ``_cache_value`` this does not
    collapse a valid falsy value (e.g. ``gender_rate == 0``, always male)."""
    if cache is None:
        return None
    if isinstance(cache, dict):
        return cache.get(key)
    return cache[key] if key in cache.keys() else None


def _cache_gender_rate(cache) -> int | None:
    value = _cache_field(cache, "gender_rate")
    return value if isinstance(value, int) else None


def _cache_abilities(cache) -> tuple[str, ...]:
    """Parse the cached abilities JSON, tolerant to a missing/malformed cache."""
    raw = _cache_field(cache, "abilities")
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed)


def cmd_bolsas(args: argparse.Namespace) -> int:
    result, training = _sync_training(force_repo_scan=True)
    _print_activity_rewards(result)
    _print_training(training)

    table = Table(title="Bolsa", box=None, pad_edge=False)
    table.add_column("Pokeball", style="bold")
    table.add_column("Stock", justify="right")
    table.add_column("Siguiente", justify="right")
    for ball in composition.ball_catalog().values():
        count = "∞" if ball.unlimited else str(composition.stock_count(result.inventory, ball.slug))
        missing = composition.commits_until_next(result.inventory, ball.slug)
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
        for ball in composition.ball_catalog().values():
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
    composition.backfill_individuality().execute()
    display.render_list_table(console, _collection_queries().captures())
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    species, form = args.nombre, args.form
    cache = _species_data_use_case().execute(species, form)
    display.render_search_card(console, species, form, cache)
    return 0


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
        "\x1b[A": "up",
        "k": "up",
        "K": "up",
        "\x1b[B": "down",
        "j": "down",
        "J": "down",
        "\r": "enter",
        "\n": "enter",
        "q": "cancel",
        "Q": "cancel",
        "\x1b": "cancel",
    }.get(key, "other")


def _team_picker_table(
    rows: list[dict],
    selected: int,
    action: team_application.TeamAction,
    window_size: int = 10,
) -> Table:
    start = max(0, min(selected - window_size // 2, len(rows) - window_size))
    visible = rows[start : start + window_size]
    verb = "añadir" if action is team_application.TeamAction.ADD else "quitar"
    table = Table(
        title=f"Elige un Pokémon · ↑/↓ mover · Enter {verb} · q cancelar",
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


def _select_capture_for_team(action: team_application.TeamAction, name: str | None) -> int | None:
    queries = _collection_queries()
    rows = (
        queries.available_for_team()
        if action is team_application.TeamAction.ADD
        else queries.team()
    )
    if name is not None:
        expected = normalize_species(name)
        rows = [
            row
            for row in rows
            if row["species"] == expected
            or normalize_species(_display_name(row["species"], row["form"])) == expected
            or normalize_species(f"{row['species']}-{row['form']}") == expected
        ]
    if not rows:
        if name is not None:
            scope = "fuera" if action is team_application.TeamAction.ADD else "dentro"
            console.print(f"No tienes ninguna captura de [bold]{name}[/] {scope} del equipo.")
        elif action is team_application.TeamAction.ADD:
            console.print("No tienes más Pokémon fuera del equipo para añadir.")
        else:
            console.print("Tu equipo está vacío.")
        return None
    if len(rows) == 1 and name is not None:
        return int(rows[0]["id"])

    # Si stdin no es interactivo (script/pipe), conserva una alternativa que
    # no depende de secuencias ANSI ni modo raw.
    if not sys.stdin.isatty():
        display.render_list_table(console, rows)
        verb = "añadir" if action is team_application.TeamAction.ADD else "quitar"
        try:
            answer = input(f"ID para {verb} (vacío cancela): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        valid = {str(row["id"]): row["id"] for row in rows}
        return valid.get(answer)

    selected = 0
    with Live(
        _team_picker_table(rows, selected, action),
        console=console,
        auto_refresh=False,
        transient=True,
    ) as live:
        while True:
            try:
                key = _read_menu_key()
            except KeyboardInterrupt:
                return None
            if key == "up":
                selected = (selected - 1) % len(rows)
            elif key == "down":
                selected = (selected + 1) % len(rows)
            elif key == "enter":
                return int(rows[selected]["id"])
            elif key == "cancel":
                return None
            live.update(_team_picker_table(rows, selected, action), refresh=True)


def cmd_vision(args: argparse.Namespace) -> int:
    """Vista enriquecida de un Pokémon que YA has capturado: sprite de krabby
    + ficha completa de PokeAPI."""
    composition.backfill_individuality().execute()
    queries = _collection_queries()
    row = queries.resolve(args.pokemon, args.form)
    if row is None:
        console.print(
            f"No tienes ninguna captura que encaje con [bold]{args.pokemon}[/]. "
            "Mira `pokedex list` para ver tus capturas "
            "(o usa `pokedex search` para consultar sin capturar)."
        )
        return 1

    # Si se capturó sin conexión, intenta enriquecer ahora.
    if row["types"] is None:
        _species_data_use_case().execute(row["species"], row["form"])
        refreshed = _collection_queries().resolve(str(row["id"]), None)
        if refreshed is not None:
            row = refreshed

    # Cachés antiguas (previas a esta fase) no tienen gender_rate/abilities:
    # se curan solas al mirar el Pokémon (`pokedex refresh` las cura en bloque).
    if row.get("gender_rate") is None and row["types"] is not None:
        _species_data_use_case().execute(row["species"], row["form"], refresh=True)
        composition.backfill_individuality().execute()
        refreshed = _collection_queries().resolve(str(row["id"]), None)
        if refreshed is not None:
            row = refreshed

    sprite = _sprite_renderer().capture_sprite(row["species"], row["form"], bool(row["shiny"]))
    display.render_vision_card(console, row, sprite)
    return 0


def cmd_equipo(args: argparse.Namespace) -> int:
    if args.accion is not None:
        action = team_application.TeamAction(args.accion)
        raw_identifier = args.id
        if raw_identifier is not None and str(raw_identifier).isdigit():
            capture_id = int(raw_identifier)
        else:
            capture_id = _select_capture_for_team(action, raw_identifier)
        if capture_id is None:
            return 0
        result = _manage_team_use_case().execute(action, capture_id)
        if result.status is team_application.TeamStatus.NOT_FOUND:
            print(f"No existe ninguna captura con id {capture_id}.")
            return 1
        if action is team_application.TeamAction.ADD:
            if result.status is team_application.TeamStatus.ALREADY_MEMBER:
                print(f"La captura #{capture_id} ya está en el equipo.")
                return 0
            if result.status is team_application.TeamStatus.FULL:
                print(
                    "Tu equipo ya tiene 6 Pokémon. "
                    "Quita uno con `pokedex equipo remove [id|nombre]`."
                )
                return 1
            print(f"Añadido al equipo (#{capture_id}).")
        else:
            if result.status is team_application.TeamStatus.ALREADY_REMOVED:
                print(f"La captura #{capture_id} ya estaba fuera del equipo.")
                return 0
            print(f"Quitado del equipo (#{capture_id}).")
        return 0
    display.render_team_panel(console, _collection_queries().team())
    return 0


def _manage_team_use_case() -> team_application.ManageTeam:
    return composition.manage_team()


def _collection_queries() -> collection_application.CollectionQueries:
    return composition.collection_queries()


def _species_data_use_case() -> species_application.GetSpeciesData:
    return composition.species_data()


def _sprite_renderer():
    return composition.sprite_renderer()


def _process_evolutions_use_case() -> evolution_application.ProcessEvolutions:
    return composition.process_evolutions()


def _open_terminal_use_case(write_last_seen) -> hook_application.OpenTerminal:
    return hook_application.OpenTerminal(
        evolutions=_process_evolutions_use_case(),
        sync_activity=_sync_training,
        start_wild_encounter=lambda generations: composition.run_wild_encounter(
            generations, write_last_seen
        ),
    )


def cmd_tipos(args: argparse.Namespace) -> int:
    display.render_tipos_breakdown(console, _collection_queries().type_counts())
    return 0


def cmd_ranking(args: argparse.Namespace) -> int:
    composition.backfill_individuality().execute()
    ranked, missing = _collection_queries().ranking()
    display.render_ranking_table(console, ranked, missing)
    return 0


def cmd_legendarios(args: argparse.Namespace) -> int:
    display.render_legendarios_panel(console, _collection_queries().rare())
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Prueba la animación de captura sin tocar la base de datos ni last_seen."""
    if args.nombre:
        species, form, shiny = args.nombre, args.form, args.shiny
    elif args.legendary:
        species, form, shiny = capture.random_legendary(), args.form, args.shiny
    else:
        species, form, shiny = composition.pick_species_form_shiny(args.generations)
        if args.shiny:
            shiny = True

    ball = composition.resolve_ball(args.bola) or composition.ball_catalog()["pokeball"]

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
        console,
        species,
        form,
        shiny,
        caught,
        ball_slug=ball.slug,
        sprite_renderer=_sprite_renderer(),
    )
    if not caught:
        console.print("[yellow]¡Se soltó![/] (era solo una demo)")
    return 0


def cmd_demo_vision(args: argparse.Namespace) -> int:
    """Ficha `vision` de un individuo SINTÉTICO al nivel pedido: para probar
    el renderizado de stats por nivel sin capturar nada ni tocar la BD."""
    import json as json_module

    from pokedex_cli.domain import individuality
    from pokedex_cli.domain.identity import normalize_form

    species = normalize_species(args.nombre)
    form = normalize_form(args.form)
    cache = _species_data_use_case().execute(species, form)
    if cache is None:
        console.print(
            f"No se pudo obtener info de '{args.nombre}' "
            "(¿nombre incorrecto? ¿sin conexión? prueba `krabby list`)."
        )
        return 1

    level = max(1, min(progression.MAX_LEVEL, int(args.nivel)))
    seed = f"demo:{species}:{form}:nv{level}:{args.seed}"
    ivs, nature = individuality.derive_ivs_nature(seed)
    gender = individuality.derive_gender(seed, _cache_gender_rate(cache))
    abilities = _cache_abilities(cache)
    ability = individuality.derive_ability(seed, abilities)

    def _decoded(key: str, fallback):
        try:
            value = json_module.loads(cache[key] or "null")
        except (TypeError, ValueError, KeyError):
            return fallback
        return value if value is not None else fallback

    bases = {key: cache[key] for key in individuality.STAT_KEYS}
    stats = individuality.compute_stats(bases, ivs, level, nature)
    growth = _cache_value(cache, "growth_rate", "medium")
    floor = progression.experience_for_level(growth, level)
    is_max = level >= progression.MAX_LEVEL
    ceiling = floor if is_max else progression.experience_for_level(growth, level + 1)

    row = {
        "demo": True,
        "species": species,
        "form": form,
        "shiny": bool(args.shiny),
        "pokedex_id": cache["pokedex_id"],
        "types": _decoded("types", []),
        "is_legendary": bool(cache["is_legendary"]),
        "is_mythical": bool(cache["is_mythical"]),
        "generation": cache["generation"],
        "flavor_text": cache["flavor_text"],
        "form_data_exact": cache["form_data_exact"],
        "gender_rate": _cache_gender_rate(cache),
        "level": level,
        "is_max_level": is_max,
        "experience_into_level": 0,
        "experience_for_next_level": ceiling - floor,
        "ivs": ivs,
        "nature": nature,
        "gender": gender,
        "ability": ability,
        "stats": stats,
        **{key: cache[key] for key in individuality.STAT_KEYS},
    }
    console.print(
        f"[dim]· demo ·[/] individuo sintético de [bold]{_display_name(species, form)}[/] "
        f"al nivel {level} [dim](semilla {args.seed}; usa --seed para otro individuo)[/]"
    )
    sprite = _sprite_renderer().capture_sprite(species, form, bool(args.shiny))
    display.render_vision_card(console, row, sprite)
    return 0


def cmd_demo_evolucion(args: argparse.Namespace) -> int:
    """Prueba visual segura: no toca capturas, experiencia ni encuentros."""
    animation.play_evolution_animation(
        console,
        args.origen,
        args.form_origen,
        args.destino,
        args.form_destino,
        args.shiny,
        speed=args.speed,
        sprite_renderer=_sprite_renderer(),
    )
    return 0


def cmd_completion(args: argparse.Namespace) -> int:
    """Imprime el script de autocompletado para el shell indicado."""
    completion_file = composition.completion_file(args.shell)
    if not completion_file.exists():
        completion_file = Path.home() / ".zfunc" / "_pokedex"
    try:
        sys.stdout.write(completion_file.read_text())
    except FileNotFoundError:
        print(f"No hay autocompletado disponible para '{args.shell}'.", file=sys.stderr)
        return 1
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    """Reemplaza la caché PokeAPI de todas las especies capturadas."""
    result = composition.refresh_species_data().execute()
    print(f"Actualizados {result.refreshed} de {result.total} perfiles capturados.")
    if not result.failed:
        return 0
    print("No se pudieron actualizar:")
    for identity in result.failed:
        suffix = "" if identity.form == "regular" else f" ({identity.form})"
        print(f"  - {identity.species}{suffix}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pokedex",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Pokédex de terminal.\n\n"
            "`pokedex` a secas (sin argumentos) abre la Pokédex interactiva: la\n"
            "lista nacional completa, con búsqueda, filtros y pantallita retro.\n\n"
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
            "  pokedex refresh                 recarga los datos de tus capturas\n"
            "  pokedex demo                    prueba la animación sin capturar\n"
            "  pokedex demo -L                 pruébala contra un legendario al azar\n"
            "  pokedex demo-evolucion bulbasaur ivysaur  prueba una evolución\n"
            "\nAutocompletado zsh:  pokedex completion zsh > ~/.zfunc/_pokedex"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<comando>", required=True)

    hook_parser = subparsers.add_parser(
        "hook",
        help="(interno) pinta un Pokémon y recuerda cuál fue",
        description="Uso interno desde ~/.zshrc: pinta un Pokémon al azar y guarda "
        "en silencio cuál fue, para poder capturarlo luego.",
    )
    hook_parser.add_argument(
        "generations",
        nargs="?",
        default="1-9",
        metavar="GENS",
        help="generaciones a incluir, p.ej. '1-3' o '1,3,6' (por defecto 1-9)",
    )
    hook_parser.set_defaults(func=cmd_hook)

    ver_parser = subparsers.add_parser(
        "ver",
        help="muestra qué Pokémon está esperando (sin capturarlo)",
        description="Muestra qué Pokémon está esperando ahora mismo y si ya lo capturaste.",
    )
    ver_parser.set_defaults(func=cmd_ver)

    capturar_parser = subparsers.add_parser(
        "capturar",
        help="intenta capturar el Pokémon que está esperando",
        description="Elige una Pokeball y lánzala al Pokémon que está esperando. La "
        "Pokeball normal es infinita; las especiales aumentan la probabilidad y se "
        "reponen con tiempo y actividad Git. Si se suelta, puedes reintentarlo.",
    )
    capturar_parser.add_argument(
        "-b",
        "--bola",
        metavar="BOLA",
        help="selección directa: poke, super, ultra o master (sin menú)",
    )
    capturar_parser.add_argument(
        "--debug",
        action="store_true",
        help="muestra la probabilidad exacta de captura",
    )
    capturar_parser.set_defaults(func=cmd_capturar)

    bolsas_parser = subparsers.add_parser(
        "bolsas",
        help="muestra y actualiza tus Pokeballs",
        description="Muestra el stock. La Pokeball normal es infinita; las especiales "
        "se reponen con tiempo y commits propios en horario laboral.",
    )
    bolsas_parser.add_argument(
        "--info",
        action="store_true",
        help="muestra efectividad, límites y detalles de la reposición",
    )
    bolsas_parser.set_defaults(func=cmd_bolsas)

    list_parser = subparsers.add_parser(
        "list",
        help="lista tus capturas",
        description="Lista todas tus capturas con su N.º de Pokédex y orden de captura.",
    )
    list_parser.set_defaults(func=cmd_list)

    search_parser = subparsers.add_parser(
        "search",
        help="ficha de cualquier Pokémon o forma",
        description="Muestra la ficha (tipos, stats, descripción) de cualquier Pokémon, "
        "aunque no lo hayas capturado.",
    )
    search_parser.add_argument("nombre", help="nombre del Pokémon (como en `krabby list`)")
    search_parser.add_argument(
        "-f",
        "--form",
        default="regular",
        metavar="FORMA",
        help="forma alternativa, p.ej. mega-x, gmax, alola (por defecto: regular)",
    )
    search_parser.set_defaults(func=cmd_search)

    vision_parser = subparsers.add_parser(
        "vision",
        help="vista enriquecida de un Pokémon capturado (sprite + ficha)",
        description="Muestra en grande el sprite de uno de tus Pokémon capturados junto "
        "a su ficha completa de PokeAPI: tipos, barras de stats, N.º de Pokédex, "
        "rareza, descripción y datos de la captura.",
    )
    vision_parser.add_argument(
        "pokemon",
        help="id de captura (como en `pokedex list`) o nombre de un Pokémon capturado",
    )
    vision_parser.add_argument(
        "-f",
        "--form",
        default=None,
        metavar="FORMA",
        help="desambigua la forma cuando buscas por nombre (p.ej. mega-x, alola)",
    )
    vision_parser.set_defaults(func=cmd_vision)

    equipo_parser = subparsers.add_parser(
        "equipo",
        help="ve o gestiona tu equipo (máx. 6)",
        description="Sin argumentos muestra tu equipo. `add` y `remove` abren un "
        "selector; también aceptan directamente un ID o nombre.",
    )
    equipo_parser.add_argument(
        "accion",
        nargs="?",
        choices=["add", "remove"],
        metavar="{add,remove}",
        help="añade o quita una captura del equipo",
    )
    equipo_parser.add_argument(
        "id",
        nargs="?",
        metavar="ID|NOMBRE",
        help="id o nombre de Pokémon; si se omite, abre el selector interactivo",
    )
    equipo_parser.set_defaults(func=cmd_equipo)

    tipos_parser = subparsers.add_parser(
        "tipos",
        help="desglose de tus capturas por tipo",
        description="Cuenta tus capturas por tipo elemental.",
    )
    tipos_parser.set_defaults(func=cmd_tipos)

    ranking_parser = subparsers.add_parser(
        "ranking",
        help="ranking por suma de stats base",
        description="Ordena tus capturas por la suma de sus stats base, con medallas.",
    )
    ranking_parser.set_defaults(func=cmd_ranking)

    legendarios_parser = subparsers.add_parser(
        "legendarios",
        help="tu salón de la fama de legendarios y singulares",
        description="Muestra los legendarios y singulares que has capturado.",
    )
    legendarios_parser.set_defaults(func=cmd_legendarios)

    refresh_parser = subparsers.add_parser(
        "refresh",
        help="borra y recarga los datos PokeAPI de tus capturas",
        description="Vacía la caché local de especies y vuelve a consultar PokeAPI "
        "para cada combinación de especie y forma que hayas capturado.",
    )
    refresh_parser.set_defaults(func=cmd_refresh)

    demo_parser = subparsers.add_parser(
        "demo",
        help="prueba la animación de captura sin guardar nada",
        description="Reproduce la animación de captura contra un Pokémon (al azar o el "
        "que indiques) SIN tocar la base de datos ni el Pokémon que espera. Ideal "
        "para probar la animación.",
    )
    demo_parser.add_argument(
        "nombre",
        nargs="?",
        help="Pokémon concreto (por defecto: uno al azar)",
    )
    demo_parser.add_argument(
        "-L",
        "--legendary",
        action="store_true",
        help="prueba contra un legendario/singular al azar",
    )
    demo_parser.add_argument(
        "-f", "--form", default="regular", metavar="FORMA", help="forma alternativa"
    )
    demo_parser.add_argument("-s", "--shiny", action="store_true", help="fuerza variante shiny")
    demo_parser.add_argument(
        "-g",
        "--generations",
        default="1-9",
        metavar="GENS",
        help="generaciones para el azar (por defecto 1-9)",
    )
    demo_parser.add_argument(
        "-r",
        "--result",
        choices=["random", "catch", "escape"],
        default="random",
        help="fuerza el resultado (por defecto: al azar)",
    )
    demo_parser.add_argument(
        "-b",
        "--bola",
        default="poke",
        choices=["poke", "super", "ultra", "master"],
        help="Pokeball cuya animación quieres probar (por defecto: poke)",
    )
    demo_parser.set_defaults(func=cmd_demo)

    vision_demo = subparsers.add_parser(
        "demo-vision",
        help="ficha vision de un individuo sintético al nivel que pidas",
        description="Renderiza la ficha `vision` de un individuo generado al vuelo "
        "(IVs, naturaleza, sexo y habilidad deterministas según --seed) al nivel "
        "indicado, para probar cómo escalan las barras de stats. No guarda nada.",
    )
    vision_demo.add_argument("nombre", help="nombre del Pokémon (como en `krabby list`)")
    vision_demo.add_argument(
        "-n",
        "--nivel",
        type=int,
        default=50,
        metavar="NIVEL",
        help="nivel del individuo sintético, 1-100 (por defecto: 50)",
    )
    vision_demo.add_argument(
        "-f", "--form", default="regular", metavar="FORMA", help="forma alternativa"
    )
    vision_demo.add_argument("-s", "--shiny", action="store_true", help="variante shiny")
    vision_demo.add_argument(
        "--seed",
        default="1",
        metavar="SEMILLA",
        help="cambia la semilla para obtener otro individuo (IVs/naturaleza/sexo)",
    )
    vision_demo.set_defaults(func=cmd_demo_vision)

    evolution_demo = subparsers.add_parser(
        "demo-evolucion",
        help="prueba la animación de evolución sin guardar nada",
        description="Hace evolucionar visualmente dos especies sin tocar tu partida.",
    )
    evolution_demo.add_argument("origen", nargs="?", default="bulbasaur")
    evolution_demo.add_argument("destino", nargs="?", default="ivysaur")
    evolution_demo.add_argument("--form-origen", default="regular", metavar="FORMA")
    evolution_demo.add_argument("--form-destino", default="regular", metavar="FORMA")
    evolution_demo.add_argument("-s", "--shiny", action="store_true")
    evolution_demo.add_argument(
        "--speed",
        type=float,
        default=1.0,
        metavar="FACTOR",
        help="factor de duración: 0.7 más rápida, 1.4 más pausada",
    )
    evolution_demo.set_defaults(func=cmd_demo_evolucion)

    completion_parser = subparsers.add_parser(
        "completion",
        help="imprime el script de autocompletado del shell",
        description="Imprime por stdout el script de autocompletado. Para zsh:\n"
        "  pokedex completion zsh > ~/.zfunc/_pokedex\n"
        "y asegúrate de tener ~/.zfunc en tu fpath antes de compinit.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    completion_parser.add_argument("shell", choices=["zsh"], help="shell objetivo")
    completion_parser.set_defaults(func=cmd_completion)

    return parser


def _run_tui() -> int:
    try:
        from pokedex_cli.presentation.tui.app import run_tui
    except ModuleNotFoundError as error:
        if error.name and error.name.split(".")[0] == "textual":
            print(
                "La Pokédex interactiva necesita 'textual'. Reinstala con ./install.sh.",
                file=sys.stderr,
            )
            return 1
        raise
    return run_tui()


def main() -> int:
    if len(sys.argv) == 1 and sys.stdin.isatty() and sys.stdout.isatty():
        return _run_tui()
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as error:
        if not composition.is_recoverable_failure(error) and args.command != "hook":
            raise
        composition.record_failure(f"command {args.command}", error)
        prefix = "pokedex hook" if args.command == "hook" else "pokedex"
        detail = (
            f"error recuperable: {error}"
            if composition.is_recoverable_failure(error)
            else str(error)
        )
        print(f"{prefix}: {detail}", file=sys.stderr)
        return 0 if args.command == "hook" else 1


if __name__ == "__main__":
    sys.exit(main())
