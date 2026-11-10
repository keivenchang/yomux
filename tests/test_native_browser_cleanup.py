# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Failure propagation through the native browser journeys' cleanup owners."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.gate_harness import run_fixture_cleanup_phases


@pytest.mark.parametrize("module, consumer", [
    ("test_browser_native_filesystem.py", "test_native_filesystem_events_refresh_visible_finder_and_hidden_finder_editor"),
    ("test_browser_native_tls.py", "test_native_tls_multiplexes_authenticated_streams_and_keeps_websocket"),
])
def test_native_browser_cleanup_stops_server_after_browser_failure(module, consumer):
    path = Path(__file__).with_name(module)
    source = ast.parse(path.read_text())
    target = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == consumer)
    boundary = next(node for node in target.body if isinstance(node, ast.Try) and node.finalbody)
    calls = []
    failure = RuntimeError("fixture browser retirement failed")

    def fail_browser(*_args):
        calls.append("browser")
        raise failure

    scope = {
        "browser": SimpleNamespace(get=fail_browser, execute_script=fail_browser),
        "server": SimpleNamespace(stop=lambda: calls.append("server")),
        "run_fixture_cleanup_phases": run_fixture_cleanup_phases,
    }
    with pytest.raises(RuntimeError) as raised:
        exec(compile(ast.Module(body=boundary.finalbody, type_ignores=[]), str(path), "exec"), scope)
    assert raised.value is failure
    assert calls == ["browser", "server"]
