"""Compatibility alias for the supported legacy inventory API."""

import sys

from pokedex_cli.infrastructure import legacy_inventory as _implementation

sys.modules[__name__] = _implementation
