"""Compatibility alias for :mod:`yolomux_lib.approval.auto_approve_worker`."""

from .approval import auto_approve_worker as _implementation
import sys

sys.modules[__name__] = _implementation
