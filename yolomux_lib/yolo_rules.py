"""Compatibility alias for :mod:`yolomux_lib.approval.yolo_rules`."""

from .approval import yolo_rules as _implementation
import sys

sys.modules[__name__] = _implementation
