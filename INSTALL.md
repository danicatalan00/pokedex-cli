# INSTALL.md - instalacion agentica de pokedex-cli

Este documento esta escrito para un agente de codigo que deba instalar `pokedex-cli` de forma plug and play en una maquina Linux/WSL, haciendo automaticamente todo lo que no requiera `sudo` y entregando al usuario los comandos que si requieren privilegios.

## Objetivo

Instalar una CLI llamada `pokedex` que:

- crea un entorno virtual local en el proyecto;
- usa `rich` y `requests` disponibles en el sistema;
- crea el ejecutable `~/bin/pokedex`;
- configura autocompletado zsh;
- guarda datos en `~/.local/share/pokedex-cli`;
- se integra opcionalmente con `krabby` para mostrar y capturar Pokemon al abrir terminales.

## Requisitos

Obligatorios:

- Linux o WSL.
- `bash`.
- `python3`.
- modulo `venv` para Python.
- paquetes Python importables desde sistema: `rich` y `requests`.
- `zsh` si se quiere autocompletado y hook al abrir terminal.

Recomendado para la experiencia completa:

- `krabby` disponible en `PATH`.
- `~/.zshrc` editable por el usuario/agente.
- `~/bin` en `PATH`.

## Reglas para el agente instalador

1. No ejecutes `sudo` automaticamente.
2. Primero revisa requisitos con los comandos de diagnostico.
3. Si falta algo que requiere privilegios, muestra al usuario el bloque exacto de comandos de la seccion "Comandos que requieren sudo".
4. Si no falta nada critico, ejecuta la seccion "Instalacion sin sudo".
5. Al final, verifica con `pokedex --help` y, si existe `krabby`, con `pokedex demo pikachu -r catch`.

## Diagnostico inicial

Ejecuta estos comandos desde la raiz del proyecto:

```bash
pwd
test -f install.sh && echo "OK install.sh encontrado"
command -v bash
command -v python3
python3 --version
python3 -m venv --help >/dev/null && echo "OK python venv disponible"
python3 -c "import rich; print('OK rich', rich.__version__)"
python3 -c "import requests; print('OK requests', requests.__version__)"
command -v zsh || true
command -v krabby || true
printf '%s\n' "$PATH" | tr ':' '\n' | grep -Fx "$HOME/bin" || true
```

Interpretacion:

- Si falla `command -v python3`, falta Python.
- Si falla `python3 -m venv --help`, falta el paquete de venv.
- Si falla `import rich` o `import requests`, faltan paquetes Python del sistema.
- Si `command -v krabby` no devuelve ruta, la CLI puede instalarse, pero el hook visual completo no funcionara hasta instalar `krabby`.
- Si `~/bin` no aparece en `PATH`, el instalador creara `~/bin/pokedex`, pero el usuario podria necesitar abrir una terminal nueva o ajustar su shell.

## Comandos que requieren sudo

Si faltan Python, venv, `rich` o `requests` en Debian/Ubuntu/WSL, entrega al usuario estos comandos, linea por linea:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-rich python3-requests zsh
```

Si falta `krabby` y el usuario quiere la experiencia completa, entrega estos comandos. Requieren Rust/Cargo; instala Rust solo si `cargo` no existe:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

Despues de instalar Rust, el usuario debe abrir una terminal nueva o ejecutar:

```bash
. "$HOME/.cargo/env"
```

Luego instalar `krabby` sin sudo:

```bash
cargo install krabby
```

Nota: `cargo install krabby` no requiere `sudo`, pero puede tardar y necesita red.

## Instalacion sin sudo

Desde la raiz del proyecto:

```bash
chmod +x install.sh
./install.sh
```

El script es idempotente. Hace lo siguiente:

- crea `.venv` dentro del proyecto con `--system-site-packages`;
- comprueba que `rich` y `requests` se puedan importar;
- crea `~/.local/share/pokedex-cli`;
- crea o actualiza `~/bin/pokedex`;
- copia autocompletado a `~/.zfunc/_pokedex`;
- anade un bloque de autocompletado a `~/.zshrc` si aun no existe.

Si `~/bin` no esta en `PATH`, anade esto al shell correspondiente. Para zsh:

```bash
grep -qxF 'export PATH="$HOME/bin:$PATH"' ~/.zshrc || printf '\nexport PATH="$HOME/bin:$PATH"\n' >> ~/.zshrc
```

Aplicar cambios de zsh en la sesion actual:

```bash
source ~/.zshrc
```

## Hook opcional al abrir terminal

Para que aparezca un Pokemon al abrir una terminal zsh, el agente puede anadir este bloque a `~/.zshrc` solo si `pokedex` y `krabby` existen:

```bash
if command -v pokedex >/dev/null 2>&1 && command -v krabby >/dev/null 2>&1; then
    pokedex hook 1-9
fi
```

Para limitar generaciones, cambia `1-9` por valores como `1-3` o `1,2,5`.

## Verificacion

Ejecuta:

```bash
command -v pokedex
pokedex --help
pokedex search pikachu
```

Si `krabby` esta instalado:

```bash
pokedex demo pikachu -r catch
pokedex hook 1-3
pokedex ver
```

## Desinstalacion

No requiere sudo:

```bash
rm -f "$HOME/bin/pokedex"
rm -f "$HOME/.zfunc/_pokedex"
rm -rf "$HOME/.local/share/pokedex-cli"
```

Despues elimina manualmente de `~/.zshrc` los bloques relacionados con `pokedex-cli` si ya no se quieren.

## Solucion de problemas

- `install.sh: falta rich/requests`: instala `python3-rich` y `python3-requests` con apt usando los comandos de sudo anteriores.
- `pokedex: no se encontro el entorno virtual`: el proyecto se movio o borro; vuelve a ejecutar `./install.sh` desde la nueva ruta.
- `No hay ningun Pokemon a la vista`: ejecuta `pokedex hook 1-9` o abre una terminal nueva con el hook configurado.
- El hook no pinta nada: comprueba `command -v krabby` y que `krabby random 1-3 --no-title -i` funcione.
- No hay autocompletado: abre una terminal zsh nueva o ejecuta `source ~/.zshrc`.
