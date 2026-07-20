"""Compatibility alias for :mod:`yolomux_lib.approval.approvals`."""

from .approval import approvals as _implementation
import sys

sys.modules[__name__] = _implementation
