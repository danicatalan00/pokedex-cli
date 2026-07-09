# UPDATE — para agente. Base = commit 0c228d0 de pokedex-cli. Aplica 2 features.

## F1: comando `pokedex vision <id|nombre> [-f FORMA]`
Ficha detalle de un Pokémon YA capturado: sprite krabby (izq) + datos PokeAPI (der), layout Panel DOUBLE_EDGE, borde = color tipo primario, barras stats gradiente.

### cli.py
1. `_row_with_cache`: en rama cache-hit añadir tras `row["pokedex_id"]=cache["pokedex_id"]`:
```
row["generation"]=cache["generation"]; row["flavor_text"]=cache["flavor_text"]
row["capture_rate"]=cache["capture_rate"]; row["form_data_exact"]=cache["form_data_exact"]
```
en rama else añadir: `generation=None, flavor_text=None, capture_rate=None, form_data_exact=1`.
2. Antes de `cmd_equipo`, añadir:
```
def _resolve_capture(conn, ident, form):
    if ident.isdigit(): return storage.get_capture(conn, int(ident))
    species=ident.lower()
    for r in storage.list_captures(conn):  # ya viene DESC por caught_at
        if r["species"]==species and (form is None or r["form"]==form): return r
    return None

def cmd_vision(args):
    conn=storage.get_connection()
    cap=_resolve_capture(conn,args.pokemon,args.form)
    if cap is None: console.print("No tienes captura que encaje... usa pokedex list"); return 1
    row=_row_with_cache(conn,cap)
    if row["types"] is None:  # capturado sin conexión -> enriquecer ahora
        data=pokeapi.fetch_species_data(row["species"],row["form"])
        if data: storage.upsert_species_cache(conn,row["species"],row["form"],data,_now_iso()); row=_row_with_cache(conn,cap)
    sprite=krabby_bridge.capture_sprite(row["species"],row["form"],bool(row["shiny"]))
    display.render_vision_card(console,row,sprite); return 0
```
3. En `build_parser`, tras `search_parser.set_defaults`:
```
vp=subparsers.add_parser("vision",help="vista enriquecida de un Pokémon capturado (sprite + ficha)",description="...")
vp.add_argument("pokemon",help="id de captura o nombre de un Pokémon capturado")
vp.add_argument("-f","--form",default=None,metavar="FORMA",help="desambigua forma al buscar por nombre")
vp.set_defaults(func=cmd_vision)
```

### krabby_bridge.py
Añadir (antes de `_best_effort_fallback`):
```
def capture_sprite(species, form, shiny):
    args=["krabby","name",species]
    if form!="regular": args+=["-f",form]
    if shiny: args.append("-s")
    args.append("--no-title")
    try: result=subprocess.run(args,capture_output=True,text=True,check=True)
    except (subprocess.CalledProcessError,FileNotFoundError): return None
    return result.stdout.rstrip("\n") or None
```

### display.py
Imports: `from rich.console import Console, Group` y añadir `from rich.text import Text`.
Añadir helpers + función (antes de `render_team_panel`):
```
STAT_LABELS=[("HP","hp"),("Ataque","atk"),("Defensa","def"),("At. Esp.","spa"),("Def. Esp.","spd"),("Velocidad","spe")]
GEN_ROMAN={f"generation-{a}":r for a,r in [("i","I"),("ii","II"),("iii","III"),("iv","IV"),("v","V"),("vi","VI"),("vii","VII"),("viii","VIII"),("ix","IX")]}

def _stat_color(v):
    return "green1" if v>=130 else "green3" if v>=100 else "chartreuse3" if v>=80 else "yellow3" if v>=60 else "orange3" if v>=40 else "red3"

def _stat_bar(v, width=16):  # escala /200, satura arriba, min 1 bloque si v>0
    v=v or 0; filled=min(width,max(1,round(v/200*width))) if v else 0
    return f"[{_stat_color(v)}]{'█'*filled}[/][grey30]{'━'*(width-filled)}[/]"

def _interleave(blocks):  # línea en blanco entre bloques
    out=[]
    for i,b in enumerate(blocks):
        out.append(b)
        if i<len(blocks)-1: out.append(Text(""))
    return out

def render_vision_card(console, row, sprite):
    species,form=row["species"],row["form"]; name=display_name(species,form)
    types=row["types"] or []; primary=types[0] if types else "normal"
    accent=TYPE_COLORS.get(primary,"cyan")
    dex=f"#{row['pokedex_id']:03d}" if row.get("pokedex_id") else "#???"
    header=Text(); header.append(f"N.º {dex}  ",style="bold grey62"); header.append(name,style=f"bold {accent}")
    if row["shiny"]: header.append("  ✨ shiny",style="bold yellow1")
    blocks=[header]
    if types: blocks.append(Text.from_markup("  "+type_badges(types)))
    if row["types"] is not None:
        stats=Table.grid(padding=(0,1))
        stats.add_column(justify="right",style="bold grey70",min_width=9); stats.add_column(); stats.add_column(justify="right",min_width=3)
        total=0
        for label,key in STAT_LABELS:
            v=row[key] or 0; total+=v; stats.add_row(label,_stat_bar(v),f"[{_stat_color(v)}]{v}[/]")
        stats.add_row("","",""); stats.add_row("[bold]Total[/]","",f"[bold {accent}]{total}[/]")
        blocks.append(stats)
        if not row.get("form_data_exact",1): blocks.append(Text("stats de la forma base (sin datos exactos de la variante)",style="dim italic"))
    else: blocks.append(Text("Sin datos enriquecidos todavía (se capturó sin conexión).",style="dim italic"))
    badges=[]
    if row["is_legendary"]: badges.append("[gold3]★ Legendario[/]")
    if row["is_mythical"]: badges.append("[magenta]✦ Singular[/]")
    if badges: blocks.append(Text.from_markup("  ".join(badges)))
    if row.get("flavor_text"): blocks.append(Panel(Text(row["flavor_text"],style="italic grey85"),box=box.ROUNDED,border_style="grey37",padding=(0,1)))
    gen=GEN_ROMAN.get(row.get("generation") or "",None)
    foot=Text(); foot.append("Capturado: ",style="grey54"); foot.append(row["caught_at"][:10],style="grey85"); foot.append(f"   ·   captura #{row['id']}",style="grey54")
    if gen: foot.append(f"   ·   Gen {gen}",style="grey54")
    if row["in_team"]: foot.append("   ·   ",style="grey54"); foot.append("⭐ en tu equipo",style="gold3")
    blocks.append(foot)
    info=Group(*_interleave(blocks))
    if sprite:
        layout=Table.grid(padding=(0,4)); layout.add_column(vertical="middle"); layout.add_column(vertical="middle")
        layout.add_row(Text.from_ansi(sprite),info); body=layout
    else:
        body=Group(Text("(instala krabby para ver el sprite)",style="dim italic"),Text(""),info)
    console.print(Panel(body,title=f"[bold {accent}]◓ POKÉDEX[/]",title_align="left",border_style=accent,box=box.DOUBLE_EDGE,padding=(1,2),expand=False))
```

