"""Compatibility alias for :mod:`yolomux_lib.tmux.tmux_signals`."""

from .tmux import tmux_signals as _implementation
import sys

sys.modules[__name__] = _implementation
