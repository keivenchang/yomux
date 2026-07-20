"""Compatibility alias for :mod:`yolomux_lib.workspace.locales`."""

from .workspace import locales as _implementation
import sys

sys.modules[__name__] = _implementation
