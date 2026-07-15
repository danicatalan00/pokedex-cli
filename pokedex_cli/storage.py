"""Compatibility alias for the supported caller-owned SQLite helpers."""

import sys

from pokedex_cli.infrastructure import legacy_storage as _implementation

sys.modules[__name__] = _implementation
