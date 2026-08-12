"""YOLOmux implementation modules."""

from pathlib import Path as _Path
import sys as _sys


# Supported worktree entrypoints suppress the first package cache before this
# module loads. Keep suppression active while loading the dependency-light path
# owner, which installs the host-local prefix and enables later bytecode writes.
_sys.dont_write_bytecode = True
from .infra.worktree_writer import configure_host_local_artifacts as _configure_host_local_artifacts
from .infra.worktree_writer import purge_abandoned_namespace_residue as _purge_abandoned_namespace_residue

_configure_host_local_artifacts(_Path(__file__).resolve().parents[1])
# A rolling update from the abandoned 0.6.12 topology leaves ignored __pycache__ debris behind
# retired packages (yolomux_lib/daemon, .../storaged); the empty directory alone makes the retired
# name importable as a namespace package. Remove that residue here so the name stays gone.
_purge_abandoned_namespace_residue(_Path(__file__).resolve().parent)

del _Path
del _configure_host_local_artifacts
del _purge_abandoned_namespace_residue
del _sys
