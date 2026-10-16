# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Tests for the disposable Docker image identity."""

from tests.helpers.fixture_tools import fixture_executable, fixture_script
import copy
import json
import os
import select
import sys
import subprocess
from pathlib import Path

import pytest

from tools import bazel_execution_contract
from tools import docker_image
from tools.product_environment import product_env_names


@pytest.mark.parametrize(("capacity", "status", "accepted"), [("137438953472", 0, True), ("", 0, False), ("unknown", 0, False), ("0", 0, False), ("137438953472", 1, False)])
def test_test_container_shared_memory_uses_daemon_ram_or_fails_explicitly(capacity, status, accepted):
    source = (Path(__file__).resolve().parents[1] / "docker/run-tests.sh").read_text()
    sizing_start = source.index('shared_memory_bytes="$(')
    sizing_end = source.index('if [ "$bazel_contract" = 1 ]; then', sizing_start)
    sizing = source[sizing_start:sizing_end]
    environment_owner = Path(__file__).resolve().parents[1] / "tools" / "product_environment.sh"
    script = f'source "{environment_owner}"\nyomux_normalize_environment\nset -e\nCAPACITY="$1"\nSTATUS="$2"\ndocker() {{ printf "%s" "$CAPACITY"; return "$STATUS"; }}\n'
    script += sizing + '\nprintf "%s" "$shared_memory_bytes"\n'
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {*product_env_names("BAZEL_MEMORY_MB"), *product_env_names("TEST_MEMORY_MB")}
    }
    result = subprocess.run([fixture_executable("bash"), "-c", script, "shm-test", capacity, str(status)], capture_output=True, text=True, env=environment)
    assert (result.returncode == 0) is accepted
    if accepted:
        assert result.stdout == capacity


@pytest.mark.parametrize(("cpu_count", "accepted", "expected"), [("1", True, "--cpus=1"), ("12", True, "--cpus=12"), ("unlimited", True, ""), ("0", False, None), ("many", False, None)])
def test_test_container_cpu_limit_is_applied_or_rejected(cpu_count, accepted, expected):
    source = (Path(__file__).resolve().parents[1] / "docker/run-tests.sh").read_text()
    sizing = source[source.index('cpu_limit="$(product_env_print'):source.index('memory_limit_mb=')]
    environment_owner = Path(__file__).resolve().parents[1] / "tools" / "product_environment.sh"
    script = f'source "{environment_owner}"\nyomux_normalize_environment\nset -e\n' + sizing + "printf '%s' \"${cpu_args[*]}\"\n"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("YOMUX_", "YOLOMUX_"))
    }
    environment["YOMUX_TEST_CPU_COUNT"] = cpu_count
    result = subprocess.run([fixture_executable("bash"), "-c", script], capture_output=True, text=True, env=environment)
    assert (result.returncode == 0) is accepted
    if accepted:
        assert result.stdout == expected
    else:
        assert "CPU limit must be a positive integer or unlimited" in result.stderr


def _fixture_root(root: Path) -> Path:
    for relative in docker_image.IMAGE_INPUT_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture input\n")
    return root


def test_fingerprint_changes_when_any_image_input_changes(tmp_path):
    baseline_root = _fixture_root(tmp_path / "baseline")
    baseline = docker_image.image_fingerprint(baseline_root)
    for index, relative in enumerate(docker_image.IMAGE_INPUT_FILES):
        changed_root = _fixture_root(tmp_path / f"changed-{index}")
        changed_path = changed_root / relative
        changed_path.write_bytes(changed_path.read_bytes() + b"changed\n")
        assert docker_image.image_fingerprint(changed_root) != baseline, relative


@pytest.mark.parametrize(
    "relative",
    [
        "cmd/yomux-go/main.go",
        "tests/test_docker_image.py",
        "static_src/js/yomux.js",
    ],
)
def test_source_only_edits_do_not_change_image_fingerprint(tmp_path, relative):
    root = _fixture_root(tmp_path / "source-only")
    baseline = docker_image.image_fingerprint(root)
    source = root / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("source edit\n", encoding="utf-8")
    assert docker_image.image_fingerprint(root) == baseline


