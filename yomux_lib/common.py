"""Compatibility alias for :mod:`yomux_lib.infra.common`."""

from .infra import common as _implementation
import sys

sys.modules[__name__] = _implementation
