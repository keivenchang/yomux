#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Canonical YOmux CLI entrypoint."""

from __future__ import annotations

import sys
from importlib.util import module_from_spec
from importlib.util import spec_from_file_location
from pathlib import Path


# The package installs the host-local prefix; suppress its own first cache until then.
sys.dont_write_bytecode = True

# This must happen before importing yomux_lib: common.py resolves every mutable
# root at import time and package initialization routes host-local artifacts. The
# normal CLI parser later asserts the same port.
_resolver_spec = spec_from_file_location("yolomux_instance_isolation", Path(__file__).with_name("tools") / "instance_isolation.py")
if _resolver_spec is None or _resolver_spec.loader is None:
    raise RuntimeError("cannot load YOmux instance-isolation resolver")
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

from yomux_lib.tmux.tmux_utils import use_machine_tmux_for_operator_launch

# Cap glibc malloc arenas before any allocation-heavy import or thread pool spawns,
# so a direct `python yomux.py` launch stays lean like a boot.sh launch does.
from yomux_lib.infra.malloc_tuning import cap_malloc_arenas

cap_malloc_arenas()

from yomux_lib.infra.root_paths import YolomuxRootError

try:
    from yomux_lib.cli import main as _cli_main
except YolomuxRootError as error:
    print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(2) from error


def main() -> int:
    """Run YOmux after selecting the machine-wide tmux server.

    The installed ``yomux`` console script calls this exported function
    directly, so normalization cannot live only in the ``__main__`` block.
    An explicit ``YOLOMUX_TMUX_SOCKET`` remains reserved for isolated
    fixtures and is preserved by the shared helper.
    """

    use_machine_tmux_for_operator_launch()
    return _cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
