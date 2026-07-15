"""Compatibility alias for :mod:`pokedex_cli.presentation.animation`."""

import sys

from pokedex_cli.presentation import animation as _implementation

sys.modules[__name__] = _implementation