def test_image_preparation_logic_changes_image_fingerprint(tmp_path):
    root = _fixture_root(tmp_path / "preparation-script")
    baseline = docker_image.image_fingerprint(root)
    preparation_script = root / "tools/docker_image.py"
    preparation_script.write_text("changed image preparation behavior\n", encoding="utf-8")
    assert docker_image.image_fingerprint(root) != baseline


def test_toolchain_build_args_selects_default_sdk_and_preserves_named_sdk(tmp_path):
    (tmp_path / ".bazelversion").write_text("9.2.0\n", encoding="utf-8")
    build_tools = tmp_path / "docker" / "build-tools.json"
    build_tools.parent.mkdir()
    build_tools.write_text(
        '{"bazel_sha256_linux_x86_64":"bazel-checksum","buildifier":"v0.0.0-test"}\n',
        encoding="utf-8",
    )
    (tmp_path / "MODULE.bazel").write_text(
        'go_sdk.download(version = "1.27.1", sdks = {"linux_amd64": ["go1.27.1.linux-amd64.tar.gz", "linux-checksum"]})\n'
        'go_sdk.download(name = "yomux_darwin_go_sdk", version = "1.24.13", sdks = {"darwin_arm64": ["go1.24.13.darwin-arm64.tar.gz", "darwin-checksum"]})\n',
        encoding="utf-8",
    )

    assert docker_image.toolchain_build_args(tmp_path) == [
        "BAZEL_VERSION=9.2.0",
        "BAZEL_SHA256=bazel-checksum",
        "GO_VERSION=1.27.1",
        "GO_SHA256=linux-checksum",
    ]


def test_dependency_preparation_uses_module_pinned_go_without_host_go(tmp_path, monkeypatch):
    pinned_go = tmp_path / "pinned" / "go" / "bin" / "go"
    module_cache = tmp_path / "module-cache"
    module_cache.mkdir()
    (tmp_path / "go.mod").write_bytes(b"module fixture\n")
    (tmp_path / "go.sum").write_bytes(b"")
    (module_cache / "seed.zip").write_text("seed", encoding="utf-8")
    calls = []

    def fake_run(command, *, env, **kwargs):
        calls.append((command, env, kwargs))
        return subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(docker_image.subprocess, "run", fake_run)
    assert docker_image._download_go_modules(pinned_go, tmp_path, module_cache)
    assert calls[0][0] == [str(pinned_go), "mod", "download", "-json"]
    assert calls[0][1]["GOMODCACHE"] == str(module_cache)
    assert calls[0][1]["GOTOOLCHAIN"] == "local"
    assert (module_cache / "seed.zip").stat().st_mode & 0o200


