"""Compatibility alias for :mod:`yomux_lib.approval.prompt_detector`."""

from .approval import prompt_detector as _implementation
import sys

sys.modules[__name__] = _implementation
