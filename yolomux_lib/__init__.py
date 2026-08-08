"""YOLOmux implementation modules."""

from pathlib import Path as _Path
import sys as _sys


# Supported worktree entrypoints suppress the first package cache before this
# module loads. Keep suppression active while loading the dependency-light path
# owner, which installs the host-local prefix and enables later bytecode writes.
_sys.dont_write_bytecode = True
from .infra.worktree_writer import configure_host_local_artifacts as _configure_host_local_artifacts

_configure_host_local_artifacts(_Path(__file__).resolve().parents[1])

del _Path
del _configure_host_local_artifacts
del _sys
