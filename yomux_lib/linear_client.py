"""Compatibility alias for :mod:`yomux_lib.integrations.linear_client`."""

from .integrations import linear_client as _implementation
import sys

sys.modules[__name__] = _implementation
