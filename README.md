<p align="center">
  <img src="docs/assets/project-logo.svg" width="720" alt="Pokédex CLI — terminal sprite logo">
</p>

<p align="center">
  <img alt="Python 3.11–3.13" src="https://img.shields.io/badge/Python-3.11%E2%80%933.13-3776AB?logo=python&logoColor=white">
  <img alt="SQLite 3" src="https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white">
  <img alt="Rich 13.7–15" src="https://img.shields.io/badge/Rich-13.7%E2%80%9315-f97316">
  <img alt="pytest 8" src="https://img.shields.io/badge/pytest-8-0A9EDC?logo=pytest&logoColor=white">
  <img alt="Ruff" src="https://img.shields.io/badge/Ruff-checked-D7FF64?logo=ruff&logoColor=261230">
  <img alt="mypy strict" src="https://img.shields.io/badge/mypy-strict-2A6DB2">
</p>

Pokédex en tu terminal. Cada terminal puede traer un Pokémon de
[Krabby](https://github.com/yannjor/krabby): tú decides si verlo, capturarlo,
entrenarlo y formar un equipo.

El estado vive en SQLite, los datos de especies se enriquecen con
[PokeAPI](https://pokeapi.co) y la experiencia visual se renderiza con Rich. 

## Inicio rápido

Requiere Python 3.11–3.13, Zsh, `rich`, `requests` y Krabby para los sprites.

```bash
./install.sh
pokedex --help
```

El instalador crea una copia estable en el directorio XDG, un shim en
`~/bin/pokedex` y el completado de Zsh. La activación del encuentro al abrir una
terminal está explicada en la [guía de instalación](docs/installation.md).

## Inicio más rápido

Apaga el cerebro y dirige tu agente de código hacia [INSTALL.md](INSTALL.md).

## Comandos habituales

| Comando | Acción |
|---|---|
| `pokedex ver` | Ver el encuentro actual |
| `pokedex capturar [-b bola]` | Intentar una captura |
| `pokedex bolsas` | Consultar stock y actividad |
| `pokedex list` | Ver la colección |
| `pokedex search <nombre>` | Consultar una especie o forma |
| `pokedex vision <id>` | Abrir la ficha de una captura |
| `pokedex equipo [add\|remove] [id\|nombre]` | Gestionar el equipo o elegir en un selector |
| `pokedex refresh` | Borrar y recargar desde PokeAPI los datos de las capturas |
| `pokedex demo` | Probar animaciones sin guardar estado |


## Documentación

La [documentación del proyecto](docs/README.md) está organizada por propósito:

- empezar y operar: [instalación](docs/installation.md) y
  [backup/recuperación](docs/operations.md);
- desarrollar: [testing](docs/testing.md), [gates](docs/change-gates.md) y
  [estándares](docs/engineering-standards.md);
- entender: [arquitectura](docs/architecture.md),
  [modelo de datos](docs/data-model.md) e [infraestructura](docs/infrastructure.md).
