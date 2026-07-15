# Operación, backup y recuperación

## Ubicación de datos

Por defecto, el estado está en
`${XDG_DATA_HOME:-$HOME/.local/share}/pokedex-cli/pokedex.db`. Los archivos
`inventory.json` y `last_seen.json`, si existen, son fuentes históricas que se
importan una sola vez.

## Backup consistente

SQLite puede tener páginas válidas todavía en `-wal`; no copies solo el `.db`
mientras hay procesos escribiendo. La forma segura es la API de backup:

```bash
python3 - <<'PY'
import os, sqlite3
from pathlib import Path

data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "pokedex-cli"
source = sqlite3.connect(data / "pokedex.db")
target = sqlite3.connect(data / "pokedex.backup.db")
with target:
    source.backup(target)
print(target.execute("PRAGMA integrity_check").fetchone()[0])
source.close()
target.close()
PY
```

Conserva también los JSON antiguos hasta confirmar que `legacy_imports` contiene
las filas `inventory` y `encounter`.

## Restauración

1. Desactiva temporalmente `pokedex hook` en `.zshrc` y cierra comandos activos.
2. Haz una copia del directorio dañado antes de modificarlo.
3. Comprueba el backup con `PRAGMA integrity_check`; debe devolver `ok`.
4. Sustituye `pokedex.db` por el backup y elimina únicamente los antiguos
   `pokedex.db-wal` y `pokedex.db-shm` asociados a la copia dañada.
5. Ejecuta `pokedex list` y `pokedex bolsas`; reactiva el hook solo si ambos
   terminan sin error.

No uses `rm pokedex.db` como primera respuesta a una corrupción: elimina
capturas, actividad e inventario.

## Diagnóstico opt-in

El startup normal nunca imprime traceback. Para investigar un fallback:

```bash
export POKEDEX_DIAGNOSTIC_LOG="$HOME/.local/share/pokedex-cli/diagnostics.log"
pokedex hook 1-3
tail -n 80 "$POKEDEX_DIAGNOSTIC_LOG"
```

El archivo contiene contexto, fecha UTC y traceback. Desactívalo con
`unset POKEDEX_DIAGNOSTIC_LOG`. Si aparece `database is locked`, espera a que
termine el otro proceso y reintenta; SQLite espera hasta 5 segundos antes de
declarar el fallo recuperable. Un bloqueo breve se recupera automáticamente.

## Entorno instalado

`install.sh` instala una copia estable en el directorio XDG, no depende de un
checkout editable y puede ejecutarse repetidamente. Si el shim informa de que
falta el entorno, vuelve a ejecutar `install.sh`; no hace falta tocar la base de
datos.
