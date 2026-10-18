"""Native subprocess fixtures sharing the gate's existing ownership primitives."""
from pathlib import Path

import pytest

from tests import isolated_dev_server as native_dev_server
from tests.gate_harness import gate_auth_credentials, gate_runtime_paths, gate_tmux
from yomux_lib import auth as auth_module

@pytest.fixture
def gate_live_server(monkeypatch, gate_runtime_paths, gate_tmux):
    paths = native_dev_server.build_paths(gate_runtime_paths.root / "native-server")
    runtime = native_dev_server.start_isolated_dev_server("native gate", Path(__file__).resolve().parents[1], paths, gate_tmux)
    try:
        yield runtime
    finally:
        runtime.stop()


@pytest.fixture
def gate_authenticated_live_server(monkeypatch, gate_runtime_paths, gate_tmux, gate_auth_credentials):
    paths = native_dev_server.build_paths(gate_runtime_paths.root / "native-authenticated")
    (paths.config_dir / "auth.yaml").write_text(auth_module.auth_config_text((auth_module.AuthUser(username=gate_auth_credentials.username, password=gate_auth_credentials.password, role=gate_auth_credentials.role),)))
    runtime = native_dev_server.start_isolated_dev_server("native authenticated gate", Path(__file__).resolve().parents[1], paths, gate_tmux, auth_bypass=False)
    try:
        yield runtime
    finally:
        runtime.stop()


@pytest.fixture
def stateful_journey(gate_live_server):
    return gate_live_server
