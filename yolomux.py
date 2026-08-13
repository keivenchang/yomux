#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""CLI entrypoint for YOLOmux."""

from __future__ import annotations

import sys
from importlib.util import module_from_spec
from importlib.util import spec_from_file_location
from pathlib import Path


# The package installs the host-local prefix; suppress its own first cache until then.
sys.dont_write_bytecode = True

# Cap glibc malloc arenas before any allocation-heavy import or thread pool spawns,
# so a direct `python yolomux.py` launch stays lean like a boot.sh launch does.
from yolomux_lib.infra.malloc_tuning import cap_malloc_arenas

cap_malloc_arenas()

from yolomux_lib.infra.root_paths import YolomuxRootError

# This must happen before importing yolomux_lib: common.py resolves every mutable
# root at import time. The normal CLI parser later asserts the same port.
_resolver_spec = spec_from_file_location("yolomux_instance_isolation", Path(__file__).with_name("tools") / "instance_isolation.py")
if _resolver_spec is None or _resolver_spec.loader is None:
    raise RuntimeError("cannot load YOLOmux instance-isolation resolver")
_resolver = module_from_spec(_resolver_spec)
sys.modules[_resolver_spec.name] = _resolver
_resolver_spec.loader.exec_module(_resolver)
try:
    _resolver.apply_early_instance_environment(sys.argv[1:])
except RuntimeError as _error:
    print(f"ERROR: {_error}", file=sys.stderr)
    raise SystemExit(2) from _error
del _resolver
del _resolver_spec

try:
    from yolomux_lib.cli import main
except YolomuxRootError as error:
    print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(2) from error


if __name__ == "__main__":
    raise SystemExit(main())
