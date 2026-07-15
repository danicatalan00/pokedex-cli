"""Compatibility alias for the Krabby wild-encounter adapter."""

import sys

from pokedex_cli.infrastructure import wild_encounters as _implementation

sys.modules[__name__] = _implementation
