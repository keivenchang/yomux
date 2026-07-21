"""Compatibility alias for :mod:`yolomux_lib.infra.common`.

The literal remains here because ``pyproject.toml`` reads this root module as
the static package-version source.
"""

YOLOMUX_VERSION = "0.6.6"

from .infra import common as _implementation
import sys

sys.modules[__name__] = _implementation
