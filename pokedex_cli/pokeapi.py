"""Compatibility alias for the legacy PokeAPI parsing facade."""

import sys

from pokedex_cli.infrastructure import pokeapi_parsing as _implementation

sys.modules[__name__] = _implementation
