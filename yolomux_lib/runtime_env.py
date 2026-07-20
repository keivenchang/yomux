"""Compatibility alias for :mod:`yolomux_lib.infra.runtime_env`."""

from .infra import runtime_env as _implementation
import sys

sys.modules[__name__] = _implementation
