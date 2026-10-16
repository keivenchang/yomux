#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Install fixture-owned state before a qualified native Bazel test."""

import os
from pathlib import Path
import platform
import pwd
import sys
import json
import subprocess
import signal
import tempfile

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.native_process_scope import NativeProcessScope, resource_coalition, TEST_OUTPUT_FILE_ENV_KEYS, action_writable_directories
from tools.tmux_paths import SHORT_TMPDIR_ENV, NATIVE_ACTION_TMPDIR, create_native_fixture_root
from tools.instance_isolation import private_directory
from tools.product_environment import product_env_name, clear_product_environment_assignments, apply_product_environment_assignments


def cancelled(number, _frame):
    raise SystemExit(128 + number)


def install_fixture_environment(system):
    if system == "Darwin":
        coalition = resource_coalition(os.getpid())
        if coalition is None or SHORT_TMPDIR_ENV not in os.environ:
            raise ValueError("native Darwin fixture requires admitted short base and kernel coalition")
        root = create_native_fixture_root(Path(os.environ[SHORT_TMPDIR_ENV]), coalition)
        os.environ[SHORT_TMPDIR_ENV] = str(root)
    else:
        # The action-private tmpfs keeps SQLite off the build storage queue.
        # Keep the copied repository outside /tmp so negative path fixtures
        # retain their premise, and keep HOME below the product socket budget.
        os.environ["TEST_TMPDIR"] = tempfile.mkdtemp(prefix="t", dir=NATIVE_ACTION_TMPDIR)
        root = Path(os.environ["TEST_TMPDIR"]) / "f"
    private_directory(root)
    state = {
        "HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data",
        "XDG_STATE_HOME": "state", "XDG_CACHE_HOME": "cache", "XDG_RUNTIME_DIR": "runtime",
        "CODEX_HOME": "codex", "CLAUDE_CONFIG_DIR": "claude", "TMPDIR": "tmp",
    }
    for key, relative in state.items():
        directory = root / relative
        private_directory(directory)
        os.environ[key] = str(directory)
    if system == "Darwin":
        # Chromium uses its own override before NSTemporaryDirectory on macOS.
        os.environ["MAC_CHROMIUM_TMPDIR"] = str(root / "tmp")
    # Package bootstrap can configure caches before the action fixture exists.
    # Let payload bootstrap derive them again from this fixture's HOME/XDG roots.
    clear_product_environment_assignments(os.environ, "WORKTREE_WRITER_TOKEN", "HOST_ARTIFACT_DIR",
                                          "GENERATED_PYTHONPYCACHEPREFIX")
    for key in ("PYTHONPYCACHEPREFIX", "PIP_CACHE_DIR", "NPM_CONFIG_CACHE", "COVERAGE_FILE"):
        os.environ.pop(key, None)
    # Keep evidence inside declared outputs so Bazel retains it after fixture cleanup.
    evidence = Path(os.environ.get("TEST_UNDECLARED_OUTPUTS_DIR") or root) / "evidence"
    private_directory(evidence)
    apply_product_environment_assignments(os.environ, {"E2E_EVIDENCE_DIR": str(evidence)})
    os.environ[product_env_name("WORKTREE_WRITER_SLOT")] = str(root / "writer")
    os.environ.update(YOMUX_CHECK_CONTAINER="0", YOLOMUX_CHECK_CONTAINER="0", PYTHONDONTWRITEBYTECODE="1")
    return root


def action_write_policy(root, environment, *, operator_home=None):
    operator_home = Path(operator_home or pwd.getpwuid(os.getuid()).pw_dir).resolve()
    directories = {str(path) for path in {root.resolve(), *action_writable_directories(environment)}}
    files = {"/dev/null", "/dev/zero", "/dev/random", "/dev/urandom", "/dev/tty", "/dev/ptmx"}
    for key in TEST_OUTPUT_FILE_ENV_KEYS:
        if environment.get(key):
            files.add(str(Path(environment[key]).resolve()))
    return ('(version 1) (allow default) (deny job-creation) (deny lsopen) (deny appleevent-send) (deny network*) '
            '(allow network-inbound (local ip "localhost:*")) '
            '(allow network* (remote ip "localhost:*")) (allow network* (remote unix-socket)) '
            '(deny file-read* (subpath ' + json.dumps(str(operator_home)) + ')) '
            '(deny file-write*) (allow file-write* ' +
            ' '.join('(subpath ' + json.dumps(path) + ')' for path in sorted(directories)) + ' ' +
            ' '.join('(literal ' + json.dumps(path) + ')' for path in sorted(files)) +
            ' (regex #"^/dev/ttys[0-9]+$") (subpath "/dev/fd")) '
            '(deny file-write* (subpath ' + json.dumps(str(operator_home)) + '))')


def main():
    root = install_fixture_environment(platform.system())
    command = sys.argv[1:]
    if not command:
        raise ValueError("qualified native entrypoint needs a test command")
    if platform.system() == "Linux":
        # The existing owner mounts private devpts and drops namespace-local
        # capabilities. Reuse it rather than introducing a sibling PTY path.
        command = ["bash", str(Path(__file__).with_name("bazel_test_entrypoint.sh")), *command]
    if platform.system() == "Darwin":
        os.environ[product_env_name("NATIVE_SIBLING_PROBE")] = str(root.parent / ("denied-" + root.name))
        command = ["/usr/bin/sandbox-exec", "-p", action_write_policy(root, os.environ), *command]
    scope = NativeProcessScope(private_supervisor=platform.system() == "Linux")
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, cancelled)
    process = subprocess.Popen(command, env=scope.environment(os.environ), start_new_session=True)
    scope.track(process)
    with scope.reaping():
        try:
            status = process.wait()
        finally:
            for number in (signal.SIGTERM, signal.SIGINT):
                signal.signal(number, signal.SIG_IGN)
            retired = scope.retire()
            process.wait()
            print("YOMUX_NATIVE_CLEANUP " + json.dumps({"retired": retired,
                "boundary": "linux-private-subreaper" if platform.system() == "Linux" else "environment-marker",
                "complete": platform.system() == "Linux"}), flush=True)
    if not retired:
        raise ValueError("owned native test processes survived cleanup")
    return status


if __name__ == "__main__":
    sys.exit(main())
