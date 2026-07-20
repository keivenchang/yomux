"""Compatibility alias for :mod:`yolomux_lib.tmux.tmux_theme`."""

from .tmux import tmux_theme as _implementation
import sys

sys.modules[__name__] = _implementation
