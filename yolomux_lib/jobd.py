"""Compatibility alias for :mod:`yolomux_lib.infra.jobd`."""

from .infra import jobd as _implementation
import sys

sys.modules[__name__] = _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
