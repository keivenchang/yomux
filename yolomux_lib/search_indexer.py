"""Compatibility alias for :mod:`yolomux_lib.search.search_indexer`."""

from .search import search_indexer as _implementation
import sys

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

sys.modules[__name__] = _implementation
