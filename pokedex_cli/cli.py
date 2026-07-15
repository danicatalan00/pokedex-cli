"""Compatibility alias for :mod:`pokedex_cli.presentation.cli`."""

import sys

from pokedex_cli.presentation import cli as _implementation

sys.modules[__name__] = _implementation
