# Instalación asistida por un agente de código

Esta es la vía *lazy* para instalar o actualizar `pokedex-cli` en Linux o WSL.
Está escrita para un agente con acceso al proyecto y a la máquina donde se va a
usar: el agente inspecciona ambos, ejecuta todo lo que pueda sin privilegios,
pide al usuario únicamente las acciones que requieran `sudo` y deja `pokedex`
listo y comprobado.

No sigas esta guía como una receta ciega. El repositorio y el entorno real son
la fuente de verdad. Lee al menos `AGENTS.md`, `pyproject.toml`, `install.sh` y
`docs/installation.md` antes de actuar, y adapta los comandos al sistema y al
gestor de paquetes encontrados.

## Resultado esperado

Una instalación correcta deja:

- una copia estable y no editable en
  `${XDG_DATA_HOME:-$HOME/.local/share}/pokedex-cli/venv`;
- el estado SQLite, que debe conservarse en una actualización, bajo el mismo
  directorio de datos;
- el ejecutable `~/bin/pokedex`;
- el completado en `~/.zfunc/_pokedex` y su configuración en `~/.zshrc`;
- opcionalmente, Krabby en `PATH` para mostrar sprites y encuentros.

El checkout no forma parte de la instalación efectiva. Puede moverse o
eliminarse después de instalar sin romper `pokedex`.

## Contrato del agente instalador

1. No uses `sudo`, no cambies el shell por defecto y no instales software global
   sin aprobación explícita.
2. Detecta si se trata de una primera instalación o de una actualización. No
   borres el entorno estable, `pokedex.db`, una `.zshrc` existente ni otro estado
   del usuario para «empezar limpio».
3. Inspecciona versiones, rutas, permisos, distribución y gestor de paquetes.
   Comprueba con el mismo `python3` que invocará `install.sh`.
4. Ejecuta directamente todas las acciones reversibles que no necesiten
   privilegios. Si falta un paquete del sistema, explica el diagnóstico y pide
   al usuario que ejecute solo el comando privilegiado necesario.
5. Después de que el usuario resuelva un requisito, vuelve a comprobarlo y
   continúa hasta completar la instalación; no te limites a entregar una lista
   de comandos.
6. Usa `./install.sh` siempre que funcione, pero no quedes bloqueado por él:
   diagnostica el paso exacto, corrígelo o reprodúcelo manualmente respetando las
   rutas y el modelo de instalación actuales, y deja constancia de la desviación.
7. Verifica la copia instalada desde fuera del repositorio. No des por buena una
   importación que pueda estar resolviendo el checkout.

## Inspección inicial

Desde la raíz del proyecto, recoge primero el estado sin modificarlo:

```bash
pwd
git status --short 2>/dev/null || true
sed -n '1,220p' AGENTS.md
sed -n '1,220p' pyproject.toml
sed -n '1,260p' install.sh

printf 'HOME=%s\nXDG_DATA_HOME=%s\nSHELL=%s\n' \
  "$HOME" "${XDG_DATA_HOME:-}" "${SHELL:-}"
command -v python3 || true
python3 --version 2>&1 || true
python3 -m venv --help >/dev/null 2>&1 && echo 'venv: OK' || echo 'venv: falta'
python3 -c 'import rich, requests; print("dependencias Python: OK")' 2>&1 || true
command -v bash || true
command -v zsh || true
command -v krabby || true
command -v pokedex || true
test -x "$HOME/bin/pokedex" && "$HOME/bin/pokedex" --help >/dev/null && \
  echo 'instalación anterior: utilizable' || true
```

Comprueba además que el Python sea 3.11–3.13, que `$HOME` y las rutas XDG sean
escribibles y que haya espacio suficiente. Si ya existe una instalación, anota
su ruta efectiva antes de actualizar:

```bash
(cd /tmp && "$HOME/bin/pokedex" --help >/dev/null)
```

La ausencia de Zsh impide validar su completado, pero no la CLI. La ausencia de
Krabby permite instalar y usar las funciones que no requieren sprites; debe
presentarse como una mejora opcional, no como un fallo de instalación.

## Resolver requisitos

Requisitos base: Linux/WSL, Bash, Python 3.11–3.13 con `venv`, y `rich`
13.7–15 y `requests` 2.x importables por el Python del sistema. Zsh es necesario
para el completado y el hook documentados.

