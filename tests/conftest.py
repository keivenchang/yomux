"""Shared pytest configuration + isolation for the yolomux test suite.

Point YOLOMUX_CONFIG_DIR / YOLOMUX_STATE_DIR at FRESH per-run temp dirs *before* any test module
imports `yolomux_lib.common` (which binds CONFIG_DIR / STATE_DIR / SETTINGS_PATH at import time). pytest
imports conftest.py ahead of the test modules, so this is the one place that owns the config/state
location — replacing the `os.environ.setdefault(..., "/tmp/yolomux-test-config")` lines that were
copy-pasted across ~11 test files, and ensuring no test (e.g. the login-locale picker, which writes
general.language) can leave a *persistent* shared config dir mutated across runs.
"""

import os
import tempfile

# setdefault so an explicit external override (CI, a developer) still wins; otherwise use a unique
# per-run temp dir that is naturally discarded between runs.
os.environ.setdefault("YOLOMUX_CONFIG_DIR", tempfile.mkdtemp(prefix="yolomux-test-config-"))
os.environ.setdefault("YOLOMUX_STATE_DIR", tempfile.mkdtemp(prefix="yolomux-test-state-"))
