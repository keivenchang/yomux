"""Compatibility alias for :mod:`yolomux_lib.approval.claude_permission_hook`."""

from .approval import claude_permission_hook as _implementation
import sys

sys.modules[__name__] = _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
