"""Compatibility alias for :mod:`yolomux_lib.chat.chat_service`."""

from .chat import chat_service as _implementation
import sys

sys.modules[__name__] = _implementation
