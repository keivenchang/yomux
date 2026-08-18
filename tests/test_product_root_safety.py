# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Cross-boundary safety contracts for explicit YOLOmux product roots."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools import system_status_latency_probe as probe
from tools.instance_isolation import resolve_instance_environment
from yolomux_lib.agent_comms import codex_app_server
from yolomux_lib.infra import common
from yolomux_lib.infra import worktree_writer


ROOT = Path(__file__).resolve().parents[1]
STARTUP_COMMON = ROOT / "tools" / "startup_common.sh"
PRODUCT_ROOT_KEYS = (
    "YOLOMUX_CONFIG_DIR",
    "YOLOMUX_STATE_DIR",
    "YOLOMUX_CACHE_DIR",
    "YOLOMUX_CODEX_HOME",
    "YOLOMUX_RUNTIME_DIR",
)
CONFIGURED_PATH_KEYS = (
    "HOME",
    "YOLOMUX_ROOT",
    *PRODUCT_ROOT_KEYS,
    "CODEX_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
    "YOLOMUX_HOST_ARTIFACT_DIR",
    "YOLOMUX_START_LOCK_DIR",
    "YOLOMUX_LOG_DIR",
    "YOLOMUX_CA_DIR",
    "YOLOMUX_TOOL_LOCK_PATH",
    "PYTHONPYCACHEPREFIX",
    "TMPDIR",
)


def _identity() -> common.HostIdentity:
    return common.HostIdentity("host-a", "host-a.example", "boot-a", 42, "proc:1", 1, "nonce-a", "fixture")


@pytest.mark.parametrize("key", PRODUCT_ROOT_KEYS)
@pytest.mark.parametrize("rooted", (False, True))
def test_relative_product_root_is_refused_with_or_without_yolomux_root(
    monkeypatch,
    tmp_path: Path,
    key: str,
    rooted: bool,
) -> None:
    cwd = Path("/tmp") / f"yr{os.getpid()}"
    cwd.mkdir(exist_ok=True)
    monkeypatch.chdir(cwd)
    relative_value = f"relative-{key.lower()}"
    values = {key: relative_value}
    if rooted:
        values["YOLOMUX_ROOT"] = str(cwd)

    with pytest.raises(ValueError, match=rf"{key}.*absolute.*{relative_value}"):
        common.resolve_yolomux_roots(values, identity=_identity(), temporary_dir=tmp_path / "tmp", uid=1000)


def test_relative_yolomux_root_is_refused_before_cwd_can_choose_its_location(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match=r"YOLOMUX_ROOT.*absolute.*relative-root"):
        common.resolve_yolomux_roots({"YOLOMUX_ROOT": "relative-root"}, identity=_identity())


@pytest.mark.parametrize("key", CONFIGURED_PATH_KEYS)
def test_tilde_prefixed_configured_path_is_relative_and_refused(key: str) -> None:
    resolution = resolve_instance_environment(7770, {key: "~/unsafe-root"}, platform="Linux")

    assert key in resolution.error
    assert "absolute" in resolution.error
    assert resolution.environment == {}


@pytest.mark.parametrize("key", ("YOLOMUX_ROOT", *PRODUCT_ROOT_KEYS))
def test_explicit_home_directory_is_refused_as_a_product_root(key: str) -> None:
    with pytest.raises(ValueError, match=rf"{key}.*home directory"):
        common.resolve_yolomux_roots({key: str(Path.home())}, identity=_identity())


@pytest.mark.parametrize("key", ("YOLOMUX_ROOT", *PRODUCT_ROOT_KEYS))
def test_filesystem_root_is_refused_as_a_product_root(key: str) -> None:
    with pytest.raises(ValueError, match=rf"{key}.*filesystem root"):
        common.resolve_yolomux_roots({key: "/"}, identity=_identity())


