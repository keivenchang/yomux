"""Compatibility alias for :mod:`yolomux_lib.workspace.settings`."""

from .workspace import settings as _implementation
import sys

sys.modules[__name__] = _implementation
