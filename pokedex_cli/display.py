"""Compatibility alias for :mod:`pokedex_cli.presentation.display`."""

import sys

from pokedex_cli.presentation import display as _implementation

sys.modules[__name__] = _implementation
