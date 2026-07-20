"""Compatibility alias for :mod:`yolomux_lib.observability.pricing_catalog`."""

from .observability import pricing_catalog as _implementation
import sys

sys.modules[__name__] = _implementation
