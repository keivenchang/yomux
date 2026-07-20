"""Compatibility alias for :mod:`yolomux_lib.workspace.uploads`."""

from .workspace import uploads as _implementation
import sys

sys.modules[__name__] = _implementation
