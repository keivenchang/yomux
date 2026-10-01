"""Compatibility alias for :mod:`yomux_lib.chat.chat_store`."""

from .chat import chat_store as _implementation
import sys

sys.modules[__name__] = _implementation
