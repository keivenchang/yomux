"""Compatibility alias for :mod:`yolomux_lib.infra.atomic_file`."""

from .infra import atomic_file as _implementation
import sys

sys.modules[__name__] = _implementation
