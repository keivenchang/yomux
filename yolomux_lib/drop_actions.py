"""Compatibility alias for :mod:`yolomux_lib.workspace.drop_actions`."""

from .workspace import drop_actions as _implementation
import sys

sys.modules[__name__] = _implementation