def test_dependency_preparation_seeds_locked_test_only_modules(tmp_path, monkeypatch):
    pinned_go = tmp_path / "pinned" / "go" / "bin" / "go"
    module_cache = tmp_path / "module-cache"
    module_cache.mkdir()
    (tmp_path / "go.mod").write_text("module fixture\n", encoding="utf-8")
    (tmp_path / "go.sum").write_text(
        "example.com/test-only v1.2.3 h1:checksum\nexample.com/test-only v1.2.3/go.mod h1:modchecksum\n",
        encoding="utf-8",
    )
    calls = []

    def fake_run(command, *, env, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(docker_image.subprocess, "run", fake_run)
    assert docker_image._download_go_modules(pinned_go, tmp_path, module_cache)
    assert calls == [
        [str(pinned_go), "mod", "download", "-json"],
        [str(pinned_go), "mod", "download", "example.com/test-only@v1.2.3"],
    ]


def test_bazel_container_admission_rejects_security_or_mount_drift(tmp_path):
    root = tmp_path / "checkout"
    cache = tmp_path / "cache"
    output_roots = [tmp_path / f"output-user-root-{index}" for index in range(bazel_execution_contract.OUTPUT_USER_ROOT_SLOTS)]
    root.mkdir()
    cache.mkdir()
    for output_root in output_roots:
        output_root.mkdir()
    image_id = "sha256:" + "a" * 64
    contract = bazel_execution_contract.identity(image_id)
    seccomp = json.dumps(json.loads((Path(__file__).resolve().parents[1] / "docker/sandbox-seccomp.json").read_text()))
    record = {
        "Image": image_id,
        "AppArmorProfile": bazel_execution_contract.apparmor_profile(),
        "Config": {"User": "1000:1000", "Env": [], "Labels": {"yomux.bazel.contract": contract}},
        "HostConfig": {
            "NetworkMode": "none", "Privileged": False, "ReadonlyRootfs": True,
            "CapAdd": None, "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges", "seccomp=" + seccomp, "apparmor=" + bazel_execution_contract.apparmor_profile()],
            "MaskedPaths": [], "ReadonlyPaths": [], "Tmpfs": {"/tmp": {}, "/home/runner": {}},
        },
        "Mounts": [
            {"Type": "bind", "Source": str(root), "Destination": "/w", "RW": False},
            {"Type": "bind", "Source": str(cache), "Destination": "/var/cache/yomux-bazel", "RW": True},
            *[
                {"Type": "bind", "Source": str(output_root), "Destination": f"/var/cache/yomux-bazel-output-slot-{index}", "RW": True}
                for index, output_root in enumerate(output_roots)
            ],
            {"Type": "tmpfs", "Source": "", "Destination": "/tmp", "RW": True},
            {"Type": "tmpfs", "Source": "", "Destination": "/home/runner", "RW": True},
        ],
    }
    bazel_execution_contract.inspect_container(record, image_id, contract, root, cache, *output_roots)

    invalid = copy.deepcopy(record)
    invalid["Image"] = "sha256:" + "b" * 64
    with pytest.raises(ValueError, match="incorrect image or execution contract identity"):
        bazel_execution_contract.inspect_container(invalid, image_id, contract, root, cache, *output_roots)

    invalid = copy.deepcopy(record)
    invalid["HostConfig"]["SecurityOpt"][1] = "seccomp=unconfined"
    with pytest.raises(ValueError, match="pinned namespace sandbox policy"):
        bazel_execution_contract.inspect_container(invalid, image_id, contract, root, cache)

    invalid = copy.deepcopy(record)
    invalid["Mounts"].append({"Type": "bind", "Source": "/", "Destination": "/host", "RW": True})
    with pytest.raises(ValueError, match="unexpected bind mounts"):
        bazel_execution_contract.inspect_container(invalid, image_id, contract, root, cache)

    invalid = copy.deepcopy(record)
    invalid["Config"]["Env"].append("GH_TOKEN=synthetic-secret")
    with pytest.raises(ValueError, match="credentials"):
        bazel_execution_contract.inspect_container(invalid, image_id, contract, root, cache)


def test_bazel_runner_rejects_isolation_overrides_and_cache_flags_are_safe():
    for option in (
        "--sandbox_add_mount_pair=/w",
        "--strategy_regexp=.*=standalone",
        "--test_env=GH_TOKEN=synthetic-secret",
        "--test_env=YOMUX_BAZEL_CONTRACT=fixture",
        "--test_env=YOLOMUX_BAZEL_CONTRACT=fixture",
        "--action_env=PATH=/host/bin",
        "--platform_suffix=unkeyed",
    ):
        with pytest.raises(ValueError):
            bazel_execution_contract.validate_options([option])
    with pytest.raises(ValueError, match="protected test input"):
        bazel_execution_contract.validate_options(["--test_env", "GH_TOKEN=synthetic-secret"])
    with pytest.raises(ValueError, match="requires KEY=VALUE"):
        bazel_execution_contract.validate_options(["--test_env"])
    bazel_execution_contract.validate_options(["--test_filter=TestSpecific", "--cache_test_results=1"])


