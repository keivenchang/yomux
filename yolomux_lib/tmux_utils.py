"""Compatibility alias for :mod:`yolomux_lib.tmux.tmux_utils`."""

from .tmux import tmux_utils as _implementation
import sys

sys.modules[__name__] = _implementation