Antes de pedir `sudo`, detecta la distribución (`/etc/os-release`) y las
herramientas disponibles. En Debian, Ubuntu o WSL basado en ellas, un bloque
habitual es:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-rich python3-requests zsh
```

No pidas instalar todo el bloque si solo falta una pieza. En otra distribución,
usa los nombres y el gestor nativos. No intentes sustituir silenciosamente un
Python incompatible ni recurras a `pip` global. Si los paquetes de la
distribución no satisfacen los rangos de `pyproject.toml`, elige una solución
local compatible y comprueba que el entorno estable puede importarla.

Krabby es opcional. Si el usuario quiere la experiencia visual, prefiere el
método de instalación vigente para su plataforma. Si se usa Cargo y ya está
disponible, normalmente basta con una acción sin `sudo`:

```bash
cargo install krabby
```

Instalar Rust, modificar perfiles del shell o descargar un instalador remoto
requiere explicarlo y obtener aprobación antes. Tras cualquier intervención del
usuario, repite únicamente los diagnósticos que fallaban.

## Instalar o actualizar

Con los requisitos satisfechos, desde la raíz del checkout:

```bash
chmod +x install.sh
./install.sh
```

El script crea una wheel e instala/actualiza una copia estable con
`--system-site-packages`; no crea una `.venv` dentro del proyecto. También crea
el shim, actualiza el completado, integra `~/.zfunc` en Zsh de forma idempotente
e invalida dumps antiguos de completado.

Si `~/bin` no está en `PATH`, añade una línea equivalente al archivo de inicio
del shell que use realmente el usuario, sin duplicarla. Para Zsh:

```zsh
export PATH="$HOME/bin:$PATH"
```

No ejecutes `source ~/.zshrc` a ciegas: puede contener efectos interactivos.
Valida su sintaxis y prueba en un proceso Zsh nuevo.

Para habilitar encuentros al abrir una terminal, añade el bloque solo si el
usuario lo quiere y colócalo en la parte interactiva de `~/.zshrc`:

```zsh
if command -v pokedex >/dev/null 2>&1; then
    pokedex hook 1-3
elif command -v krabby >/dev/null 2>&1; then
    krabby random 1-3 --no-title -i
fi
```

`pokedex hook` está diseñado para degradar sin romper el prompt si Krabby, la
red u otro recurso recuperable no están disponibles.

## Verificación obligatoria

Haz las comprobaciones finales contra la instalación estable y desde `/tmp`:

```bash
(cd /tmp && "$HOME/bin/pokedex" --help >/dev/null)
(cd /tmp && "${XDG_DATA_HOME:-$HOME/.local/share}/pokedex-cli/venv/bin/python" \
  -c 'import pokedex_cli; print(pokedex_cli.__file__)')
zsh -n "$HOME/.zshrc"
git diff --check
```

La ruta impresa debe estar en `site-packages/pokedex_cli`, no en el checkout.
Si Zsh no está instalado o no existe `~/.zshrc`, informa de esa única
verificación pendiente en vez de fingir que se ejecutó. Comprueba también que
un Zsh nuevo encuentra `pokedex` cuando hayas cambiado `PATH`.

Con Krabby disponible se puede hacer una prueba visual no destructiva:

```bash
(cd /tmp && "$HOME/bin/pokedex" demo pikachu -r catch)
```

No uses `search`, `hook`, captura ni otras operaciones con red o estado como
única prueba de instalación. Si fallan, separa un problema de instalación de
una indisponibilidad opcional de Krabby o de la red.

No termines con el checkout más nuevo que la copia instalada: si modificaste el
proyecto durante la reparación, vuelve a ejecutar `./install.sh` y repite estas
comprobaciones.

## Recuperación manual

Si `install.sh` falla, conserva su salida y localiza primero la frontera:
creación del venv, construcción/instalación de la wheel, importación de
dependencias, escritura del shim o edición de Zsh. Corrige solo esa frontera.
Puedes crear el entorno estable, instalar la wheel, copiar el completado o
reparar el bloque de Zsh manualmente, pero mantén los artefactos y rutas de
«Resultado esperado» y vuelve a ejecutar las verificaciones completas.

Casos frecuentes:

- Un entorno estable antiguo o roto puede requerir reconstrucción, pero mueve o
  respalda primero el venv y no toques `pokedex.db`.
- Un `pokedex` encontrado en otra ruta no valida `~/bin/pokedex`; prueba el shim
  explícitamente.
- Un error de importación desde la raíz puede quedar oculto por el checkout;
  reproduce siempre desde `/tmp`.
- Si la edición automática de `.zshrc` no encaja con su estructura, haz una
  modificación mínima, conserva el contenido existente y exige `zsh -n`.
- Si hay cambios locales en el repositorio, no los descartes ni sobrescribas;
  instala el estado que el usuario pidió y explica qué versión quedó efectiva.

La desinstalación y el borrado de datos son operaciones distintas y quedan
fuera de este flujo: nunca elimines el estado del usuario como parte de una
instalación o actualización.