@pytest.mark.parametrize(
    ("key", "attribute"),
    (
        ("YOLOMUX_CONFIG_DIR", "config_dir"),
        ("YOLOMUX_STATE_DIR", "state_dir"),
        ("YOLOMUX_CACHE_DIR", "cache_dir"),
        ("YOLOMUX_CODEX_HOME", "codex_home"),
        ("YOLOMUX_RUNTIME_DIR", "runtime_dir"),
    ),
)
def test_absolute_product_root_override_is_honored(tmp_path: Path, key: str, attribute: str) -> None:
    configured = tmp_path / key.lower()

    paths = common.resolve_yolomux_roots({key: str(configured)}, identity=_identity())

    actual = {
        "config_dir": paths.config_dir,
        "state_dir": paths.state_dir,
        "cache_dir": paths.cache_dir,
        "codex_home": paths.codex_home,
        "runtime_dir": paths.runtime_dir,
    }[attribute]
    if key == "YOLOMUX_RUNTIME_DIR":
        assert actual.is_relative_to(configured)
    else:
        assert actual == configured


def test_absolute_product_roots_are_independent_of_process_cwd(monkeypatch, tmp_path: Path) -> None:
    values = {
        "YOLOMUX_CONFIG_DIR": str(tmp_path / "config"),
        "YOLOMUX_STATE_DIR": str(tmp_path / "state"),
        "YOLOMUX_CACHE_DIR": str(tmp_path / "cache"),
        "YOLOMUX_CODEX_HOME": str(tmp_path / "codex"),
        "YOLOMUX_RUNTIME_DIR": str(tmp_path / "runtime"),
    }
    first_cwd = tmp_path / "first-cwd"
    second_cwd = tmp_path / "second-cwd"
    first_cwd.mkdir()
    second_cwd.mkdir()

    monkeypatch.chdir(first_cwd)
    first = common.resolve_yolomux_roots(values, identity=_identity())
    monkeypatch.chdir(second_cwd)
    second = common.resolve_yolomux_roots(values, identity=_identity())

    assert first == second


@pytest.mark.parametrize("key", PRODUCT_ROOT_KEYS)
def test_rooted_product_root_refuses_each_outside_override(tmp_path: Path, key: str) -> None:
    outside = Path("/outside") / key.lower()

    with pytest.raises(ValueError, match=rf"{key}.*{outside}"):
        common.resolve_yolomux_roots(
            {"YOLOMUX_ROOT": str(tmp_path / "root"), key: str(outside)},
            identity=_identity(),
        )


