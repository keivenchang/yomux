"""Compatibility alias for :mod:`yomux_lib.observability.activity`."""

from .observability import activity as _implementation
import sys

sys.modules[__name__] = _implementation
