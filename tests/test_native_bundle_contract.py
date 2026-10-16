# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Offline bundle checks must never count as native execution admission."""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from tools import bazel_execution_contract as contract
from tools import native_bundle_prepare
from tools.instance_isolation import private_directory


def write_lock(root, manifest_path, manifest):
    files = {}
    locked_directories = {}
    for directory, directories, leaves in os.walk(root, followlinks=False):
        locked_directories[Path(directory).relative_to(root).as_posix()] = stat.S_IMODE(Path(directory).stat().st_mode)
        for name in list(directories):
            if (Path(directory) / name).is_symlink():
                directories.remove(name)
                leaves.append(name)
        for name in leaves:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            files[relative] = (
                {"symlink": os.readlink(path)} if path.is_symlink() else
                {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "mode": stat.S_IMODE(path.stat().st_mode)}
            )
    manifest["files"] = files
    manifest["directories"] = locked_directories
    data = json.dumps(manifest, sort_keys=True).encode()
    manifest_path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "payload"
    root.mkdir()
    (root / "bin").mkdir()
    executables = {}
    for name in contract.required_native_tools("Linux"):
        path = root / "bin" / name
        path.write_bytes(b"synthetic executable; never run\n")
        path.chmod(0o555)
        executables[name] = "bin/" + name
    for name in contract.NATIVE_SEED_DIRECTORIES:
        (root / name).mkdir()
        seed = root / name / "seed"
        seed.write_bytes(b"synthetic seed\n")
        seed.chmod(0o444)
    for name, path in contract.xterm_fixture_paths(contract.native_xterm_package_root(root, "Linux")).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((contract.ROOT / "static/vendor" / name).read_bytes())
        path.chmod(0o444)
    packages = {}
    (root / "packages").mkdir()
    for requirement in (contract.ROOT / "docker/requirements-test.txt").read_text().splitlines():
        if not requirement or requirement.startswith("#"):
            continue
        name, version = requirement.split("==")
        relative = "packages/" + name + ".metadata"
        path = root / relative
        path.write_bytes(f"Name: {name}\nVersion: {version}\n".encode())
        path.chmod(0o444)
        packages[name] = relative
    manifest = {
        "schema": 1,
        "platform": {"system": "Linux", "machine": "x86_64"},
        "executables": executables,
        "python_packages": packages,
    }
    lock = tmp_path / "manifest.json"
    digest = write_lock(root, lock, manifest)
    return root, lock, manifest, digest


def verify(bundle):
    root, lock, _manifest, digest = bundle
    return contract.verify_native_bundle(root, lock, digest, system="Linux", machine="x86_64")


def test_locked_bundle_is_checked_without_running_tools(bundle, monkeypatch):
    def refuse_process(*args, **kwargs):
        pytest.fail("offline bundle verification attempted to run a tool")

    monkeypatch.setattr(subprocess, "run", refuse_process)
    monkeypatch.setattr(subprocess, "check_output", refuse_process)
    admitted = verify(bundle)
    assert admitted.manifest_digest == bundle[3]
    assert admitted.executables["python3"] == bundle[0] / "bin/python3"


@pytest.mark.parametrize("asset", tuple(contract.NATIVE_XTERM_ASSETS))
def test_missing_xterm_fixture_refuses_even_a_newly_locked_bundle(bundle, asset):
    root, lock, manifest, _digest = bundle
    contract.xterm_fixture_paths(contract.native_xterm_package_root(root, "Linux"))[asset].unlink()
    digest = write_lock(root, lock, manifest)
    with pytest.raises(ValueError, match="xterm package fixture is missing"):
        verify((root, lock, manifest, digest))


def test_newly_locked_xterm_fixture_must_still_match_upstream_vendor_bytes(bundle):
    root, lock, manifest, _digest = bundle
    asset = contract.xterm_fixture_paths(contract.native_xterm_package_root(root, "Linux"))["xterm.css"]
    asset.chmod(0o644)
    asset.write_bytes(b"different upstream bytes\n")
    asset.chmod(0o444)
    digest = write_lock(root, lock, manifest)
    with pytest.raises(ValueError, match="differs from tracked vendor asset"):
        verify((root, lock, manifest, digest))


