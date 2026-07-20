"""Compatibility alias for :mod:`yolomux_lib.chat.chat_questions`."""

from .chat import chat_questions as _implementation
import sys

sys.modules[__name__] = _implementation
