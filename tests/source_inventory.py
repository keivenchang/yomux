"""Immutable, process-scoped Python source and AST inventory for structural tests."""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=None)
def python_source_paths(root: str) -> tuple[Path, ...]:
    """Return the stable Python inventory below one repository path."""

    path = Path(root)
    return tuple(sorted(path.rglob("*.py"))) if path.is_dir() else (path,)


@lru_cache(maxsize=None)
def _parsed_python_source(path_text: str, mtime_ns: int, size: int) -> tuple[str, ast.Module]:
    del mtime_ns, size
    path = Path(path_text)
    source = path.read_text(encoding="utf-8")
    return source, ast.parse(source, filename=str(path))


def parsed_python_source(path: Path) -> tuple[str, ast.Module]:
    """Read and parse one source file once until its filesystem identity changes."""

    resolved = path.resolve()
    stat = resolved.stat()
    return _parsed_python_source(str(resolved), int(stat.st_mtime_ns), int(stat.st_size))


def clear_python_source_inventory_cache() -> None:
    """Test hook for proving cache and invalidation behavior."""

    python_source_paths.cache_clear()
    _parsed_python_source.cache_clear()
