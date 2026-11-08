# Operación y recuperación

## Backup consistente

No copies solo `pokedex.db`: una escritura válida puede seguir en el WAL. Usa la
API de backup de SQLite o el comando `.backup`, y comprueba que
`PRAGMA integrity_check` devuelve `ok`.

```bash
sqlite3 "$HOME/.local/share/pokedex-cli/pokedex.db" \
  ".backup '$HOME/.local/share/pokedex-cli/pokedex.backup.db'"
sqlite3 "$HOME/.local/share/pokedex-cli/pokedex.backup.db" \
  'PRAGMA integrity_check;'
```

Conserva también los JSON históricos hasta confirmar su entrada en
`legacy_imports`.

## Restaurar

1. Desactiva temporalmente el hook y cierra comandos activos.
2. Conserva una copia del directorio dañado.
3. Verifica el backup y sustituye la base.
4. Elimina solo los `-wal`/`-shm` asociados a la copia dañada.
5. Ejecuta `pokedex list`, valida integridad y reactiva el hook.

No borres la base como primera respuesta: contiene colección, inventario y
actividad.

## Repos que cuentan como actividad

El hook busca repos Git bajo `HOME`. Para limitarlo a un directorio:

```bash
export POKEDEX_REPOS_ROOT="$HOME/Projects"
```

Los commits se sincronizan como mucho una vez por hora; las terminales de en
medio no lanzan Git y la siguiente sincronización recoge todo lo pendiente.
`pokedex bolsas` fuerza la sincronización y un nuevo escaneo de repos.

## Diagnóstico opt-in

```bash
export POKEDEX_DIAGNOSTIC_LOG="$HOME/.local/share/pokedex-cli/diagnostics.log"
pokedex hook 1-3
unset POKEDEX_DIAGNOSTIC_LOG
```

Sin esa variable el startup no escribe trazas. Un bloqueo breve se reintenta
dentro de la política SQLite; un fallo persistente degrada sin romper el prompt.
Para reinstalar sin tocar datos consulta [instalación](installation.md).