@pytest.mark.parametrize("relative_key", ("YOLOMUX_STATE_DIR", "YOLOMUX_RUNTIME_DIR"))
def test_cli_refuses_relative_product_roots_before_writing_to_cwd(tmp_path: Path, relative_key: str) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
        "YOLOMUX_CONFIG_DIR": str(tmp_path / "config"),
        "YOLOMUX_STATE_DIR": str(tmp_path / "state"),
        "YOLOMUX_CACHE_DIR": str(tmp_path / "cache"),
        "YOLOMUX_CODEX_HOME": str(tmp_path / "codex"),
        "YOLOMUX_RUNTIME_DIR": str(tmp_path / "runtime"),
    }
    env[relative_key] = "relative-product-root"
    env.pop("YOLOMUX_ROOT", None)

    result = subprocess.run(
        [sys.executable, str(ROOT / "yolomux.py"), "--print-background-owner"],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert relative_key in result.stderr
    assert "relative-product-root" in result.stderr
    assert "absolute" in result.stderr
    assert "Traceback" not in result.stderr
    assert list(cwd.iterdir()) == []


@pytest.mark.parametrize(
    ("key", "value", "message"),
    (
        ("YOLOMUX_ROOT", "relative-root", "absolute"),
        ("YOLOMUX_CONFIG_DIR", "relative-config", "absolute"),
        ("YOLOMUX_STATE_DIR", "relative-state", "absolute"),
        ("YOLOMUX_CACHE_DIR", "relative-cache", "absolute"),
        ("YOLOMUX_RUNTIME_DIR", "relative-runtime", "absolute"),
        ("YOLOMUX_CODEX_HOME", "relative-codex", "absolute"),
        ("CODEX_HOME", "relative-codex", "absolute"),
        ("XDG_CACHE_HOME", "relative-cache", "absolute"),
        ("XDG_RUNTIME_DIR", "relative-runtime", "absolute"),
        ("YOLOMUX_START_LOCK_DIR", "relative-lock", "absolute"),
        ("YOLOMUX_LOG_DIR", "relative-logs", "absolute"),
        ("YOLOMUX_CA_DIR", "relative-ca", "absolute"),
        ("YOLOMUX_TOOL_LOCK_PATH", "relative-tool.lock", "absolute"),
        ("HOME", "relative-home", "absolute"),
        ("YOLOMUX_ROOT", str(Path.home()), "home directory"),
        ("YOLOMUX_ROOT", "/", "filesystem root"),
    ),
)
def test_instance_resolution_rejects_unsafe_explicit_roots(key: str, value: str, message: str) -> None:
    resolution = resolve_instance_environment(7770, {key: value}, platform="Linux")

    assert key in resolution.error
    assert message in resolution.error
    assert resolution.environment == {}


def test_rooted_instance_ignores_ambient_codex_home() -> None:
    root = Path("/tmp") / f"yc{os.getpid()}"
    resolution = resolve_instance_environment(
        7771,
        {"YOLOMUX_ROOT": str(root), "CODEX_HOME": "relative-codex"},
        platform="Linux",
    )

    assert resolution.error == ""
    assert resolution.environment == {}
    paths = common.resolve_yolomux_roots(
        {"YOLOMUX_ROOT": str(root), "CODEX_HOME": "relative-codex"},
        identity=_identity(),
    )
    assert paths.codex_home == Path.home() / ".codex"


@pytest.mark.parametrize("rooted", (False, True))
def test_explicit_yolomux_codex_home_ignores_ambient_codex_home(tmp_path: Path, rooted: bool) -> None:
    root = Path("/tmp") / f"ye{os.getpid()}"
    explicit_codex_home = root / "codex" if rooted else tmp_path / "codex"
    values = {
        "YOLOMUX_CODEX_HOME": str(explicit_codex_home),
        "CODEX_HOME": "relative-ignored-codex",
    }
    if rooted:
        values["YOLOMUX_ROOT"] = str(root)

    resolution = resolve_instance_environment(7771 if rooted else 7770, values, platform="Linux")

    assert resolution.error == ""
    paths = common.resolve_yolomux_roots(values, identity=_identity())
    assert paths.codex_home == explicit_codex_home


def test_home_mapping_owns_defaults_and_exact_home_refusal(tmp_path: Path) -> None:
    home = tmp_path / "home"
    paths = common.resolve_yolomux_roots({"HOME": str(home)}, identity=_identity(), temporary_dir=tmp_path / "tmp")

    assert paths.config_dir == home / ".config" / "yolomux"
    assert paths.state_dir == home / ".local" / "state" / "yolomux"
    assert paths.codex_home == home / ".codex"
    with pytest.raises(ValueError, match=r"YOLOMUX_ROOT.*home directory"):
        common.resolve_yolomux_roots(
            {"HOME": str(home), "YOLOMUX_ROOT": str(home)},
            identity=_identity(),
        )


def test_cli_refuses_relative_home_before_default_roots_write_to_cwd(tmp_path: Path) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    env = {
        **os.environ,
        "HOME": "relative-home",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
    }
    for key in ("YOLOMUX_ROOT", *PRODUCT_ROOT_KEYS, "CODEX_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        env.pop(key, None)

    result = subprocess.run(
        [sys.executable, str(ROOT / "yolomux.py"), "--print-background-owner"],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "HOME" in result.stderr
    assert "absolute" in result.stderr
    assert "Traceback" not in result.stderr
    assert list(cwd.iterdir()) == []


def test_host_artifact_paths_refuse_relative_runtime_before_writing(tmp_path: Path) -> None:
    worktree = tmp_path / "shared" / "repo"
    worktree.mkdir(parents=True)

    with pytest.raises(ValueError, match=r"YOLOMUX_RUNTIME_DIR.*absolute.*relative-runtime"):
        worktree_writer.configure_host_local_artifacts(
            worktree,
            environ={"YOLOMUX_RUNTIME_DIR": "relative-runtime"},
            apply_process=False,
        )

    assert not (tmp_path / "relative-runtime").exists()


def test_rooted_host_artifacts_ignore_ambient_xdg_runtime(tmp_path: Path) -> None:
    root = tmp_path / "root"
    paths = worktree_writer.host_artifact_paths(
        tmp_path / "worktree",
        environ={"YOLOMUX_ROOT": str(root), "XDG_RUNTIME_DIR": str(tmp_path / "outside")},
    )

    assert paths.root.is_relative_to(root / "runtime")

    custom_runtime = root / "custom-runtime"
    custom = worktree_writer.host_artifact_paths(
        tmp_path / "worktree",
        environ={"YOLOMUX_ROOT": str(root), "YOLOMUX_RUNTIME_DIR": str(custom_runtime)},
    )
    assert custom.root.is_relative_to(custom_runtime)


def test_shared_resolver_and_codex_adapter_refuse_worktree_product_roots() -> None:
    unsafe_root = ROOT / ".unsafe-product-root-test"

    with pytest.raises(ValueError, match="shared worktree"):
        common.resolve_yolomux_roots({"YOLOMUX_ROOT": str(unsafe_root)}, identity=_identity())
    with pytest.raises(ValueError, match="shared worktree"):
        common.codex_runtime_env(
            {
                "YOLOMUX_ROOT": str(unsafe_root),
                "YOLOMUX_CODEX_HOME": str(unsafe_root / "codex"),
            }
        )

    assert not unsafe_root.exists()


@pytest.mark.parametrize("key", ("YOLOMUX_HOST_ARTIFACT_DIR", "PYTHONPYCACHEPREFIX"))
def test_rooted_package_import_refuses_outside_artifact_overrides_before_writing(tmp_path: Path, key: str) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    env = dict(os.environ)
    for configured_key in (*CONFIGURED_PATH_KEYS, "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        env.pop(configured_key, None)
    env.update({
        "HOME": str(tmp_path / "home"),
        "PYTHONPATH": str(ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "YOLOMUX_ROOT": str(root),
        key: str(outside),
    })

    result = subprocess.run(
        [sys.executable, "-c", "import yolomux_lib"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert key in result.stderr
    assert not root.exists()
    assert not outside.exists()


@pytest.mark.parametrize("prefix_kind", ("relative", "filesystem-root", "home"))
def test_python_cache_prefix_refuses_unsafe_roots_before_writing(
    monkeypatch,
    tmp_path: Path,
    prefix_kind: str,
) -> None:
    cwd = tmp_path / "cwd"
    worktree = tmp_path / "worktree"
    home = tmp_path / "home"
    cwd.mkdir()
    worktree.mkdir()
    monkeypatch.chdir(cwd)
    prefix = {"relative": "relative-pycache", "filesystem-root": "/", "home": str(home)}[prefix_kind]
    values = {
        "HOME": str(home),
        "YOLOMUX_HOST_ARTIFACT_DIR": str(tmp_path / "artifacts"),
        "PYTHONPYCACHEPREFIX": prefix,
    }

    with pytest.raises(ValueError, match=r"PYTHONPYCACHEPREFIX"):
        worktree_writer.configure_host_local_artifacts(worktree, environ=values, apply_process=True)

    assert not (cwd / "relative-pycache").exists()


@pytest.mark.parametrize("key", ("YOLOMUX_CODEX_HOME", "CODEX_HOME"))
def test_codex_runtime_adapters_refuse_relative_home_without_creating_it(monkeypatch, tmp_path: Path, key: str) -> None:
    monkeypatch.chdir(tmp_path)
    base_env = {
        "PATH": "/usr/bin",
        "YOLOMUX_CONFIG_DIR": str(tmp_path / "config"),
        "YOLOMUX_STATE_DIR": str(tmp_path / "state"),
        "YOLOMUX_CACHE_DIR": str(tmp_path / "cache"),
        "YOLOMUX_RUNTIME_DIR": str(tmp_path / "runtime"),
        key: "relative-codex",
    }
    if key == "CODEX_HOME":
        base_env["YOLOMUX_CODEX_HOME"] = ""

    with pytest.raises(ValueError, match=rf"{key}.*absolute.*relative-codex"):
        common.codex_runtime_env(base_env)
    with pytest.raises(ValueError, match=rf"{key}.*absolute.*relative-codex"):
        codex_app_server.codex_runtime_env(base_env)

    assert not (tmp_path / "relative-codex").exists()


def test_config_root_rejects_relative_process_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match=r"YOLOMUX_CONFIG_DIR.*absolute"):
        probe.config_dir_from_process({"YOLOMUX_CONFIG_DIR": "relative-config"})


def test_shared_start_lock_rejects_unsafe_root_before_writing(tmp_path: Path) -> None:
    for configured_root in ("relative-root", str(tmp_path / "home")):
        cwd = tmp_path / ("relative" if configured_root == "relative-root" else "home-case")
        cwd.mkdir()
        env = {
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "YOLOMUX_ROOT": configured_root,
        }
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; repo_root="$2"; python_bin="$3"; yolomux_acquire_start_lock',
                "startup-lock-invalid-root",
                str(STARTUP_COMMON),
                str(ROOT),
                sys.executable,
            ],
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
        )

        assert result.returncode != 0
        assert "startup root validation failed before lock acquisition" in result.stderr
        assert list(cwd.iterdir()) == []
        assert not (tmp_path / "home" / "cache" / "start.lock").exists()


@pytest.mark.parametrize("unsafe_kind", ("relative", "outside", "deep", "outside-and-deep"))
def test_boot_rejects_unsafe_root_before_stopping_existing_listener(tmp_path: Path, unsafe_kind: str) -> None:
    listener = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import socket; server = socket.socket(); server.bind(('127.0.0.1', 0)); "
            "server.listen(); print(server.getsockname()[1], flush=True); server.accept()",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert listener.stdout is not None
        port = int(listener.stdout.readline().strip())
        env = {
            **os.environ,
            "YOLOMUX_START_LOAD_WAIT_SECONDS": "30",
        }
        for configured_key in (*CONFIGURED_PATH_KEYS, "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
            if configured_key != "HOME":
                env.pop(configured_key, None)
        if unsafe_kind == "relative":
            env["YOLOMUX_ROOT"] = "relative-root"
        elif unsafe_kind == "outside":
            env["YOLOMUX_ROOT"] = str(tmp_path / "root")
            env["YOLOMUX_CONFIG_DIR"] = str(tmp_path / "outside")
        elif unsafe_kind == "deep":
            env["YOLOMUX_ROOT"] = str(tmp_path / ("x" * 100))
        else:
            env["YOLOMUX_ROOT"] = str(tmp_path / ("x" * 100))
            env["YOLOMUX_CONFIG_DIR"] = str(tmp_path / "outside")

        result = subprocess.run(
            [str(ROOT / "boot.sh"), str(port)],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
        )

        assert result.returncode != 0
        assert "startup root validation failed" in result.stderr
        if unsafe_kind == "outside-and-deep":
            assert "YOLOMUX_CONFIG_DIR resolves outside YOLOMUX_ROOT" in result.stderr
            assert "too deep for product socket" not in result.stderr
        assert listener.poll() is None
        assert list(tmp_path.iterdir()) == []
    finally:
        listener.terminate()
        listener.wait(timeout=5)


def test_boot_rejects_relative_log_dir_before_stopping_existing_listener(tmp_path: Path) -> None:
    listener = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import socket; server = socket.socket(); server.bind(('127.0.0.1', 0)); "
            "server.listen(); print(server.getsockname()[1], flush=True); server.accept()",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert listener.stdout is not None
        port = int(listener.stdout.readline().strip())
        result = subprocess.run(
            [str(ROOT / "boot.sh"), "--log-dir", "relative-logs", str(port)],
            cwd=tmp_path,
            env={**os.environ, "YOLOMUX_START_LOAD_WAIT_SECONDS": "30"},
            text=True,
            capture_output=True,
        )

        assert result.returncode != 0
        assert "YOLOMUX_LOG_DIR" in result.stderr
        assert "absolute" in result.stderr
        assert listener.poll() is None
        assert list(tmp_path.iterdir()) == []
    finally:
        listener.terminate()
        listener.wait(timeout=5)


def test_boot_checks_absolute_log_sink_before_stopping_existing_listener(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("block\n", encoding="utf-8")
    listener = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import socket; server = socket.socket(); server.bind(('127.0.0.1', 0)); "
            "server.listen(); print(server.getsockname()[1], flush=True); server.accept()",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert listener.stdout is not None
        port = int(listener.stdout.readline().strip())
        result = subprocess.run(
            [str(ROOT / "boot.sh"), "--log-dir", str(blocker / "logs"), str(port)],
            cwd=tmp_path,
            env={
                **os.environ,
                "YOLOMUX_START_LOAD_DISCOUNT_CORES": "999",
                "YOLOMUX_START_LOAD_WAIT_SECONDS": "30",
            },
            text=True,
            capture_output=True,
        )

        assert result.returncode != 0
        assert "log path is not writable" in result.stderr
        assert listener.poll() is None
        assert blocker.read_text(encoding="utf-8") == "block\n"
    finally:
        listener.terminate()
        listener.wait(timeout=5)


def test_boot_rejects_tmpdir_inside_checkout_before_listener_or_worktree_mutation(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    tools_dir = checkout / "tools"
    tools_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "boot.sh", checkout / "boot.sh")
    shutil.copy2(ROOT / "tools" / "startup_common.sh", tools_dir / "startup_common.sh")
    shutil.copy2(ROOT / "tools" / "instance_isolation.py", tools_dir / "instance_isolation.py")
    before = sorted(path.relative_to(checkout) for path in checkout.rglob("*"))
    listener = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import socket; server = socket.socket(); server.bind(('127.0.0.1', 0)); "
            "server.listen(); print(server.getsockname()[1], flush=True); server.accept()",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert listener.stdout is not None
        port = int(listener.stdout.readline().strip())
        result = subprocess.run(
            [str(checkout / "boot.sh"), str(port)],
            cwd=checkout,
            env={
                **os.environ,
                "HOME": str(tmp_path / "home"),
                "TMPDIR": str(checkout),
                "YOLOMUX_START_LOAD_DISCOUNT_CORES": "999",
                "YOLOMUX_START_LOAD_WAIT_SECONDS": "30",
            },
            text=True,
            capture_output=True,
        )

        assert result.returncode != 0
        assert "TMPDIR cannot resolve inside the shared worktree" in result.stderr
        assert listener.poll() is None
        assert sorted(path.relative_to(checkout) for path in checkout.rglob("*")) == before
    finally:
        listener.terminate()
        listener.wait(timeout=5)


@pytest.mark.parametrize(
    ("option", "key", "value"),
    (
        ("--state-dir", "YOLOMUX_STATE_DIR", "relative-state"),
        ("--ca-dir", "YOLOMUX_CA_DIR", "relative-ca"),
    ),
)
def test_setup_tls_dry_run_rejects_relative_output_roots_before_writing(
    tmp_path: Path,
    option: str,
    key: str,
    value: str,
) -> None:
    state_dir = str(tmp_path / "state")
    ca_dir = str(tmp_path / "ca")
    arguments = [str(ROOT / "tools" / "setup-tls.sh"), "--dry-run", "--state-dir", state_dir, "--ca-dir", ca_dir]
    arguments[arguments.index(option) + 1] = value

    result = subprocess.run(arguments, cwd=tmp_path, text=True, capture_output=True)

    assert result.returncode != 0
    assert key in result.stderr
    assert "absolute" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_setup_tls_rejects_relative_environment_ca_before_default_state_import(tmp_path: Path) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "YOLOMUX_CA_DIR": "relative-ca",
        "YOLOMUX_RUNTIME_DIR": str(tmp_path / "runtime"),
    }

    result = subprocess.run(
        [str(ROOT / "tools" / "setup-tls.sh"), "--dry-run"],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "YOLOMUX_CA_DIR" in result.stderr
    assert "absolute" in result.stderr
    assert not (tmp_path / "runtime").exists()


def test_setup_tls_rejects_relative_environment_state_before_product_import(tmp_path: Path) -> None:
    home = tmp_path / "home"
    env = {
        **os.environ,
        "HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
        "YOLOMUX_STATE_DIR": "relative-state",
        "YOLOMUX_HOST_ARTIFACT_DIR": str(tmp_path / "artifacts"),
    }

    result = subprocess.run(
        [str(ROOT / "tools" / "setup-tls.sh"), "--dry-run"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "YOLOMUX_STATE_DIR" in result.stderr
    assert not home.exists()


def test_setup_tls_rooted_dry_run_refuses_outside_cli_state_without_writing(tmp_path: Path) -> None:
    root = Path("/tmp") / ("x" * 100)
    outside = tmp_path / "outside"
    env = dict(os.environ)
    for key in ("YOLOMUX_ROOT", *PRODUCT_ROOT_KEYS, "CODEX_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "PYTHONPYCACHEPREFIX", "TMPDIR"):
        env.pop(key, None)
    env.update({"YOLOMUX_ROOT": str(root), "PYTHONDONTWRITEBYTECODE": "1"})
    result = subprocess.run(
        [str(ROOT / "tools" / "setup-tls.sh"), "--dry-run", "--state-dir", str(outside)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "YOLOMUX_STATE_DIR resolves outside YOLOMUX_ROOT" in result.stderr
    assert not root.exists()
    assert not outside.exists()


def test_setup_tls_default_dry_run_writes_no_product_state(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
        "YOLOMUX_HOST_ARTIFACT_DIR": str(tmp_path / "artifacts"),
    }
    for key in ("YOLOMUX_ROOT", *PRODUCT_ROOT_KEYS, "CODEX_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        env.pop(key, None)

    result = subprocess.run(
        [str(ROOT / "tools" / "setup-tls.sh"), "--dry-run"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert list(home.iterdir()) == []
    assert not (tmp_path / "artifacts").exists()


def test_check_refuses_relative_tool_lock_before_writing(tmp_path: Path) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "YOLOMUX_TOOL_LOCK_PATH": "relative-tool.lock",
        "YOLOMUX_RUNTIME_DIR": str(tmp_path / "runtime"),
    }
    env.pop("PYTHONPYCACHEPREFIX", None)

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check.py"), "--lane", "static"],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "YOLOMUX_TOOL_LOCK_PATH" in result.stderr
    assert "absolute" in result.stderr
    assert not (cwd / "relative-tool.lock").exists()
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("unsafe_kind", ("relative", "outside", "deep"))
def test_check_refuses_unsafe_root_environment_before_package_writes(tmp_path: Path, unsafe_kind: str) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if unsafe_kind == "relative":
        env["YOLOMUX_STATE_DIR"] = "relative-state"
        env["YOLOMUX_HOST_ARTIFACT_DIR"] = str(tmp_path / "artifacts")
    elif unsafe_kind == "outside":
        env["YOLOMUX_ROOT"] = str(tmp_path / "root")
        env["YOLOMUX_CONFIG_DIR"] = str(tmp_path / "outside")
    else:
        env["YOLOMUX_ROOT"] = str(tmp_path / ("x" * 100))

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check.py"), "--lane", "static"],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "CHECK REFUSED" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "artifacts").exists()
    assert not (tmp_path / "root").exists()
