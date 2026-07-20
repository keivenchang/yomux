"""Compatibility alias for :mod:`yolomux_lib.search.file_index`."""

from .search import file_index as _implementation
import sys

sys.modules[__name__] = _implementation
