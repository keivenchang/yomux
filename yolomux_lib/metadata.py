"""Compatibility alias for :mod:`yolomux_lib.workspace.metadata`."""

from .workspace import metadata as _implementation
import sys

sys.modules[__name__] = _implementation