def test_execution_contract_identity_changes_with_runner_or_sandbox_inputs(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    contract_files = (
        "docker/sandbox-seccomp.json",
        "docker/sandbox-apparmor.profile",
        "docker/run-tests.sh",
        "tools/bazel_execution_contract.py",
        "tools/bazel_output_slot_entrypoint.sh",
        "tools/bazel_test_entrypoint.sh",
        "tools/bazel_test_common.sh",
        "tools/instance_isolation.py",
        "tools/product_environment.py",
        "tools/product_namespace.json",
        "tools/product_environment.sh",
        "tools/run_bazel_tests.sh",
    )
    originals = {}
    for relative in contract_files:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        originals[relative] = (repo_root / relative).read_bytes()
        path.write_bytes(originals[relative])

    image_id = "sha256:" + "c" * 64
    baseline = bazel_execution_contract.identity(image_id, tmp_path)
    for relative in contract_files:
        changed = tmp_path / relative
        changed.write_bytes(originals[relative] + b"\ncontract drift\n")
        assert bazel_execution_contract.identity(image_id, tmp_path) != baseline, relative
        changed.write_bytes(originals[relative])


@pytest.mark.parametrize("with_driver", [False, True])
def test_native_browser_resolution_requires_a_host_chromedriver_on_path(tmp_path, with_driver):
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    chrome = binary_dir / "chromium"
    chrome.write_text(fixture_script("#!/bin/sh\nexit 0\n"), encoding="utf-8")
    chrome.chmod(0o755)
    driver = binary_dir / "chromedriver"
    if with_driver:
        driver.write_text(fixture_script("#!/bin/sh\nexit 0\n"), encoding="utf-8")
        driver.chmod(0o755)
    helper = Path(__file__).resolve().parents[1] / "tools/bazel_test_common.sh"
    result = subprocess.run(
        [fixture_executable("bash"), "-c", 'source "$1"; if bazel_native_browser_resolve; then printf "%s|%s" "$BAZEL_NATIVE_CHROME_BIN" "$BAZEL_NATIVE_CHROMEDRIVER_BIN"; else printf unavailable; fi', "native-browser-test", str(helper)],
        capture_output=True,
        text=True,
        env={"PATH": str(binary_dir), "CHROME_BIN": str(chrome)},
        check=False,
    )
    assert result.returncode == 0
    if with_driver:
        assert result.stdout == f"{chrome}|{driver}"
    else:
        assert result.stdout == "unavailable"


def test_registry_pin_is_available_to_clean_checkouts(tmp_path):
    root = Path(__file__).resolve().parents[1]
    pin = root / 'docker/bazel-registry.json'
    assert pin.is_file()
    # A worktree's .git points outside the container mount. Exercise its ignore
    # policy in a private repository instead of consulting operator Git state.
    (tmp_path / '.gitignore').write_bytes((root / '.gitignore').read_bytes())
    (tmp_path / 'docker').mkdir()
    (tmp_path / 'docker/bazel-registry.json').write_bytes(pin.read_bytes())
    environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    subprocess.run(['git', 'init', '--template=', str(tmp_path)], env=environment, capture_output=True, check=True)
    result = subprocess.run(['git', 'check-ignore', '--no-index', '-q', 'docker/bazel-registry.json'], cwd=tmp_path, env=environment, check=False)
    assert result.returncode == 1, 'required registry pin must not be ignored'


def test_missing_image_inputs_fail_before_downloads(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(docker_image, '_download_verified', lambda *args: calls.append(args))
    with pytest.raises(ValueError, match='missing required image inputs:'):
        docker_image.prepare_go_module_cache(tmp_path / 'destination', tmp_path)
    assert calls == []


def test_docker_admission_failure_does_not_launch_native_fallback():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'tools/run_bazel_tests.sh').read_text()
    start = source.index('  status=0\n')
    end = source.index('\nfi\n', start)
    block = source[start:end]
    script = 'execution=docker\nrequested_execution=auto\nREPO_ROOT=/fixture\noriginal_args=()\n' + block.replace('"$REPO_ROOT/docker/run-tests.sh"', 'failed_docker')
    script = 'failed_docker() { return 78; }\n' + script
    result = subprocess.run([fixture_executable("bash"), '-c', script], capture_output=True, text=True, check=False)
    assert result.returncode == 78
    assert 'WARNING: HERMETIC DOCKER TESTING FAILED' in result.stderr
    assert 'selecting native' not in result.stderr


def test_linux_auto_refuses_unreachable_docker_without_native_fallback():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'tools/run_bazel_tests.sh').read_text()
    start = source.index('case "$execution" in\n')
    end = source.index('if [ "$execution" = native ]; then', start)
    script = '''execution=auto
bazel_test_in_container() { return 1; }
uname() { printf Linux; }
docker() { return 1; }
''' + source[start:end]
    result = subprocess.run([fixture_executable("bash"), '-c', script], capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert 'WARNING: HERMETIC DOCKER TESTING FAILED' in result.stderr
    assert '--execution native' in result.stderr
    assert '"failure_phase":"daemon"' in result.stdout


def test_image_build_failure_reports_phase_and_retains_exit_status():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'docker/run-tests.sh').read_text()
    start = source.index('  build_status=0\n')
    end = source.index('  trap - EXIT\n', start)
    script = '''set -euo pipefail
docker() { return 17; }
build_args=(); build_context_args=(); REPO_ROOT=/fixture; IMAGE=fixture
''' + source[start:end] + '\nexit "$build_status"\n'
    result = subprocess.run([fixture_executable("bash"), '-c', script], capture_output=True, text=True, check=False)
    assert result.returncode == 17
    assert '"failure_phase":"image-build"' in result.stdout
    assert 'WARNING: HERMETIC DOCKER TESTING FAILED' in result.stderr