@pytest.mark.parametrize("name", contract.product_env_names("XTERM_PACKAGE_ROOT"))
def test_xterm_fixture_root_is_a_protected_qualified_input(name):
    with pytest.raises(ValueError, match="protected test input"):
        contract.validate_options(["--test_env=" + name + "=/host/fixture"])


@pytest.mark.parametrize("digest", ["", "a" * 63, "A" * 64, "0" * 64])
def test_manifest_has_an_independent_checksum_lock(bundle, digest):
    with pytest.raises(ValueError, match="checksum|lock"):
        verify((*bundle[:3], digest))


def test_changed_tool_is_rejected_and_changes_bundle_identity(bundle):
    root, lock, manifest, digest = bundle
    node = root / "bin/node"
    node.chmod(0o755)
    node.write_bytes(b"changed tool bytes\n")
    node.chmod(0o555)
    with pytest.raises(ValueError, match="bytes or mode"):
        verify(bundle)
    new_digest = write_lock(root, lock, manifest)
    assert new_digest != digest
    assert verify((root, lock, manifest, new_digest)).manifest_digest == new_digest


@pytest.mark.parametrize("index", [256, 599])
def test_bundle_verification_rejects_corruption_after_first_batch(bundle, index):
    root, lock, manifest, _digest = bundle
    for number in range(600):
        path = root / f"zz-runtime-{number:04d}"
        path.write_bytes(b"locked runtime input\n")
        path.chmod(0o444)
    digest = write_lock(root, lock, manifest)
    path = root / f"zz-runtime-{index:04d}"
    path.chmod(0o644)
    path.write_bytes(b"changed runtime input\n")
    path.chmod(0o444)
    with pytest.raises(ValueError, match=path.name):
        verify((root, lock, manifest, digest))


def test_prepared_manifest_preserves_all_bytes_modes_links_and_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(native_bundle_prepare.platform, "system", lambda: "Linux")
    payload = tmp_path / "payload"
    payload.mkdir()
    expected = {}
    for number in range(270):
        source = payload / f"runtime-{number:04d}"
        data = bytes([number % 256]) + b"\x00runtime\n"
        source.write_bytes(data)
        source.chmod(0o755 if number % 2 else 0o644)
        expected[source.name] = {"mode": 0o555 if number % 2 else 0o444,
                                 "sha256": hashlib.sha256(data).hexdigest()}
    link = payload / "link"
    link.symlink_to("runtime-0000")
    expected[link.name] = {"symlink": "runtime-0000"}
    relative = "python/lib/python3.12/site-packages/example.dist-info/METADATA"
    metadata = payload / relative
    metadata.parent.mkdir(parents=True)
    data = b"Name: Example-Package\nVersion: 1.2.3\n"
    metadata.write_bytes(data)
    expected[relative] = {"mode": 0o444, "sha256": hashlib.sha256(data).hexdigest()}
    first = native_bundle_prepare.manifest_for(payload)
    assert first["files"] == expected
    assert first["python_packages"] == {"Example-Package": relative}
    assert first["directories"]["."] == 0o700
    assert all(mode == 0o755 for name, mode in first["directories"].items() if name != ".")
    assert native_bundle_prepare.manifest_for(payload) == first


@pytest.mark.parametrize("tool", ["chrome", "chromedriver", "claude", "codex", "opencode", "cc", "unshare"])
def test_required_tools_cannot_be_omitted(bundle, tool):
    root, lock, manifest, _digest = bundle
    del manifest["executables"][tool]
    digest = write_lock(root, lock, manifest)
    with pytest.raises(ValueError, match="required native tools"):
        verify((root, lock, manifest, digest))


@pytest.mark.parametrize("mutation", ["untracked-file", "writable-tool", "not-executable", "missing-file"])
def test_payload_inventory_and_modes_are_enforced(bundle, mutation):
    root, lock, manifest, digest = bundle
    if mutation == "untracked-file":
        (root / "extra").write_bytes(b"undeclared\n")
    elif mutation == "writable-tool":
        (root / "bin/node").chmod(0o755)
    elif mutation == "missing-file":
        (root / "bin/node").unlink()
    else:
        (root / "bin/node").chmod(0o444)
        digest = write_lock(root, lock, manifest)
    with pytest.raises(ValueError, match="inventory|mode|not executable"):
        verify((root, lock, manifest, digest))


