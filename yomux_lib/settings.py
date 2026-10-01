"""Compatibility alias for :mod:`yomux_lib.workspace.settings`."""

from .workspace import settings as _implementation
import sys

sys.modules[__name__] = _implementation
