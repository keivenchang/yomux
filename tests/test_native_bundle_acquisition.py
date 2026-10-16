# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Exercise platform dependency inspection against actual executable bytes."""

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile

import pytest

from tools import native_bundle_acquire
from tools import bazel_execution_contract
from tools.native_bundle_acquire import extract_locked_archive, macho_output, macho_runtime_seed, relocate_macho
from yomux_lib.infra import host_identity
from yomux_lib.infra import filesystem_preflight


def compile_universal(*arguments):
    subprocess.run([os.environ.get("CC", "/usr/bin/clang"), "-arch", "arm64", "-arch", "x86_64",
                    *map(str, arguments)], check=True, capture_output=True)


@pytest.mark.parametrize("changed_version", [False, True])
def test_mac_agents_use_pinned_native_packages_without_postinstall(tmp_path, changed_version):
    modules = tmp_path / "agents/node_modules"
    packages = {
        "claude": ("@anthropic-ai/claude-code", "@anthropic-ai/claude-code-darwin-arm64", "claude"),
        "opencode": ("opencode-ai", "opencode-darwin-arm64", "bin/opencode"),
    }
    for name, (package, native_package, relative) in packages.items():
        parent = modules / package
        parent.mkdir(parents=True)
        (parent / "package.json").write_bytes(json.dumps({"optionalDependencies": {native_package: "1.2.3"}}).encode())
        native = modules / native_package
        native.mkdir(parents=True)
        (native / "package.json").write_bytes(json.dumps({"version": "9.9.9" if changed_version else "1.2.3"}).encode())
        binary = native / relative
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        # npm's skipped postinstall launcher cannot be executed directly.
        (parent / "placeholder").write_bytes(b'echo "postinstall was not run"\n')
    if changed_version:
        with pytest.raises(ValueError, match="differs from pinned"):
            native_bundle_acquire.mac_agent_executables(tmp_path)
    else:
        sources = native_bundle_acquire.mac_agent_executables(tmp_path)
        for name in packages:
            assert subprocess.run([sources[name]], check=False).returncode == 0
            target = tmp_path / "bin" / name
            target.parent.mkdir(exist_ok=True)
            target.symlink_to("obsolete-postinstall-launcher")
            native_bundle_acquire.executable(name, sources[name], tmp_path)
            native_bundle_acquire.executable(name, sources[name], tmp_path)
            assert target.resolve() == sources[name]
        assert sources["codex"] == modules / ".bin/codex"


