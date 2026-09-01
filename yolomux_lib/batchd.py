"""Canonical import and executable entry point for YOLOmux's batch broker."""

import sys

from .infra import batchd as _implementation

sys.modules[__name__] = _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
