"""Compatibility alias for :mod:`yolomux_lib.infra.state_services`."""

from .infra import state_services as _implementation
import sys

sys.modules[__name__] = _implementation
