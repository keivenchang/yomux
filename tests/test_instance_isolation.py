import os
import sys
from pathlib import Path

from yolomux_lib import cli
from tools.instance_isolation import EARLY_PORT_ENV
from tools.instance_isolation import MANAGED_INSTANCE_PORT_ENV
from tools.instance_isolation import YOLOMUX_ROOT_ENV
from tools.instance_isolation import assert_early_port
from tools.instance_isolation import is_managed_instance_port
from tools.instance_isolation import resolve_instance_environment
from tools.instance_isolation import scan_port
from yolomux_lib.infra.root_paths import YolomuxRoots


def test_scan_port_accepts_both_cli_spellings_and_ignores_values_after_double_dash():
    assert scan_port(["--port", "7771"]) == 7771
    assert scan_port(["--port=7771"]) == 7771
    assert scan_port(["--", "--port", "7771"]) is None
    assert scan_port(["--port", "nope"]) is None
    assert scan_port(["--port"]) is None
    assert scan_port(["--port=70000"]) is None


def test_nondefault_ports_receive_disjoint_single_roots(tmp_path: Path):
    one = resolve_instance_environment(7771, {}, platform="Linux", home=tmp_path / "home", tempdir=tmp_path / "tmp")
    two = resolve_instance_environment(7772, {}, platform="Linux", home=tmp_path / "home", tempdir=tmp_path / "tmp")
    assert not one.error and not two.error
    assert one.environment[YOLOMUX_ROOT_ENV] != two.environment[YOLOMUX_ROOT_ENV]
    assert one.environment[EARLY_PORT_ENV] == "7771"
    assert one.environment[MANAGED_INSTANCE_PORT_ENV] == "7771"
    assert is_managed_instance_port(7771, one.environment)


def test_caller_set_root_never_selects_the_managed_local_owner_adapter(tmp_path: Path):
    explicit = {YOLOMUX_ROOT_ENV: str(tmp_path / "root")}

    assert is_managed_instance_port(7771, explicit) is False


def test_legacy_default_and_explicit_root_are_quiet(tmp_path: Path):
    assert resolve_instance_environment(7770, {}, platform="Linux").environment == {}
    assert is_managed_instance_port(7770, {}) is False
    custom = {YOLOMUX_ROOT_ENV: str(tmp_path / "root")}
    assert resolve_instance_environment(7771, custom, platform="Linux").error == ""


def test_explicit_private_7771_root_needs_no_shared_flag():
    exact = {
        YOLOMUX_ROOT_ENV: str(Path.home() / "dev" / "yolomux-verify-7771"),
        "YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT": "7771",
    }
    assert resolve_instance_environment(7771, exact, platform="Linux").error == ""


def test_legacy_individual_overrides_are_left_to_the_product_resolver(tmp_path: Path):
    assert resolve_instance_environment(7771, {"YOLOMUX_STATE_DIR": str(tmp_path)}, platform="Linux").error == ""


def test_startup_path_line_names_auto_derived_root_and_resolved_paths(tmp_path: Path):
    resolution = resolve_instance_environment(17775, {}, platform="Linux", tempdir=tmp_path / "tmp")
    root = Path(resolution.environment[YOLOMUX_ROOT_ENV])
    paths = YolomuxRoots(root / "config", root / "state", root / "cache", root / "codex", root / "runtime", root)

    line = cli.startup_path_line(17775, environ=resolution.environment, roots=paths)

    assert line == (
        f"YOLOmux paths: YOLOMUX_ROOT={root} (auto-derived for non-default port 17775 because no root-family override was set); "
        f"config={root / 'config'}; state={root / 'state'}; cache={root / 'cache'}; runtime={root / 'runtime'}"
    )


def test_startup_path_line_names_explicit_root(tmp_path: Path):
    root = tmp_path / "explicit-root"
    paths = YolomuxRoots(root / "config", root / "state", root / "cache", root / "codex", root / "runtime", root)

    line = cli.startup_path_line(17775, environ={YOLOMUX_ROOT_ENV: str(root)}, roots=paths)

    assert f"YOLOMUX_ROOT={root} (explicit)" in line


def test_startup_path_line_explains_shared_config_runtime_launch(tmp_path: Path):
    config = tmp_path / "shared-config"
    state = tmp_path / "shared-state"
    cache = tmp_path / "shared-cache"
    runtime = tmp_path / "private-runtime"
    paths = YolomuxRoots(config, state, cache, tmp_path / "codex", runtime)
    environ = {
        "YOLOMUX_CONFIG_DIR": str(config),
        "YOLOMUX_RUNTIME_DIR": str(runtime),
    }

    line = cli.startup_path_line(17775, environ=environ, roots=paths)

    assert "YOLOMUX_ROOT=unset (auto-derivation skipped because YOLOMUX_CONFIG_DIR, YOLOMUX_RUNTIME_DIR were explicitly set)" in line
    assert f"config={config}; state={state}; cache={cache}; runtime={runtime}" in line


def test_early_port_mismatch_refuses(monkeypatch):
    monkeypatch.setenv(EARLY_PORT_ENV, "7771")
    try:
        assert_early_port(7772)
    except RuntimeError as error:
        assert "disagrees" in str(error)
    else:
        raise AssertionError("expected early-port mismatch refusal")
    monkeypatch.delenv(EARLY_PORT_ENV)
    assert_early_port(7772)


def test_cli_refuses_when_early_port_disagrees_with_argparse(monkeypatch, capsys):
    monkeypatch.setenv(EARLY_PORT_ENV, "7771")
    monkeypatch.setattr(sys, "argv", ["yolomux.py", "--port", "7772", "--print-background-owner"])
    assert cli.main() == 2
    assert "early instance port 7771 disagrees with parsed --port 7772" in capsys.readouterr().err
