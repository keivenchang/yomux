"""Compatibility alias for :mod:`yolomux_lib.infra.cache`."""

from .infra import cache as _implementation
import sys

sys.modules[__name__] = _implementation
