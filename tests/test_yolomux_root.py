import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from yolomux_lib.infra import common
from yolomux_lib.local_services import rpc


def _identity() -> common.HostIdentity:
    return common.HostIdentity("host-a", "host-a.example", "boot-a", 42, "proc:1", 1, "nonce-a", "fixture")


def _rooted_subprocess_environment(root: Path) -> dict[str, str]:
    """Run the import with only the root contract, not pytest's fixture roots."""

    values = dict(os.environ)
    for key in (
        "YOLOMUX_CONFIG_DIR",
        "YOLOMUX_STATE_DIR",
        "YOLOMUX_CACHE_DIR",
        "YOLOMUX_CODEX_HOME",
        "YOLOMUX_RUNTIME_DIR",
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "CODEX_HOME",
    ):
        values.pop(key, None)
    values["YOLOMUX_ROOT"] = str(root)
    return values


def test_rooted_subprocess_isolates_instance_state_but_reuses_user_codex_credentials():
    """Exercise the actual import path under both Docker and host pytest routes."""

    # The test container's pytest temp root is deliberately very deep.  A
    # compact /tmp root is the practical container/debugging shape which must
    # fit the Unix-socket budget; the deep-root rejection has its own test.
    root = Path("/tmp/yolomux-root-7771")
    probe = """
import json
from pathlib import Path
from yolomux_lib.infra import common
paths = common._YOLOMUX_ROOTS
longest = max(common.runtime_socket_candidates(paths), key=lambda value: len(str(value).encode()))
print(json.dumps({
    'home': str(Path.home()),
    'root': str(paths.root),
    'config': str(paths.config_dir),
    'state': str(paths.state_dir),
    'cache': str(paths.cache_dir),
    'codex': str(paths.codex_home),
    'runtime': str(paths.runtime_dir),
    'longest_socket': str(longest),
    'socket_bytes': len(str(longest).encode()),
    'socket_budget': common.LOCAL_RPC_SOCKET_PATH_BYTES,
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        env=_rooted_subprocess_environment(root),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["root"] == str(root)
    assert {
        "config": payload["config"],
        "state": payload["state"],
        "cache": payload["cache"],
        "codex": payload["codex"],
        "runtime": payload["runtime"],
    } == {
        "config": str(root / "config"),
        "state": str(root / "state"),
        "cache": str(root / "cache"),
        "codex": str(Path.home() / ".codex"),
        "runtime": str(root / "runtime"),
    }
    assert all(
        Path(payload[name]).is_relative_to(root)
        for name in ("config", "state", "cache", "runtime", "longest_socket")
    )
    assert not Path(payload["home"]).is_relative_to(root)
    assert payload["socket_bytes"] <= payload["socket_budget"], payload


def test_root_isolates_instance_state_but_not_user_owned_codex_credentials(tmp_path: Path):
    root = Path("/tmp/yr")
    paths = common.resolve_yolomux_roots(
        {"YOLOMUX_ROOT": str(root), "XDG_CACHE_HOME": "/outside/cache", "CODEX_HOME": "/outside/codex"},
        identity=_identity(),
    )

    assert paths.config_dir == root / "config"
    assert paths.state_dir == root / "state"
    assert paths.cache_dir == root / "cache"
    assert paths.codex_home == Path.home() / ".codex"
    assert paths.runtime_dir.is_relative_to(root / "runtime")
    assert all(path.is_relative_to(root) for path in paths.writable_paths())


@pytest.mark.parametrize(
    ("key", "value"),
    (("YOLOMUX_CONFIG_DIR", "/outside/config"), ("YOLOMUX_RUNTIME_DIR", "/outside/runtime"), ("YOLOMUX_CODEX_HOME", "/outside/codex")),
)
def test_root_refuses_each_outside_individual_override(tmp_path: Path, key: str, value: str):
    with pytest.raises(ValueError, match=rf"{key}.*{value}"):
        common.resolve_yolomux_roots({"YOLOMUX_ROOT": str(tmp_path / "root"), key: value}, identity=_identity())


def test_root_unset_preserves_existing_default_paths(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("YOLOMUX_ROOT", raising=False)
    paths = common.resolve_yolomux_roots({}, identity=_identity(), temporary_dir=tmp_path / "tmp", uid=1000)

    assert paths.config_dir == Path.home() / ".config" / "yolomux"
    assert paths.state_dir == Path.home() / ".local" / "state" / "yolomux"
    assert paths.cache_dir == Path.home() / ".cache" / "yolomux"
    assert paths.codex_home == Path.home() / ".codex"
    assert paths.runtime_dir == tmp_path / "tmp" / "yolomux-1000" / "h-host-a" / "b-boot-a"


def test_deep_root_fails_socket_budget_before_creating_a_directory(tmp_path: Path):
    root = tmp_path / ("x" * 70)

    with pytest.raises(ValueError, match="YOLOMUX_ROOT.*shorter"):
        common.resolve_yolomux_roots({"YOLOMUX_ROOT": str(root)}, identity=_identity())

    assert not root.exists()


def test_socket_budget_accepts_a_realistic_development_root():
    root = Path("/home/keivenc/dev/yolomux-verify-7771")

    paths = common.resolve_yolomux_roots({"YOLOMUX_ROOT": str(root)}, identity=_identity())

    longest = max(common.runtime_socket_candidates(paths, identity=_identity()), key=lambda path: len(str(path).encode()))
    assert len(str(longest).encode()) <= common.LOCAL_RPC_SOCKET_PATH_BYTES
    assert rpc.safe_socket_path(longest, prefix="yolomux-root-test") == longest


def test_rooted_import_refuses_an_outside_config_before_creating_the_root(tmp_path: Path):
    root = tmp_path / "root"
    env = {**os.environ, "YOLOMUX_ROOT": str(root), "YOLOMUX_CONFIG_DIR": "/outside/config"}
    result = subprocess.run(
        [sys.executable, "-c", "import yolomux_lib.infra.common"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "YOLOMUX_CONFIG_DIR" in result.stderr
    assert "/outside/config" in result.stderr
    assert not root.exists()


def test_cli_reports_outside_codex_override_without_a_traceback(tmp_path: Path):
    root = tmp_path / "root"
    env = {**os.environ, "YOLOMUX_ROOT": str(root), "YOLOMUX_CODEX_HOME": "/outside/codex"}
    for key in ("YOLOMUX_CONFIG_DIR", "YOLOMUX_STATE_DIR", "YOLOMUX_CACHE_DIR", "YOLOMUX_RUNTIME_DIR"):
        env.pop(key, None)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "yolomux.py"), "--print-background-owner"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "ERROR: YOLOMUX_CODEX_HOME resolves outside YOLOMUX_ROOT: /outside/codex" in result.stderr
    assert "Traceback" not in result.stderr
    assert not root.exists()


def test_socket_budget_enumerates_product_candidates_not_a_guessed_suffix(tmp_path: Path):
    paths = common.resolve_yolomux_roots({"YOLOMUX_ROOT": "/tmp/yr"}, identity=_identity())
    candidates = common.runtime_socket_candidates(paths)

    assert {path.name for path in candidates} >= {"statusd.sock", "jobd.sock", "watchd.sock", "approvald.sock", "indexer.sock"}
    assert any(path.name.startswith("statsd.p24s7.") for path in candidates)
    assert max(candidates, key=lambda path: len(str(path).encode())) in candidates
