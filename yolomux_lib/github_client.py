"""Compatibility alias for :mod:`yolomux_lib.integrations.github_client`."""

from .integrations import github_client as _implementation
import sys

sys.modules[__name__] = _implementation
