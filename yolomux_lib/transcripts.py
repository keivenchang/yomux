"""Compatibility alias for :mod:`yolomux_lib.observability.transcripts`."""

from .observability import transcripts as _implementation
import sys

sys.modules[__name__] = _implementation