@pytest.mark.parametrize("target", ["/usr/bin/python3", "../../host-python"])
def test_locked_symlinks_cannot_use_host_tools(bundle, target):
    root, lock, manifest, _digest = bundle
    (root / "bin/python3").unlink()
    (root / "bin/python3").symlink_to(target)
    (root.parent / "host-python").write_bytes(b"host tool\n")
    digest = write_lock(root, lock, manifest)
    with pytest.raises(ValueError, match="symlink"):
        verify((root, lock, manifest, digest))


def test_relative_symlink_within_locked_payload_is_allowed(bundle):
    root, lock, manifest, _digest = bundle
    (root / "bin/python3").unlink()
    (root / "bin/python3").symlink_to("node")
    digest = write_lock(root, lock, manifest)
    assert verify((root, lock, manifest, digest)).executables["python3"] == root / "bin/node"


def test_optional_dangling_link_stays_inside_payload(bundle):
    root, lock, manifest, _digest = bundle
    (root / "optional-document").symlink_to("absent-document")
    digest = write_lock(root, lock, manifest)
    verify((root, lock, manifest, digest))


def test_dangling_required_executable_refuses_admission(bundle):
    root, lock, manifest, _digest = bundle
    (root / "bin/chrome").unlink()
    (root / "bin/chrome").symlink_to("absent-browser")
    digest = write_lock(root, lock, manifest)
    with pytest.raises(ValueError, match="absent or not executable"):
        verify((root, lock, manifest, digest))


def test_traversal_error_cannot_hide_undeclared_input(bundle, monkeypatch):
    root = bundle[0]
    hidden = root / "unscanned"
    hidden.mkdir()
    (hidden / "undeclared").write_bytes(b"undeclared bytes\n")
    original_scandir = os.scandir

    def unreadable_scandir(path):
        if Path(path) == hidden:
            raise PermissionError("synthetic unreadable subtree")
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", unreadable_scandir)
    with pytest.raises(PermissionError, match="unreadable subtree"):
        verify(bundle)


def test_directory_inventory_is_locked_including_empty_directories(bundle):
    (bundle[0] / "undeclared_empty_directory").mkdir()
    with pytest.raises(ValueError, match="directory inventory"):
        verify(bundle)


@pytest.mark.parametrize("mode", [0o444, 0o445, 0o454])
def test_executable_requires_effective_access(bundle, monkeypatch, mode):
    root, lock, manifest, _digest = bundle
    path = root / "bin/node"
    path.chmod(mode)
    digest = write_lock(root, lock, manifest)
    original_access = os.access

    def access(candidate, mask):
        if Path(candidate) == path and mask == os.X_OK:
            return False
        return original_access(candidate, mask)

    monkeypatch.setattr(os, "access", access)
    with pytest.raises(ValueError, match="not executable"):
        verify((root, lock, manifest, digest))


@pytest.mark.parametrize("seed", contract.NATIVE_SEED_DIRECTORIES)
def test_empty_dependency_seed_fails_before_execution(bundle, seed):
    root, lock, manifest, _digest = bundle
    (root / seed / "seed").unlink()
    digest = write_lock(root, lock, manifest)
    with pytest.raises(ValueError, match="dependency seed is missing"):
        verify((root, lock, manifest, digest))


def test_wrong_python_package_version_is_rejected_even_when_checksum_matches(bundle):
    root, lock, manifest, _digest = bundle
    path = root / manifest["python_packages"]["selenium"]
    path.chmod(0o644)
    path.write_bytes(b"Name: selenium\nVersion: 0.0.0\n")
    path.chmod(0o444)
    digest = write_lock(root, lock, manifest)
    with pytest.raises(ValueError, match="package differs from its pin"):
        verify((root, lock, manifest, digest))


def test_bundle_cannot_cross_platforms(bundle):
    root, lock, _manifest, digest = bundle
    with pytest.raises(ValueError, match="platform"):
        contract.verify_native_bundle(root, lock, digest, system="Darwin", machine="arm64")


