import os
import subprocess
import sys
from pathlib import Path

from yolomux_lib import cli
from tools.instance_isolation import EARLY_PORT_ENV
from tools.instance_isolation import INSTANCE_ENV
from tools.instance_isolation import MANAGED_INSTANCE_PORT_ENV
from tools.instance_isolation import YOLOMUX_ROOT_ENV
from tools.instance_isolation import InstanceIdentity
from tools.instance_isolation import RowPlan
from tools.instance_isolation import apply_row_plan
from tools.instance_isolation import assert_early_port
from tools.instance_isolation import clean_row_environment
from tools.instance_isolation import is_managed_instance_port
from tools.instance_isolation import parse_instance
from tools.instance_isolation import resolve_instance_environment
from tools.instance_isolation import resolve_row_plan
from tools.instance_isolation import scan_port
from yolomux_lib.infra.root_paths import YolomuxRoots


def test_one_instance_descriptor_owns_port_and_managed_capability(tmp_path: Path):
    """W1: one typed descriptor replaces the three drifting same-valued env vars."""
    res = resolve_instance_environment(7771, {}, platform="Linux", tempdir=tmp_path / "tmp")
    assert not res.error
    # exactly one identity carrier; the two separate drift vars are gone
    assert INSTANCE_ENV in res.environment
    assert EARLY_PORT_ENV not in res.environment
    assert MANAGED_INSTANCE_PORT_ENV not in res.environment
    assert parse_instance(res.environment) == InstanceIdentity(port=7771, managed=True)
    # readers work off the one descriptor
    assert is_managed_instance_port(7771, res.environment)
    assert is_managed_instance_port(7772, res.environment) is False
    # early-vs-parsed guard reads the descriptor's port
    try:
        assert_early_port(7772, res.environment)
    except RuntimeError as error:
        assert "disagrees" in str(error)
    else:
        raise AssertionError("expected early-vs-parsed refusal from the descriptor")
    # a caller-set root still never grants managed capability (no descriptor emitted)
    assert is_managed_instance_port(7771, {YOLOMUX_ROOT_ENV: str(tmp_path / "root")}) is False


def test_clean_row_environment_strips_inherited_and_resolves_the_row(tmp_path: Path):
    """W1: the launcher builds one clean child env per row - inherited YOLOmux
    root/instance vars (from a parent shell that belongs to another instance) are
    stripped, then the row is resolved fresh; the parent env is not mutated."""
    contaminated = {
        "PATH": "/usr/bin",
        YOLOMUX_ROOT_ENV: "/tmp/foreign/p9999",
        INSTANCE_ENV: "9999:managed",
        "YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT": "9999",
        "YOLOMUX_STATE_DIR": "/tmp/foreign/state",
    }
    child = clean_row_environment(7771, contaminated, platform="Linux", tempdir=tmp_path / "tmp")

    # unrelated inherited vars survive
    assert child["PATH"] == "/usr/bin"
    # the foreign row is gone; this row resolved fresh to its own private root
    assert parse_instance(child) == InstanceIdentity(port=7771, managed=True)
    assert child[YOLOMUX_ROOT_ENV].endswith("/p7771")
    assert "foreign" not in child[YOLOMUX_ROOT_ENV]
    assert "YOLOMUX_STATE_DIR" not in child
    # the parent environment is left untouched
    assert contaminated[YOLOMUX_ROOT_ENV] == "/tmp/foreign/p9999"
    assert contaminated[INSTANCE_ENV] == "9999:managed"


def test_row_plan_resolves_once_serializes_and_applies_without_secrets(tmp_path: Path):
    """W1: one row plan (unset+assign, no inherited values/secrets) resolved once,
    serialized as bounded JSON, and applied to a clean copy of any environment."""
    plan = resolve_row_plan(7771, {}, platform="Linux", tempdir=tmp_path / "tmp")
    # the plan carries only roots/ports/identity - never inherited values
    assert INSTANCE_ENV in plan.assign
    assert plan.assign[INSTANCE_ENV] == "7771:managed"
    assert YOLOMUX_ROOT_ENV in plan.assign
    assert YOLOMUX_ROOT_ENV in plan.unset and INSTANCE_ENV in plan.unset

    # round-trips through bounded JSON unchanged
    assert RowPlan.from_json(plan.to_json()) == plan

    # applying it strips inherited instance/root vars and overlays the resolved row
    contaminated = {"PATH": "/usr/bin", YOLOMUX_ROOT_ENV: "/tmp/foreign/p9999", INSTANCE_ENV: "9999:managed"}
    child = apply_row_plan(plan, contaminated)
    assert child["PATH"] == "/usr/bin"
    assert child[YOLOMUX_ROOT_ENV] == plan.assign[YOLOMUX_ROOT_ENV]
    assert child[INSTANCE_ENV] == "7771:managed"
    # parent left untouched
    assert contaminated[YOLOMUX_ROOT_ENV] == "/tmp/foreign/p9999"

    # a default (production) port carries no managed assignment
    default_plan = resolve_row_plan(7770, {}, platform="Linux")
    assert default_plan.assign == {}


def test_exec_mode_strips_inherited_and_applies_the_row_before_the_command(tmp_path: Path):
    """W1: the exec mode applies a resolved plan (strip inherited, overlay row) to
    a copy of the environment and runs the command with that clean environment."""
    plan = resolve_row_plan(7771, {}, platform="Linux", tempdir=tmp_path / "tmp")
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(plan.to_json())

    contaminated = {
        **os.environ,
        YOLOMUX_ROOT_ENV: "/tmp/foreign/p9999",
        INSTANCE_ENV: "9999:managed",
    }
    result = subprocess.run(
        [sys.executable, "tools/instance_isolation.py", "exec", "--plan-file", str(plan_file), "--", "printenv", INSTANCE_ENV],
        capture_output=True, text=True, env=contaminated,
    )
    assert result.returncode == 0, result.stderr
    # the inherited foreign row was stripped and this row applied
    assert result.stdout.strip() == "7771:managed"


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
    # one identity descriptor, not two separate drift vars
    assert one.environment[INSTANCE_ENV] == "7771:managed"
    assert EARLY_PORT_ENV not in one.environment
    assert MANAGED_INSTANCE_PORT_ENV not in one.environment
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
    monkeypatch.setenv(INSTANCE_ENV, "7771:managed")
    try:
        assert_early_port(7772)
    except RuntimeError as error:
        assert "disagrees" in str(error)
    else:
        raise AssertionError("expected early-port mismatch refusal")
    monkeypatch.delenv(INSTANCE_ENV)
    assert_early_port(7772)


def test_cli_refuses_when_early_port_disagrees_with_argparse(monkeypatch, capsys):
    monkeypatch.setenv(INSTANCE_ENV, "7771:managed")
    monkeypatch.setattr(sys, "argv", ["yolomux.py", "--port", "7772", "--print-background-owner"])
    assert cli.main() == 2
    assert "early instance port 7771 disagrees with parsed --port 7772" in capsys.readouterr().err
