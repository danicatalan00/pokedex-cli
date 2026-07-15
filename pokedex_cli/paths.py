"""Compatibility alias for local filesystem paths and encounter state."""

import sys

from pokedex_cli.infrastructure import paths as _implementation

sys.modules[__name__] = _implementation
