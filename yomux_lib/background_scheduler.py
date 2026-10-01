"""Compatibility exports for the process-local background scheduler."""

from .infra import background_scheduler as _implementation
import sys

sys.modules[__name__] = _implementation
