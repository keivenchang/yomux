"""Compatibility alias for :mod:`yolomux_lib.workspace.session_files`."""

from .workspace import session_files as _implementation
import sys

sys.modules[__name__] = _implementation
