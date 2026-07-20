"""Compatibility alias for :mod:`yolomux_lib.workspace.workdir`."""

from .workspace import workdir as _implementation
import sys

sys.modules[__name__] = _implementation
