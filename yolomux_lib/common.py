"""Compatibility alias for :mod:`yolomux_lib.infra.common`."""

from .infra import common as _implementation
import sys

sys.modules[__name__] = _implementation