### completions/_pokedex.zsh
En array `commands`, tras la línea `search`: `'vision:vista enriquecida de un Pokémon capturado (sprite + ficha)'`.
En el `case $line[1]`, tras el bloque `search)`:
```
vision)
  _arguments '(-f --form)'{-f,--form}'[desambigua la forma]:forma:' '1:captura:_pokedex_capture_ids'
  ;;
```

## F2: fix autocompletado zsh con oh-my-zsh
Bug: oh-my-zsh ejecuta `compinit` dentro de `source $ZSH/oh-my-zsh.sh`; el instalador metía el `fpath` al final del `.zshrc` (tras compinit) → completado nunca cargaba.

### install.sh — reemplazar el bloque de autocompletado zshrc
Detectar oh-my-zsh e insertar el `fpath` ANTES de `source $ZSH/oh-my-zsh.sh`; si no, bloque clásico al final. Marcador idempotente = `pokedex-cli: autocompletado`. Al final `rm -f "$HOME"/.zcompdump*`.
```
ZSHRC="$HOME/.zshrc"
if [[ -f "$ZSHRC" ]] && ! grep -qF 'pokedex-cli: autocompletado' "$ZSHRC"; then
    if grep -qF 'source $ZSH/oh-my-zsh.sh' "$ZSHRC"; then
        TMP="$(mktemp)"
        awk '!inserted && index($0,"source $ZSH/oh-my-zsh.sh"){print "# pokedex-cli: autocompletado (antes del compinit de oh-my-zsh)";print "fpath=(\"$HOME/.zfunc\" $fpath)";print "";inserted=1}{print}' "$ZSHRC" > "$TMP" && mv "$TMP" "$ZSHRC"
    else
        cat >> "$ZSHRC" <<'ZBLOCK'

# pokedex-cli: autocompletado
fpath=("$HOME/.zfunc" $fpath)
autoload -Uz compinit && compinit
ZBLOCK
    fi
    rm -f "$HOME"/.zcompdump* 2>/dev/null || true
fi
```

### Instalación en máquina destino (no requiere sudo)
```
cp completions/_pokedex.zsh ~/.zfunc/_pokedex   # mkdir -p ~/.zfunc antes
# insertar fpath antes de oh-my-zsh (ver install.sh) + rm -f ~/.zcompdump*
```
Activar: abrir terminal nueva (no `source`, rompe con p10k instant prompt).

## Verificación
```
.venv/bin/python -m pokedex_cli --help | grep vision
.venv/bin/python -m pokedex_cli vision <id-existente>          # panel con sprite+stats
zsh -c 'fpath=("$HOME/.zfunc" $fpath); autoload -Uz compinit && compinit -u; [[ -n "${_comps[pokedex]}" ]] && echo OK'
bash -n install.sh
```
Notas: barras width=16 para caber en 80 cols. `vision` requiere Pokémon capturado (si no, error → `pokedex list`). Sin krabby: muestra solo ficha.
