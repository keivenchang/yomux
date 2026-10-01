"""Compatibility alias for :mod:`yomux_lib.workspace.metadata`."""

from .workspace import metadata as _implementation
import sys

sys.modules[__name__] = _implementation
