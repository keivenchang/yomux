"""Compatibility alias for :mod:`yolomux_lib.observability.activity_summary`."""

from .observability import activity_summary as _implementation
import sys

sys.modules[__name__] = _implementation
