"""Compatibility alias for :mod:`yomux_lib.infra.types`."""

from .infra import types as _implementation
import sys

sys.modules[__name__] = _implementation
