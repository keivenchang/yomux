"""Compatibility alias for :mod:`yolomux_lib.observability.events`."""

from .observability import events as _implementation
import sys

sys.modules[__name__] = _implementation
