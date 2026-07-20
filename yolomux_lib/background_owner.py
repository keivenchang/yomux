"""Compatibility alias for :mod:`yolomux_lib.infra.background_owner`."""

from .infra import background_owner as _implementation
import sys

sys.modules[__name__] = _implementation
