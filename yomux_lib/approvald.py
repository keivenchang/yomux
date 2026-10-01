"""Compatibility alias for :mod:`yomux_lib.approval.approvald`."""

from .approval import approvald as _implementation
import sys

sys.modules[__name__] = _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
