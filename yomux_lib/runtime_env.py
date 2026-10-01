"""Compatibility alias for :mod:`yomux_lib.infra.runtime_env`."""

from .infra import runtime_env as _implementation
import sys

sys.modules[__name__] = _implementation
