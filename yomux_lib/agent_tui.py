"""Compatibility alias for :mod:`yomux_lib.tmux.agent_tui`."""

from .tmux import agent_tui as _implementation
import sys

sys.modules[__name__] = _implementation
