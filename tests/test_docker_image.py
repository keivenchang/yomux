# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Tests for the disposable Docker image identity."""

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from tools import bazel_execution_contract
from tools import docker_image


@pytest.mark.parametrize(("capacity", "status", "accepted"), [("137438953472", 0, True), ("", 0, False), ("unknown", 0, False), ("0", 0, False), ("137438953472", 1, False)])
def test_test_container_shared_memory_uses_daemon_ram_or_fails_explicitly(capacity, status, accepted):
    source = (Path(__file__).resolve().parents[1] / "docker/run-tests.sh").read_text()
    sizing_start = source.index('shared_memory_bytes="$(')
    sizing_end = source.index('if [ "$bazel_contract" = 1 ]; then', sizing_start)
    sizing = source[sizing_start:sizing_end]
    script = 'set -e\nCAPACITY="$1"\nSTATUS="$2"\ndocker() { printf "%s" "$CAPACITY"; return "$STATUS"; }\n'
    script += sizing + '\nprintf "%s" "$shared_memory_bytes"\n'
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"YOMUX_BAZEL_MEMORY_MB", "YOMUX_TEST_MEMORY_MB"}
    }
    result = subprocess.run(["bash", "-c", script, "shm-test", capacity, str(status)], capture_output=True, text=True, env=environment)
    assert (result.returncode == 0) is accepted
    if accepted:
        assert result.stdout == capacity


@pytest.mark.parametrize(("cpu_count", "accepted"), [("1", True), ("12", True), ("0", False), ("many", False)])
def test_test_container_cpu_limit_is_applied_or_rejected(cpu_count, accepted):
    source = (Path(__file__).resolve().parents[1] / "docker/run-tests.sh").read_text()
    sizing = source[source.index('cpu_limit="${YOMUX_TEST_CPU_COUNT'):source.index('memory_limit_mb=')]
    script = "set -e\n" + sizing + "printf '%s' \"${cpu_args[*]}\"\n"
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"YOMUX_BAZEL_CPU_COUNT", "YOMUX_TEST_CPU_COUNT"}
    }
    environment["YOMUX_TEST_CPU_COUNT"] = cpu_count
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=environment)
    assert (result.returncode == 0) is accepted
    if accepted:
        assert result.stdout == f"--cpus={cpu_count}"
    else:
        assert "CPU limit must be a positive integer" in result.stderr


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
        "Config": {"User": "1000:1000", "Env": [], "Labels": {"yomux.bazel.contract": contract}},
        "HostConfig": {
            "NetworkMode": "none", "Privileged": False, "ReadonlyRootfs": True,
            "CapAdd": None, "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges", "seccomp=" + seccomp],
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
        "docker/run-tests.sh",
        "tools/bazel_execution_contract.py",
        "tools/bazel_output_slot_entrypoint.sh",
        "tools/bazel_test_entrypoint.sh",
        "tools/bazel_test_common.sh",
        "tools/instance_isolation.py",
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
    chrome.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    chrome.chmod(0o755)
    driver = binary_dir / "chromedriver"
    if with_driver:
        driver.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        driver.chmod(0o755)
    helper = Path(__file__).resolve().parents[1] / "tools/bazel_test_common.sh"
    result = subprocess.run(
        ["/bin/bash", "-c", 'source "$1"; if bazel_native_browser_resolve; then printf "%s|%s" "$BAZEL_NATIVE_CHROME_BIN" "$BAZEL_NATIVE_CHROMEDRIVER_BIN"; else printf unavailable; fi', "native-browser-test", str(helper)],
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
