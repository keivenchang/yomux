"""Compatibility alias for :mod:`yomux_lib.tmux.sessions`."""

from .tmux import sessions as _implementation
import sys

sys.modules[__name__] = _implementation