def test_apparmor_loaded_policy_must_match_compiled_source(monkeypatch):
    name = bazel_execution_contract.apparmor_profile()
    compiled = b"compiled-fixture"
    digest = bazel_execution_contract.hashlib.sha256(compiled).hexdigest()
    actual = ["enforce", digest]
    calls = []

    def output(command, **kwargs):
        calls.append(command)
        if command[0] == "apparmor_parser":
            return compiled
        return "\n".join(actual)

    monkeypatch.setattr(bazel_execution_contract.subprocess, "check_output", output)
    assert bazel_execution_contract.verify_apparmor() == (name, digest)
    assert not any("-r" in call for call in calls)
    for invalid in (["complain", digest], ["enforce", "changed"]):
        actual[:] = invalid
        with pytest.raises(ValueError, match="differs from compiled source"):
            bazel_execution_contract.verify_apparmor()


def test_admission_tool_inputs_cannot_be_overridden():
    for key in ("PATH", "HOME", "CHROME_BIN", "CHROMEDRIVER_BIN"):
        with pytest.raises(ValueError, match="protected test input"):
            bazel_execution_contract.validate_options(["--test_env=" + key + "=/host"])


def test_contract_container_owner_label_has_no_literal_quotes(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = (root / 'docker/run-tests.sh').read_text()
    label_start = source.index('runner_env=()')
    label_end = source.index('\nfi', source.index('owner_label=()', label_start)) + 3
    create_start = source.index('  contract_container="$(docker create')
    create_end = source.index('  contract_cleanup()', create_start)
    capture = tmp_path / 'argv.json'
    script = '''source "$1/tools/product_environment.sh"
product_env_assign CHECK_RUN_TOKEN=fixture-token
product_env_assign BAZEL_CACHE=off
product_env_assign BAZEL_EXECUTION=docker
cpu_args=(); cache_mount=()
REPO_ROOT=/fixture
image_id=fixture; contract=fixture; IMAGE=fixture
apparmor_profile=fixture; memory_limit_bytes=1024; shared_memory_bytes=1024; workdir=/w
argv_output="$2"
docker() { python3 -c 'import json,sys; from pathlib import Path; Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))' "$argv_output" "$@"; printf fixture; }
'''
    script += source[label_start:label_end] + '\n' + source[create_start:create_end]
    result = subprocess.run([fixture_executable("bash"), '-c', script, 'label-test', str(root), str(capture)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    argv = json.loads(capture.read_text())
    labels = [argv[index + 1] for index, value in enumerate(argv) if value == '--label']
    assert 'yolomux.check.run=fixture-token' in labels
    assert 'YOMUX_BAZEL_CACHE=off' in argv
    assert 'YOMUX_BAZEL_EXECUTION=docker' in argv
    assert not any('"' in label for label in labels)


def test_compiled_apparmor_policy_changes_execution_identity():
    image = 'sha256:' + 'a' * 64
    assert bazel_execution_contract.identity(image, apparmor_digest='a' * 64) != bazel_execution_contract.identity(image, apparmor_digest='b' * 64)


def test_private_module_preparation_rejects_sum_changes(tmp_path, monkeypatch):
    for name in ('go.mod', 'go.sum'):
        (tmp_path / name).write_bytes(b'original')
    cache = tmp_path / 'cache'
    cache.mkdir()

    def changed_sum(command, *, cwd, **kwargs):
        (cwd / 'go.sum').write_bytes(b'changed')
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(docker_image.subprocess, 'run', changed_sum)
    with pytest.raises(ValueError, match='change locked go.sum'):
        docker_image._download_go_modules(tmp_path / 'go', tmp_path, cache)
    assert (tmp_path / 'go.sum').read_bytes() == b'original'


def test_parallel_image_requests_build_once(tmp_path):
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path / "bin"
    binary.mkdir()
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    image = "fixture-image-" + str(tmp_path).replace("/", "-")
    marker = tmp_path / "image"
    counter = tmp_path / "build-count"
    python_stub = binary / "python3"
    python_stub.write_text(f"""#!{sys.executable}
import os,sys
if len(sys.argv)>2 and sys.argv[1].endswith('docker_image.py'):
 if sys.argv[2]=='--name':
  os.write(int(os.environ['READY_FD']),b'N'); print(os.environ['FIXTURE_IMAGE']); sys.exit(0)
 if sys.argv[2]=='--prepare-go-mod-cache':
  os.write(int(os.environ['READY_FD']),b'P'); os.read(int(os.environ['RELEASE_FD']),1); sys.exit(0)
 if sys.argv[2]=='--toolchain-build-args':
  print('BAZEL_VERSION=fixture\\nBAZEL_SHA256=fixture\\nGO_VERSION=fixture\\nGO_SHA256=fixture'); sys.exit(0)
os.execv({sys.executable!r},[{sys.executable!r},*sys.argv[1:]])
""")
    docker_stub = binary / "docker"
    docker_stub.write_text(f"""#!{sys.executable}
import sys
from pathlib import Path
args=sys.argv[1:]
if args[:2]==['image','inspect']:
 if not Path({str(marker)!r}).exists(): sys.exit(1)
 if '--format' in args: print('sha256:'+'a'*64)
elif args and args[0]=='info':
 print('1073741824' if any('MemTotal' in arg for arg in args) else '[]')
elif args and args[0]=='build':
 path=Path({str(counter)!r}); path.write_text(path.read_text()+'build\\n' if path.exists() else 'build\\n')
 Path({str(marker)!r}).touch()
""")
    for path in (python_stub, docker_stub):
        path.chmod(0o755)
    environment = {key: value for key, value in os.environ.items() if not key.startswith(('YOMUX_', 'YOLOMUX_'))}
    environment.update(PATH=str(binary)+':'+os.environ['PATH'], READY_FD=str(ready_write), RELEASE_FD=str(release_read), FIXTURE_IMAGE=image, YOMUX_BAZEL_CACHE='off')
    processes = []
    try:
        command = [str(root / 'docker/run-tests.sh'), '--', 'true']
        processes.append(subprocess.Popen(command, env=environment, pass_fds=(ready_write,release_read), stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        observed = b''
        while b'P' not in observed:
            assert select.select([ready_read],[],[],10)[0], 'first build did not reach preparation'
            observed += os.read(ready_read,64)
        processes.append(subprocess.Popen(command, env=environment, pass_fds=(ready_write,release_read), stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        assert select.select([ready_read],[],[],10)[0], 'second request did not fingerprint its image'
        assert os.read(ready_read,1)==b'N'
        os.write(release_write,b'RR')
        for process in processes:
            _, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr.decode()
        assert counter.read_text().splitlines() == ['build']
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill(); process.wait()
        for descriptor in (ready_read,ready_write,release_read,release_write):
            os.close(descriptor)
