"""Compatibility alias for :mod:`yomux_lib.infra.cache`."""

from .infra import cache as _implementation
import sys

sys.modules[__name__] = _implementation