def test_cli_bundle_verification_does_not_claim_sandbox_admission(bundle, monkeypatch, capsys):
    root, lock, _manifest, digest = bundle
    monkeypatch.setattr(contract.platform, "system", lambda: "Linux")
    monkeypatch.setattr(contract.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(sys, "argv", ["contract", "--verify-native-bundle", str(root), str(lock), digest])
    assert contract.main() == 0
    event = json.loads(capsys.readouterr().out.removeprefix("YOMUX_EXECUTION "))
    assert event["bundle_verified"] is True
    assert event["admission"] == "not-run"
    assert event["coverage_complete"] is False
    assert "hermetic" not in event


def test_cli_invalid_manifest_reports_bundle_failure(bundle, monkeypatch, capsys):
    root, lock, _manifest, _digest = bundle
    lock.write_bytes(b"[]")
    digest = hashlib.sha256(b"[]").hexdigest()
    monkeypatch.setattr(sys, "argv", ["contract", "--verify-native-bundle", str(root), str(lock), digest])
    assert contract.main() == 78
    captured = capsys.readouterr()
    event = json.loads(captured.out.removeprefix("YOMUX_EXECUTION "))
    assert event["failure_phase"] == "bundle"
    assert event["bundle_verified"] is False
    assert "must be an object" in captured.err


@pytest.mark.parametrize("path", ["../host", "/host", "bin//node", "./bin/node", ""])
def test_paths_are_canonical_and_relative(path):
    with pytest.raises(ValueError, match="bundle path"):
        contract._bundle_path(path)


@pytest.mark.parametrize("option", [
    "--norepository_disable_download", "--repo_env=PATH=/host",
    "--override_repository=tools=/host", "--registry=https://example.invalid",
    "--shell_executable=/host/bash", "--extra_toolchains=//host:tools",
    "--extra_execution_platforms=//host:platform", "--platforms=//host:platform",
    "--noincompatible_strict_action_env", "--experimental_spawn_scheduler=local",
    "--modify_execution_info=TestRunner=+no-sandbox", "--modify_execution_info=.*=+requires-network", "--test_strategy=standalone",
    "--genrule_strategy=local", "--noincompatible_exclusive_test_sandboxed", "--persistent_android_resource_processor", "--future_unknown_policy=true",
])
def test_qualified_execution_cannot_replace_tools_or_enable_downloads(option):
    with pytest.raises(ValueError, match="execution policy"):
        contract.validate_options([option])


@pytest.mark.parametrize("key", ["PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "BASH_ENV", "NODE_OPTIONS", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "JAVA_HOME", "BUN_OPTIONS", "CODESIGN_ALLOCATE", "MAC_CHROMIUM_TMPDIR", "TEST_TMPDIR", "TEST_SRCDIR", "RUNFILES_DIR", "YOMUX_NATIVE_OUTER_MOUNTS"])
def test_environment_cannot_inject_undeclared_runtime_inputs(key):
    for args in (["--test_env=" + key + "=/host"], ["--test_env", key + "=/host"]):
        with pytest.raises(ValueError, match="protected test input"):
            contract.validate_options(args)


@pytest.mark.parametrize("revision", ["a" * 40, "not-a-revision", ""])
def test_declared_revision_survives_absent_git_metadata(monkeypatch, tmp_path, revision):
    monkeypatch.setattr(contract, "ROOT", tmp_path)
    monkeypatch.setenv("YOMUX_BAZEL_EXECUTION", "qualified-native")
    monkeypatch.setenv("YOMUX_BAZEL_CONTRACT", "fixture-policy")
    monkeypatch.setenv("YOMUX_TEST_REVISION", revision)

    def undeclared_git(*args, **kwargs):
        raise AssertionError("test evidence attempted to read undeclared Git metadata")

    monkeypatch.setattr(contract.subprocess, "run", undeclared_git)
    if revision == "a" * 40:
        assert contract.repository_head_sha(tmp_path, declared=True) == revision
    else:
        with pytest.raises(ValueError, match="declared checkout revision"):
            contract.repository_head_sha(tmp_path, declared=True)


def test_release_context_collects_pinned_git_facts_with_clean_environment(monkeypatch, tmp_path):
    revision = "a" * 40
    responses = {
        ("rev-parse", "HEAD"): revision + "\n",
        ("tag", "--points-at", revision): "unrelated\nv1.2.3\n",
        ("show", revision + ":version.yaml"): 'version: "1.2.3"\n',
        ("show", "-s", "--format=%s", revision): "Release YOmux 1.2.3\n",
        ("cat-file", "-t", "refs/tags/v1.2.3"): "tag\n",
        ("for-each-ref", "--format=%(contents:subject)", "refs/tags/v1.2.3"): "YOmux 1.2.3\n",
    }
    monkeypatch.setenv("GIT_DIR", "/foreign/git")

    def git(command, **kwargs):
        assert not any(key.startswith("GIT_") for key in kwargs["env"])
        assert command[:5] == ["git", "-c", "safe.directory=" + str(tmp_path), "-C", str(tmp_path)]
        return subprocess.CompletedProcess(command, 0, responses[tuple(command[5:])], "")

    monkeypatch.setattr(contract.subprocess, "run", git)
    context = contract.repository_release_context(tmp_path)
    assert context == {"revision": revision, "head_version_source": 'version: "1.2.3"\n',
                       "commit_subject": "Release YOmux 1.2.3",
                       "tags": {"v1.2.3": {"type": "tag", "subject": "YOmux 1.2.3"}}}


@pytest.mark.parametrize("case", ["valid", "missing", "revision", "tag"])
def test_declared_release_context_is_bound_to_declared_revision_without_git(monkeypatch, tmp_path, case):
    root = tmp_path.resolve()
    monkeypatch.setattr(contract, "ROOT", root)
    monkeypatch.setenv("YOMUX_BAZEL_EXECUTION", "qualified-native")
    monkeypatch.setenv("YOMUX_BAZEL_CONTRACT", "fixture-policy")
    monkeypatch.setenv("YOMUX_TEST_REVISION", "a" * 40)
    context = {"revision": ("b" if case == "revision" else "a") * 40,
               "head_version_source": 'version: "1.2.3"\n',
               "commit_subject": "Release YOmux 1.2.3", "tags": {}}
    if case == "tag":
        context["tags"]["v1.2.3"] = {"subject": "missing type"}
    monkeypatch.setenv("YOMUX_TEST_RELEASE_CONTEXT", "" if case == "missing" else json.dumps(context))

    def undeclared_git(*args, **kwargs):
        raise AssertionError("declared release evidence attempted to read Git metadata")

    monkeypatch.setattr(contract.subprocess, "run", undeclared_git)
    if case == "valid":
        assert contract.repository_release_context(root, declared=True) == context
    else:
        with pytest.raises(ValueError):
            contract.repository_release_context(root, declared=True)
    with pytest.raises(AssertionError, match="attempted to read Git"):
        contract.repository_release_context(root / "foreign", declared=True)


@pytest.mark.parametrize("prefix", ["YOMUX", "YOLOMUX"])
def test_release_context_cannot_be_overridden_by_test_options(prefix):
    with pytest.raises(ValueError, match="protected test input"):
        contract.validate_options(["--test_env=" + prefix + "_TEST_RELEASE_CONTEXT={}"])


@pytest.mark.parametrize("prefix", ["YOMUX", "YOLOMUX"])
@pytest.mark.parametrize("separate", [False, True])
def test_fixture_evidence_destination_cannot_be_overridden(prefix, separate):
    assignment = prefix + "_E2E_EVIDENCE_DIR=/tmp/foreign-evidence"
    with pytest.raises(ValueError, match="protected test input"):
        contract.validate_options(["--test_env", assignment] if separate else ["--test_env=" + assignment])


def test_private_fixture_state_cannot_follow_a_symlink(tmp_path):
    foreign = tmp_path / "foreign"
    foreign.mkdir(mode=0o700)
    state = tmp_path / "fixture"
    state.symlink_to(foreign, target_is_directory=True)
    with pytest.raises(ValueError, match="user-owned mode 0700"):
        private_directory(state)
    assert list(foreign.iterdir()) == []


def test_existing_public_fixture_directory_is_refused(tmp_path):
    state = tmp_path / "fixture"
    state.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="user-owned mode 0700"):
        private_directory(state)
    assert stat.S_IMODE(state.stat().st_mode) == 0o755


@pytest.mark.parametrize("name", contract.TEST_OUTPUT_FILE_ENV_KEYS)
def test_native_policy_output_paths_cannot_be_overridden(name):
    with pytest.raises(ValueError, match="protected test input"):
        contract.validate_options(["--test_env=" + name + "=/tmp/undeclared-output"])