@pytest.fixture
def xterm_archives(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    vendor = source / "static/vendor"
    vendor.mkdir(parents=True)
    packages = {"": {"dependencies": {"@xterm/xterm": "6.0.0", "@xterm/addon-unicode11": "0.9.0"}}}
    responses = {}
    expected = {}
    for package, version in packages[""]["dependencies"].items():
        content = io.BytesIO()
        with tarfile.open(fileobj=content, mode="w:gz") as archive:
            for name, relative in bazel_execution_contract.NATIVE_XTERM_ASSETS.items():
                prefix = package + "/"
                if not relative.startswith(prefix):
                    continue
                data = ("independent upstream archive: " + name).encode()
                expected[name] = data
                (vendor / name).write_bytes(data)
                member = tarfile.TarInfo("package/" + relative.removeprefix(prefix))
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        url = "https://registry.npmjs.org/" + package + "/-/fixture.tgz"
        responses[url] = content.getvalue()
        packages["node_modules/" + package] = {"version": version, "resolved": url,
            "integrity": "sha512-" + base64.b64encode(hashlib.sha512(content.getvalue()).digest()).decode()}
    (source / "package.json").write_text(json.dumps({"dependencies": packages[""]["dependencies"]}))
    (source / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": packages}))
    monkeypatch.setattr(native_bundle_acquire, "ROOT", source)
    monkeypatch.setattr(native_bundle_acquire, "SOURCE_LAYOUTS", {})
    monkeypatch.setattr(native_bundle_acquire.urllib.request, "urlopen", lambda url, **kwargs: io.BytesIO(responses[url]))
    return tmp_path / "bundle/payload", tmp_path / "stage", tmp_path / "unused-lock-helper", responses, expected


def test_xterm_preparation_extracts_integrity_locked_upstream_archives_and_resumes_offline(xterm_archives, monkeypatch):
    payload, stage, lock_helper, _responses, expected = xterm_archives
    native_bundle_acquire.prepare_xterm_fixture(payload, stage, lock_helper)
    assert {name: path.read_bytes() for name, path in bazel_execution_contract.xterm_fixture_paths(payload / "xterm/node_modules").items()} == expected

    def refuse_network(*args, **kwargs):
        pytest.fail("verified xterm archive was downloaded again")

    monkeypatch.setattr(native_bundle_acquire.urllib.request, "urlopen", refuse_network)
    native_bundle_acquire.prepare_xterm_fixture(payload, stage, lock_helper)


def test_xterm_preparation_refuses_archive_that_differs_from_npm_integrity(xterm_archives):
    payload, stage, lock_helper, responses, _expected = xterm_archives
    responses[next(iter(responses))] = b"different downloaded bytes"
    with pytest.raises(ValueError, match="differs from npm lock integrity"):
        native_bundle_acquire.prepare_xterm_fixture(payload, stage, lock_helper)
    assert not (payload / "xterm/node_modules").exists()


def test_xterm_preparation_cannot_modify_a_published_bundle(xterm_archives):
    payload, stage, lock_helper, _responses, _expected = xterm_archives
    payload.parent.mkdir()
    (payload.parent / "manifest.json").write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="frozen native bundle"):
        native_bundle_acquire.prepare_xterm_fixture(payload, stage, lock_helper)
    assert not payload.exists()


def test_linux_export_has_offline_localhost_resolution(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/hosts").write_bytes(b"")
    (tmp_path / "etc/resolv.conf").write_bytes(b"nameserver 8.8.8.8\n")
    native_bundle_acquire.prepare_linux_network_files(tmp_path)
    assert (tmp_path / "etc/hosts").read_bytes() == b"127.0.0.1 localhost\n::1 localhost\n"
    assert (tmp_path / "etc/resolv.conf").read_bytes() == b""


@pytest.mark.skipif(platform.system() != "Darwin", reason="Mach-O inspection uses the macOS SDK")
def test_macho_helper_parentheses_are_not_archive_member_syntax(tmp_path):
    executable = tmp_path / "Browser Helper (Alerts)"
    shutil.copyfile("/usr/bin/true", executable)
    before = hashlib.sha256(executable.read_bytes()).hexdigest()
    dependencies = macho_output(["-L"], executable)
    assert "/usr/lib/libSystem.B.dylib" in dependencies
    assert hashlib.sha256(executable.read_bytes()).hexdigest() == before


@pytest.mark.skipif(platform.system() != "Darwin", reason="platform signatures are macOS-specific")
def test_copied_platform_utility_can_execute_outside_system_location(tmp_path, monkeypatch):
    executable = tmp_path / "true"
    shutil.copyfile("/usr/bin/true", executable)
    executable.chmod(0o755)
    monkeypatch.setattr(native_bundle_acquire, "SOURCE_LAYOUTS", {executable: Path("/usr/bin/true")})
    relocate_macho(tmp_path)
    subprocess.run([str(executable)], check=True, capture_output=True)


@pytest.mark.skipif(platform.system() != "Darwin", reason="platform utilities are macOS-specific")
def test_signed_platform_utilities_execute_from_bundle(tmp_path, monkeypatch):
    tools = bazel_execution_contract.NATIVE_DARWIN_PLATFORM_TOOLS
    assert set(tools) == {"ioreg", "mount", "sysctl", "system_profiler"}
    assert set(tools).issubset(bazel_execution_contract.required_native_tools("Darwin"))
    assert set(tools).intersection(bazel_execution_contract.required_native_tools("Linux")) == {"mount"}
    layouts = {}
    for name, source in tools.items():
        destination = tmp_path / name
        shutil.copyfile(source, destination)
        destination.chmod(0o555)
        layouts[destination] = Path(source)
    monkeypatch.setattr(native_bundle_acquire, "SOURCE_LAYOUTS", layouts)
    relocate_macho(tmp_path)

    def copied_runner(command, **kwargs):
        return subprocess.run([str(tmp_path / command[0]), *command[1:]], **kwargs)

    assert host_identity._darwin_stable_host_id(runner=copied_runner) == host_identity._darwin_stable_host_id()
    assert host_identity._darwin_boot_id(runner=copied_runner) == host_identity._darwin_boot_id()
    hardware_types = subprocess.check_output([str(tmp_path / "system_profiler"), "-listDataTypes"], text=True, timeout=10)
    assert "SPHardwareDataType" in hardware_types
    filesystem = filesystem_preflight._classify_darwin_filesystem(tmp_path, runner=copied_runner)
    assert filesystem.determined
    assert filesystem == filesystem_preflight._classify_darwin_filesystem(tmp_path)


@pytest.mark.skipif(platform.system() != "Darwin", reason="Mach-O inspection uses the macOS SDK")
def test_universal_executable_architecture_titles_are_not_dependencies(tmp_path):
    executable = tmp_path / "Git Helper (Alerts)"
    source = tmp_path / "main.c"
    source.write_bytes(b"int main(void){return 0;}\n")
    compile_universal(source, "-o", executable)
    before = hashlib.sha256(executable.read_bytes()).hexdigest()
    relocate_macho(tmp_path)
    assert hashlib.sha256(executable.read_bytes()).hexdigest() == before


def test_java_class_magic_is_not_a_universal_macho_binary(tmp_path):
    bytecode = tmp_path / "fixture.class"
    content = bytes.fromhex("cafebabe0000003400000000")
    bytecode.write_bytes(content)
    bytecode.chmod(0o555)
    relocate_macho(tmp_path)
    assert bytecode.read_bytes() == content


def test_compiler_relocation_starts_from_declared_tools(tmp_path):
    assert macho_runtime_seed(tmp_path / "compiler/bin/clang", tmp_path)
    assert macho_runtime_seed(tmp_path / "compiler/libexec/git-core/git-http-fetch", tmp_path)
    assert not macho_runtime_seed(tmp_path / "compiler/bin/xcindex-test", tmp_path)
    assert not macho_runtime_seed(tmp_path / "compiler/lib/swift/libswiftCore.dylib", tmp_path)
    assert not macho_runtime_seed(tmp_path / "sdk/System/Library/fixture.dylib", tmp_path)


def test_preparation_archive_strips_source_prefix(tmp_path):
    source = tmp_path / "source.tar"
    with tarfile.open(source, "w") as archive:
        member = tarfile.TarInfo("revision/modules/example/MODULE.bazel")
        content = b'module(name = "example")\n'
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    destination = tmp_path / "registry"
    extract_locked_archive(source, destination, None, strip_components=1)
    assert (destination / "modules/example/MODULE.bazel").read_bytes() == content
    assert not (destination / "revision").exists()


@pytest.mark.skipif(platform.system() != "Darwin", reason="Mach-O inspection uses the macOS SDK")
def test_relocated_rpath_survives_removal_of_original_library(tmp_path, monkeypatch):
    source = tmp_path.resolve() / "source"
    (source / "lib").mkdir(parents=True)
    (source / "bin").mkdir()
    (source / "library.c").write_bytes(b"int value(void){return 0;}\n")
    (source / "main.c").write_bytes(b"int value(void); int main(void){return value();}\n")
    library = source / "lib/libaudit.dylib"
    compile_universal("-dynamiclib", source / "library.c", "-Wl,-install_name,@rpath/libaudit.dylib", "-o", library)
    compile_universal(source / "main.c", "-L" + str(source / "lib"), "-laudit",
                      "-Wl,-rpath," + str(source / "lib"), "-o", source / "bin/audit")
    payload = tmp_path.resolve() / "payload"
    shutil.copytree(source, payload / "tree")
    monkeypatch.setattr(native_bundle_acquire, "SOURCE_LAYOUTS", {payload / "tree": source})
    relocate_macho(payload)
    library.unlink()
    subprocess.run([str(payload / "tree/bin/audit")], check=True, capture_output=True)
    assert str(source / "lib") not in native_bundle_acquire.macho_output(["-l"], payload / "tree/bin/audit")
