#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared execution policy, Docker admission, and offline native bundle verification."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from email.parser import BytesParser
from functools import partial
import hashlib
from itertools import islice
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import shutil
import stat
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yomux_lib.local_services.runtime import redact_local_service_text
from tools.product_environment import product_env_names, product_env, product_env_name
from tools.instance_isolation import INHERITED_CREDENTIAL_ENV_KEYS, PRODUCT_ROOT_KEYS
from tools import bazel_native
from tools.native_process_scope import native_execution_scope, TEST_OUTPUT_FILE_ENV_KEYS
from tools.tmux_paths import NATIVE_ACTION_TMPDIR
# Based on moby/moby v28.0.0 profiles/seccomp/default.json. Added namespace
# operations need capabilities inside a newly created user namespace; the outer
# non-root container drops every capability. clone3 retains the ENOSYS fallback.
SANDBOX_FLAGS = (
    "--spawn_strategy=linux-sandbox",
    "--strategy=TestRunner=linux-sandbox",
    "--experimental_use_hermetic_linux_sandbox",
    "--sandbox_default_allow_network=false",
    "--sandbox_enable_loopback_device",
    "--sandbox_explicit_pseudoterminal",
    "--run_under=//:bazel_test_entrypoint",
    "--sandbox_fake_hostname",
    "--incompatible_strict_action_env",
    "--action_env=PATH=/opt/yomux-agent-clients/node_modules/.bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin",
    "--action_env=HOME=/home/runner",
    "--action_env=TZ=America/Los_Angeles",
    "--test_env=TZ=America/Los_Angeles",
    "--test_env=CHROME_BIN=/usr/bin/chromium",
    "--test_env=CHROMEDRIVER_BIN=/usr/bin/chromedriver",
    *("--sandbox_add_mount_pair=" + path for path in
      ("/usr", "/bin", "/lib", "/lib64", "/etc", "/opt")),
)
OUTPUT_USER_ROOT_SLOTS = 8
EXECUTION_MODES = ("auto", "docker", "native", "qualified-native")


def repository_git_environment():
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def repository_head_sha(root=ROOT, *, declared=False):
    if (declared and Path(root).resolve() == ROOT and product_env("BAZEL_CONTRACT")
            and product_env("BAZEL_EXECUTION") == "qualified-native"):
        revision = product_env("TEST_REVISION", "")
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("declared checkout revision is missing or invalid")
        return revision
    try:
        result = subprocess.run(["git", "-c", "safe.directory=" + str(root), "-C", str(root), "rev-parse", "HEAD"],
            env=repository_git_environment(), capture_output=True, text=True, timeout=5, check=False)
    except OSError:
        return None
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", revision) else None


def repository_release_context(root=ROOT, *, declared=False):
    if (declared and Path(root).resolve() == ROOT and product_env("BAZEL_CONTRACT")
            and product_env("BAZEL_EXECUTION") == "qualified-native"):
        context = json.loads(product_env("TEST_RELEASE_CONTEXT", ""))
        revision = repository_head_sha(root, declared=True)
        if (not isinstance(context, dict) or context.get("revision") != revision
                or not isinstance(context.get("head_version_source"), str)
                or not isinstance(context.get("commit_subject"), str)
                or not isinstance(context.get("tags"), dict)):
            raise ValueError("declared release context is invalid or belongs to another revision")
        for tag, information in context["tags"].items():
            if (not re.fullmatch(r"v\d+\.\d+\.\d+", tag) or not isinstance(information, dict)
                    or not isinstance(information.get("type"), str)
                    or not isinstance(information.get("subject"), str)):
                raise ValueError("declared release tag context is invalid")
        return context

    revision = repository_head_sha(root)
    if revision is None:
        raise ValueError("release context requires observable checkout revision")

    def git_output(*arguments):
        return subprocess.run(["git", "-c", "safe.directory=" + str(root), "-C", str(root), *arguments],
                              env=repository_git_environment(), capture_output=True, text=True,
                              timeout=5, check=True).stdout

    tags = sorted(tag for tag in git_output("tag", "--points-at", revision).splitlines()
                  if re.fullmatch(r"v\d+\.\d+\.\d+", tag))
    try:
        version_source = git_output("show", revision + ":version.yaml")
    except subprocess.CalledProcessError:
        version_source = (root / "version.yaml").read_text(encoding="utf-8")
    context = {
        "revision": revision,
        "head_version_source": version_source,
        "commit_subject": git_output("show", "-s", "--format=%s", revision).strip(),
        "tags": {tag: {"type": git_output("cat-file", "-t", "refs/tags/" + tag).strip(),
                       "subject": git_output("for-each-ref", "--format=%(contents:subject)", "refs/tags/" + tag).strip()}
                 for tag in tags},
    }
    if repository_head_sha(root) != revision:
        raise ValueError("checkout revision changed while collecting release context")
    return context

# Bundle verification precedes sandbox admission. The runtime toolchain declares
# every locked leaf, including these executable entrypoints.
NATIVE_REQUIRED_TOOLS = frozenset({
    "python3", "node", "git", "tmux", "chrome", "chromedriver", "claude",
    "codex", "opencode", "buildifier", "bash", "cc", "bazel", "go",
    "env", "mkdir", "cp", "mv", "rm", "readlink", "dirname", "mktemp", "chmod",
    "find", "tar", "sort", "comm", "cmp", "diff", "sed", "awk", "cat",
    "grep", "timeout", "ps", "kill", "ln", "uname", "id", "getconf",
    "basename", "sleep", "printenv", "rmdir", "realpath", "stat", "touch",
    "date", "wc", "head", "tail", "tee", "xargs", "cut", "tr", "printf", "openssl", "curl",
    "df", "sh", "true", "false", "seq", "whoami", "stty", "hostname", "nohup",
    "mypy",
    "ar", "as", "ld", "nm", "ranlib", "strip", "flock",
})
NATIVE_LINUX_TOOLS = frozenset({"unshare", "mount", "setpriv", "setsid", "cpp", "gcov", "objcopy", "objdump", "paste", "sha256sum"})
NATIVE_DARWIN_PLATFORM_TOOLS = {
    "ioreg": "/usr/sbin/ioreg",
    "mount": "/sbin/mount",
    "sysctl": "/usr/sbin/sysctl",
    "system_profiler": "/usr/sbin/system_profiler",
}
NATIVE_DARWIN_TOOLS = frozenset({"otool", "install_name_tool", "codesign", "codesign_allocate", "lipo", *NATIVE_DARWIN_PLATFORM_TOOLS})
NATIVE_SEED_DIRECTORIES = ("go-mod-cache", "bazel-repository-cache", "bazel-registry")
NATIVE_XTERM_ASSETS = {
    "xterm.js": "@xterm/xterm/lib/xterm.js",
    "xterm.css": "@xterm/xterm/css/xterm.css",
    "xterm-addon-unicode11.js": "@xterm/addon-unicode11/lib/addon-unicode11.js",
}


def xterm_fixture_paths(package_root):
    return {name: Path(package_root) / relative for name, relative in NATIVE_XTERM_ASSETS.items()}


def native_xterm_package_root(root, system):
    return Path(root) / ("rootfs/opt/xterm/node_modules" if system == "Linux" else "xterm/node_modules")


def required_native_tools(system):
    return NATIVE_REQUIRED_TOOLS | (NATIVE_LINUX_TOOLS if system == "Linux" else NATIVE_DARWIN_TOOLS)


@dataclass(frozen=True)
class NativeBundle:
    root: Path
    manifest_digest: str
    system: str
    machine: str
    executables: dict[str, Path]


def _bundle_path(name):
    if not isinstance(name, str):
        raise ValueError("bundle paths must be strings")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or str(path) != name:
        raise ValueError("non-canonical bundle path: " + name)
    return path


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_package_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _raise_bundle_walk_error(error):
    raise error


def _verify_native_leaf(root, item):
    name, record = item
    path = root / _bundle_path(name)
    if not isinstance(record, dict):
        raise ValueError("invalid native bundle file record: " + name)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(path)
        if record != {"symlink": target} or PurePosixPath(target).is_absolute():
            raise ValueError("native bundle symlink differs from its lock: " + name)
        # Minimal distributions retain optional documentation and virtual /proc
        # links. Absent targets stay locked; required tools must still exist.
        if not path.resolve(strict=False).is_relative_to(root):
            raise ValueError("native bundle symlink escapes its payload: " + name)
    elif stat.S_ISREG(metadata.st_mode):
        mode = stat.S_IMODE(metadata.st_mode)
        if mode & 0o222 or mode & 0o7000 or record != {"sha256": _sha256_file(path), "mode": mode}:
            raise ValueError("native bundle file bytes or mode differ from its lock: " + name)
    else:
        raise ValueError("native bundle contains a non-file input: " + name)


def native_input_results(function, items):
    # Bound both concurrent file opens and queued records for SDK inventories.
    records = iter(items)
    with ThreadPoolExecutor(max_workers=8) as executor:
        while batch := list(islice(records, 256)):
            yield from executor.map(function, batch)


def verify_native_bundle(root, manifest_path, expected_digest, *, system=None, machine=None):
    """Verify a prepared bundle offline; this does not qualify execution.

    The manifest is independently checksum-locked and kept outside its payload.
    Every leaf is declared, including relative symlinks and wheel metadata. No
    executable runs during this check, and nothing is installed or downloaded.
    Native sandbox probes and read-only mounts are still required before reuse.
    """
    if not isinstance(expected_digest, str) or len(expected_digest) != 64 or any(
            char not in "0123456789abcdef" for char in expected_digest):
        raise ValueError("a checksum-locked native bundle manifest is required")
    root = Path(root).resolve(strict=True)
    manifest_path = Path(manifest_path).resolve(strict=True)
    if manifest_path.is_relative_to(root):
        raise ValueError("native bundle manifest must be outside the payload")
    data = manifest_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_digest:
        raise ValueError("native bundle manifest checksum differs from its lock")
    manifest = json.loads(data)
    if not isinstance(manifest, dict):
        raise ValueError("native bundle manifest must be an object")
    system = system or platform.system()
    machine = machine or platform.machine()
    if system not in {"Linux", "Darwin"}:
        raise ValueError("qualified native bundles support Linux and macOS only")
    if manifest.get("schema") != 1 or manifest.get("platform") != {"system": system, "machine": machine}:
        raise ValueError("native bundle schema or platform differs from this host")
    files = manifest.get("files")
    locked_directories = manifest.get("directories")
    executables = manifest.get("executables")
    if (not isinstance(files, dict) or not files or not isinstance(locked_directories, dict)
            or not locked_directories or not isinstance(executables, dict)):
        raise ValueError("native bundle file, directory, and executable inventories are required")
    required = required_native_tools(system)
    if missing := required - executables.keys():
        raise ValueError("required native tools are missing: " + ", ".join(sorted(missing)))
    actual = set()
    actual_directories = {}
    for directory, directories, leaves in os.walk(root, followlinks=False, onerror=_raise_bundle_walk_error):
        metadata = Path(directory).lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if not stat.S_ISDIR(metadata.st_mode) or mode & 0o7022:
            raise ValueError("native bundle directory is not controlled by its owner: " + directory)
        actual_directories[Path(directory).relative_to(root).as_posix()] = mode
        for name in list(directories):
            if (Path(directory) / name).is_symlink():
                directories.remove(name)
                leaves.append(name)
        for name in leaves:
            actual.add((Path(directory) / name).relative_to(root).as_posix())
    if actual != set(files):
        raise ValueError("native bundle file inventory differs from its lock")
    if actual_directories != locked_directories:
        raise ValueError("native bundle directory inventory differs from its lock")
    for _result in native_input_results(partial(_verify_native_leaf, root), files.items()):
        pass
    for name, path in xterm_fixture_paths(native_xterm_package_root(root, system)).items():
        if path.relative_to(root).as_posix() not in files or not path.is_file():
            raise ValueError("required native xterm package fixture is missing: " + name)
        if _sha256_file(path) != _sha256_file(ROOT / "static/vendor" / name):
            raise ValueError("native xterm package fixture differs from tracked vendor asset: " + name)
    resolved_tools = {}
    for name, relative in executables.items():
        path = root / _bundle_path(relative)
        if (relative not in files or not path.is_file() or not path.stat().st_mode & 0o111
                or not os.access(path, os.X_OK)):
            raise ValueError("native bundle executable is absent or not executable: " + name)
        resolved_tools[name] = path.resolve(strict=True)
    for name in NATIVE_SEED_DIRECTORIES:
        directory = root / name
        prefix = directory.resolve().relative_to(root).as_posix() + "/"
        if not directory.is_dir() or not any(path.startswith(prefix) for path in files):
            raise ValueError("native dependency seed is missing: " + name)
    packages = manifest.get("python_packages")
    if not isinstance(packages, dict):
        raise ValueError("native Python package inventory is missing")
    normalized_packages = {canonical_package_name(name): path for name, path in packages.items()}
    if len(normalized_packages) != len(packages):
        raise ValueError("native Python package inventory has duplicate normalized names")
    for requirement in (ROOT / "docker/requirements-test.txt").read_text().splitlines():
        if not requirement or requirement.startswith("#"):
            continue
        name, version = requirement.split("==")
        relative = normalized_packages.get(canonical_package_name(name))
        if not isinstance(relative, str) or relative not in files:
            raise ValueError("required native Python package is missing: " + name)
        metadata = BytesParser().parsebytes((root / _bundle_path(relative)).read_bytes())
        if metadata["Name"] is None or canonical_package_name(metadata["Name"]) != canonical_package_name(name) or metadata["Version"] != version:
            raise ValueError("native Python package differs from its pin: " + name)
    return NativeBundle(root, expected_digest, system, machine, resolved_tools)


def native_flags(bundle, credentials):
    """The qualified runner supplies all policy flags after rejecting overrides."""
    sandbox = "linux-sandbox" if bundle.system == "Linux" else "processwrapper-sandbox"
    flags = [
        "--spawn_strategy=" + sandbox,
        "--strategy=TestRunner=" + sandbox,
        "--sandbox_default_allow_network=false",
        "--incompatible_strict_action_env",
        "--run_under=//:native_test_entrypoint",
        "--shell_executable=" + str(bundle.executables["bash"]),
        "--action_env=SHELL=" + str(bundle.executables["bash"]),
        "--test_env=SHELL=" + str(bundle.executables["bash"]),
        "--action_env=PATH=" + str(bundle.root / "bin"),
        "--test_env=PATH=" + str(bundle.root / "bin"),
        "--action_env=TZ=America/Los_Angeles", "--test_env=TZ=America/Los_Angeles",
        "--test_env=CHROME_BIN=" + str(bundle.executables["chrome"]),
        "--test_env=CHROMEDRIVER_BIN=" + str(bundle.executables["chromedriver"]),
        "--repo_env=YOMUX_NATIVE_BUNDLE=" + str(bundle.root),
        "--repo_env=YOMUX_DIAGNOSTIC_PYTHON=",
        "--repository_disable_download",
        "--lockfile_mode=off",
        "--registry=file://" + str(bundle.root / "bazel-registry"),
        "--repo_env=GOPROXY=off", "--repo_env=GOSUMDB=off",
        "--repo_env=GOMODCACHE=" + str(bundle.root / "go-mod-cache"),
        "--repo_env=GO_REPOSITORY_USE_HOST_MODCACHE=1",
        "--repo_env=CC=" + str(bundle.executables["cc"]),
        "--action_env=CC=" + str(bundle.executables["cc"]),
        "--test_env=CC=" + str(bundle.executables["cc"]),
    ]
    flags.extend("--test_env=" + name + "=" + str(native_xterm_package_root(bundle.root, bundle.system))
                 for name in product_env_names("XTERM_PACKAGE_ROOT"))
    for name, value in {"GOWORK": "off", "GO111MODULE": "on", "GOTOOLCHAIN": "local",
                        "GOPROXY": "off", "GOSUMDB": "off",
                        "GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1",
                        "GOMODCACHE": str(bundle.root / "go-mod-cache")}.items():
        flags.extend(("--action_env=" + name + "=" + value, "--test_env=" + name + "=" + value))
    for setting, value in {"BAZEL_EXECUTION": "qualified-native", "TEST_CONTAINER": "0"}.items():
        for name in product_env_names(setting):
            flags.append("--test_env=" + name + "=" + value)
    if bundle.system == "Linux":
        flags.extend("--sandbox_block_path=" + str(path) for path in credentials if path.exists())
        flags.extend(("--action_env=TMPDIR=/tmp", "--host_action_env=TMPDIR=/tmp", "--experimental_use_hermetic_linux_sandbox", "--sandbox_enable_loopback_device",
                      "--sandbox_explicit_pseudoterminal", "--sandbox_fake_hostname",
                      "--sandbox_tmpfs_path=/tmp", "--sandbox_add_mount_pair=/tmp:/tmp",
                      "--sandbox_tmpfs_path=" + NATIVE_ACTION_TMPDIR,
                      "--sandbox_add_mount_pair=" + NATIVE_ACTION_TMPDIR + ":" + NATIVE_ACTION_TMPDIR))
        # Bazel 9.2 mounts tmpfs before pivot_root. Bind that fresh action-local
        # mount into the hermetic root as well, or its /tmp stays disk-backed.
        filesystem = bundle.root / "rootfs"
        for path in ("usr", "bin", "lib", "lib64", "etc", "opt"):
            source = filesystem / path
            if not source.exists():
                raise ValueError("native Linux runtime filesystem is incomplete: " + path)
            flags.append("--sandbox_add_mount_pair=" + str(source) + ":/" + path)
        flags.append("--sandbox_add_mount_pair=" + str(bundle.root))
    else:
        flags.extend(("--action_env=CODESIGN_ALLOCATE=" + str(bundle.executables["codesign_allocate"]),
                      "--test_env=CODESIGN_ALLOCATE=" + str(bundle.executables["codesign_allocate"])))
        sdk = json.loads((bundle.root.parent / "manifest.json").read_bytes()).get("sdk")
        actual = {
            "os": subprocess.check_output(["/usr/bin/sw_vers", "-buildVersion"], text=True).strip(),
            "path": subprocess.check_output(["/usr/bin/xcrun", "--show-sdk-path"], text=True).strip(),
            "version": subprocess.check_output(["/usr/bin/xcrun", "--show-sdk-version"], text=True).strip(),
        }
        if sdk != actual:
            raise ValueError("macOS/Xcode SDK identity differs from the prepared bundle")
        flags.extend(("--repo_env=SDKROOT=" + str(bundle.root / "sdk"),
                      "--action_env=SDKROOT=" + str(bundle.root / "sdk"),
                      "--action_env=GIT_EXEC_PATH=" + str(bundle.root / "compiler/libexec/git-core"),
                      "--test_env=GIT_EXEC_PATH=" + str(bundle.root / "compiler/libexec/git-core"),
                      "--action_env=GIT_TEMPLATE_DIR=" + str(bundle.root / "compiler/share/git-core/templates"),
                      "--test_env=GIT_TEMPLATE_DIR=" + str(bundle.root / "compiler/share/git-core/templates")))
    return flags


def native_identity(bundle, flags, source_root=None):
    source_root = source_root or ROOT
    source_inputs = ["tools/bazel_execution_contract.py", "tools/bazel_native.py",
                     "tools/native_linux_envelope.py", "tools/native_darwin_launcher.py",
                     "tools/bazel_native_probe.py", "tools/bazel_native_entrypoint.py",
                     "tools/native_process_scope.py", "tests/browser_helpers/webdriver_lease.py",
                     "yomux_lib/infra/host_identity.py", "yomux_lib/infra/filesystem_preflight.py", "yomux_lib/infra/transient_paths.py",
                     "tools/instance_isolation.py", "tools/tmux_paths.py", "tools/product_environment.py", APPARMOR_TEMPLATE,
                     "tools/bazel_test_entrypoint.sh", "tools/bazel_test_common.sh",
                     "tools/bazel_rules.bzl", "tools/bazel_runtime_toolchain.bzl", "tools/bazel_generate.py", "tests/helpers/fixture_tools.py"]
    contract = {"schema": 1, "backend": "qualified-native", "bundle": bundle.manifest_digest,
                "platform": (bundle.system, bundle.machine, platform.release()), "policy": flags,
                "execution_scope": native_execution_scope(bundle.system),
                "source": {name: _sha256_file(source_root / name) for name in source_inputs}}
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()


def validate_native_probe(record, system):
    common = {"external_network_blocked", "operator_home_blocked", "operator_home_writes_blocked", "tool_runtime_readonly", "credential_environment_scrubbed", "fixture_state_private", "fixture_writes_isolated", "pty_usable"}
    host = {"undeclared_host_reads_blocked", "host_localhost_isolated", "host_processes_isolated", "fixture_storage_tmpfs"}
    if not isinstance(record, dict) or set(record) != common | host:
        raise ValueError("native sandbox probe returned an incomplete guarantee inventory")
    for key in common | (host if system == "Linux" else set()):
        if record[key] is not True:
            raise ValueError("native sandbox guarantee failed: " + key)
    if not all(isinstance(record[key], bool) for key in host):
        raise ValueError("native sandbox probe did not measure host isolation")


APPARMOR_TEMPLATE = "docker/sandbox-apparmor.profile"


def apparmor_profile(root=ROOT):
    policy = (root / APPARMOR_TEMPLATE).read_bytes()
    return "yomux-bazel-" + hashlib.sha256(policy).hexdigest()[:24]


def verify_apparmor(install=False, *, root=ROOT):
    # Compare the kernel's exported compiled policy with freshly compiled source;
    # a matching profile name alone cannot establish its effective permissions.
    name = apparmor_profile(root)
    with tempfile.TemporaryDirectory(prefix="yomux-apparmor-") as directory:
        policy = Path(directory) / "profile"
        policy.write_text((root / APPARMOR_TEMPLATE).read_text().replace("@YOMUX_PROFILE@", name))
        compiled = subprocess.check_output(["apparmor_parser", "-S", "-K", str(policy)], stderr=subprocess.PIPE)
        if install:
            subprocess.run(["sudo", "-n", "apparmor_parser", "-r", "-K", str(policy)], check=True)
        read_policy = """import pathlib,sys
for path in pathlib.Path('/sys/kernel/security/apparmor/policy/profiles').iterdir():
 if (path/'name').read_text().strip() == sys.argv[1]:
  print((path/'mode').read_text().strip())
  print((path/'raw_sha256').read_text().strip())
  break
else:
 raise SystemExit('required YOmux AppArmor profile is not loaded')
"""
        actual = subprocess.check_output(["sudo", "-n", "python3", "-I", "-c", read_policy, name], stderr=subprocess.PIPE, text=True).splitlines()
        if actual != ["enforce", hashlib.sha256(compiled).hexdigest()]:
            raise ValueError("loaded AppArmor policy differs from compiled source")
    return name, hashlib.sha256(compiled).hexdigest()


def execution_event(**fields):
    print("YOMUX_EXECUTION " + json.dumps(fields, sort_keys=True), flush=True)


def identity(image_id, root=ROOT, apparmor_digest=None):
    policy = (root / "docker/sandbox-seccomp.json").read_bytes()
    runner_files = (
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
    contract = {
        "schema": 2, "apparmor": apparmor_profile(root),
        "apparmor_compiled": apparmor_digest or product_env("BAZEL_APPARMOR_SHA256", "unqualified"), "systempaths": "unmasked-for-private-proc-remount", "image": image_id, "sandbox": SANDBOX_FLAGS,
        "seccomp": hashlib.sha256(policy).hexdigest(),
        "runner": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in runner_files},
        "platform": (platform.system(), platform.machine(), platform.release()),
        "container": "read-only,non-root,cap-drop-all,no-new-privileges,network-none",
        "mounts": "checkout-ro,action-cache-rw,container-locked-output-slots,tmp-private,home-private,no-git-metadata",
        "output_root_ownership": f"{OUTPUT_USER_ROOT_SLOTS}-stable-slots-container-flock",
    }
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()


def inspect_container(record, image_id, contract, root, cache, *output_roots):
    config, host = record["Config"], record["HostConfig"]
    policy = json.loads((ROOT / "docker/sandbox-seccomp.json").read_text())
    security = host.get("SecurityOpt", [])
    seccomp_options = [value.removeprefix("seccomp=") for value in security if value.startswith("seccomp=")]
    actual_policy = None
    if len(seccomp_options) == 1:
        try:
            actual_policy = json.loads(seccomp_options[0])
        except json.JSONDecodeError:
            pass
    if (set(value for value in security if not value.startswith("seccomp=")) !=
            {"no-new-privileges", "apparmor=" + apparmor_profile()} or record.get("AppArmorProfile") != apparmor_profile() or actual_policy != policy or
            host.get("MaskedPaths") or host.get("ReadonlyPaths")):
        raise ValueError("pinned namespace sandbox policy is missing or changed")
    environment = dict(value.split("=", 1) for value in config.get("Env", []))
    if product_env("BAZEL_APPARMOR_SHA256", "unqualified", environ=environment) != product_env("BAZEL_APPARMOR_SHA256", "unqualified"):
        raise ValueError("incorrect compiled AppArmor policy identity")
    if record["Image"] != image_id or config.get("Labels", {}).get("yomux.bazel.contract") != contract:
        raise ValueError("incorrect image or execution contract identity")
    if (host["NetworkMode"] != "none" or host["Privileged"] or
            not host["ReadonlyRootfs"] or host.get("CapAdd") or
            "ALL" not in host.get("CapDrop", []) or
            "no-new-privileges" not in host.get("SecurityOpt", [])):
        raise ValueError("container isolation configuration differs from the contract")
    if config["User"] in ("", "0", "root", "0:0"):
        raise ValueError("test container must use the image's non-root runner")
    for entry in config.get("Env", []):
        if entry.partition("=")[0] in INHERITED_CREDENTIAL_ENV_KEYS:
            raise ValueError("host credentials were forwarded")
    expected = {"/w": (str(Path(root).resolve()), False)}
    if cache:
        expected["/var/cache/yomux-bazel"] = (str(Path(cache).resolve()), True)
    for index, output_root in enumerate(output_roots):
        if output_root:
            # A rootless daemon maps the runner UID to a host subordinate UID. The
            # host process cannot traverse the mode-0700 cache partition, so compare
            # generated bind paths lexically and verify ownership in the container.
            expected[f"/var/cache/yomux-bazel-output-slot-{index}"] = (os.path.abspath(output_root), True)
    if any(m["Type"] not in {"bind", "tmpfs"} for m in record["Mounts"]):
        raise ValueError("unexpected volume or host state mount")
    actual = {m["Destination"]: (m["Source"], m["RW"]) for m in record["Mounts"] if m["Type"] == "bind"}
    if set(actual) != set(expected) or any(
            rw != expected[path][1] or
            (os.path.abspath(source) != expected[path][0] if path.startswith("/var/cache/yomux-bazel-output-slot-")
             else not os.path.samefile(source, expected[path][0]))
            for path, (source, rw) in actual.items()):
        raise ValueError(f"unexpected bind mounts (host state or Git metadata): {actual} expected {expected}")
    if set(host.get("Tmpfs", {})) != {"/tmp", "/home/runner"}:
        raise ValueError("private writable runtime mounts are missing")


def admit_runtime():
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("Docker Bazel contract requires Linux x86_64")
    image = product_env('BAZEL_IMAGE_ID', "", environ=os.environ)
    if not image.startswith("sha256:") or len(image) != 71:
        raise ValueError("immutable image digest is missing")
    policy_digest = product_env("BAZEL_APPARMOR_SHA256", "")
    if len(policy_digest) != 64 or any(char not in "0123456789abcdef" for char in policy_digest):
        raise ValueError("verified compiled AppArmor policy digest is missing")
    if product_env('BAZEL_CONTRACT', environ=os.environ) != identity(image):
        raise ValueError("execution contract identity differs from mounted source")
    if os.getuid() == 0:
        raise ValueError("test container must be non-root")
    status = Path("/proc/self/status").read_text()
    for line in status.splitlines():
        if line.startswith("CapEff:") and int(line.split()[1], 16):
            raise ValueError("outer container has effective capabilities")
        if line.startswith("NoNewPrivs:") and line.split()[1] != "1":
            raise ValueError("no-new-privileges is missing")
    if {p.name for p in Path("/sys/class/net").iterdir()} != {"lo"}:
        raise ValueError("external network interface is visible")
    for name in INHERITED_CREDENTIAL_ENV_KEYS:
        if name in os.environ:
            raise ValueError("host credentials were forwarded: " + name)
    # Exercise the actual Bazel helper before any cache lookup, including the
    # same private PTY setup as every test. No test-result cache can admit a host.
    with tempfile.TemporaryDirectory(prefix="yomux-bazel-admission-") as directory:
        root = Path(directory)
        output = root / "output"
        subprocess.run(["bazel", "--batch", "--bazelrc=/dev/null",
                        "--output_user_root=" + str(output), "help"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=60)
        helper = next((output / "install").glob("*/linux-sandbox"))
        sandbox = root / "sandbox"
        work = sandbox / "execroot/_main"
        work.mkdir(parents=True)
        shutil.copyfile(ROOT / "tools/bazel_test_entrypoint.sh", work / "entrypoint.sh")
        command = [str(helper), "-h", str(sandbox), "-W", str(work), "-N", "-H", "-P"]
        for flag in SANDBOX_FLAGS:
            if flag.startswith("--sandbox_add_mount_pair="):
                command.extend(("-M", flag.partition("=")[2]))
        command.extend(("--", "/bin/bash", "./entrypoint.sh", "python3", "-c", RUNTIME_PROBE))
        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        except subprocess.CalledProcessError as error:
            raise ValueError("linux-sandbox runtime probe exited " + str(error.returncode) + ": " + redact_local_service_text(error.stderr.decode(errors="replace"))) from error


RUNTIME_PROBE = f"""
import os, pathlib, socket
assert os.getuid() != 0, 'sandbox changed the runner UID'
for path in ('/w', '/home/runner', '/var/cache/yomux-bazel',
             *tuple(f"/var/cache/yomux-bazel-output-slot-{{index}}" for index in range({OUTPUT_USER_ROOT_SLOTS}))):
    assert not pathlib.Path(path).exists(), 'undeclared input visible: ' + path
status = pathlib.Path('/proc/self/status').read_text()
for field in ('CapEff:', 'CapPrm:', 'CapInh:', 'CapAmb:'):
    assert int(next(row.split()[1] for row in status.splitlines() if row.startswith(field)), 16) == 0, field
master, slave = os.openpty()
os.write(master, b'private-pty\\n')
assert os.read(slave, 64) == b'private-pty\\n'
os.close(master); os.close(slave)
with socket.socket() as listener:
    listener.bind(('127.0.0.1', 0)); listener.listen()
    with socket.create_connection(listener.getsockname()) as client:
        connection, _ = listener.accept(); connection.close()
try:
    socket.create_connection(('198.51.100.1', 80), 0.1)
except OSError:
    pass
else:
    raise AssertionError('external networking is available')
for root in ('/usr', '/bin', '/lib', '/lib64', '/etc', '/opt'):
    try:
        pathlib.Path(root + '/yomux-admission-write-probe').write_bytes(b'probe')
    except OSError:
        pass
    else:
        raise AssertionError('image tools are writable: ' + root)
"""



def command_arguments(arguments):
    return arguments[1:] if arguments[:1] == ["--"] else arguments


def validate_options(arguments):
    value_options = {"--test_arg", "--test_env", "--test_filter", "--test_tag_filters", "--test_size_filters",
                     "--test_timeout_filters", "--test_lang_filters", "--test_output", "--test_summary", "--test_timeout",
                     "--cache_test_results", "--jobs", "--local_resources", "--color", "--curses", "--show_result"}
    switches = {"--keep_going", "--nokeep_going", "--verbose_failures", "--noverbose_failures", "--show_progress",
                "--noshow_progress", "--show_timestamps", "--noshow_timestamps", "--announce_rc", "--noannounce_rc"}
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if (argument.startswith(("--sandbox", "--nosandbox", "--run_under", "--strategy", "--spawn_strategy",
                                 "--worker", "--dynamic", "--experimental_use_hermetic_linux_sandbox",
                                 "--noexperimental_use_hermetic_linux_sandbox", "--platform_suffix", "--action_env", "--host_action_env",
                                 "--repo_env", "--override_repository", "--inject_repository", "--registry",
                                 "--repository_disable_download", "--norepository_disable_download",
                                 "--shell_executable", "--extra_toolchains", "--extra_execution_platforms",
                                 "--platforms", "--host_platform", "--experimental_spawn_scheduler", "--modify_execution_info", "--test_strategy",
                                 "--remote", "--experimental_remote", "--experimental_sandbox", "--noexperimental_sandbox",
                                 "--incompatible_strict_action_env", "--noincompatible_strict_action_env"))):
            raise ValueError("Bazel option would change the certified execution policy: " + argument.partition("=")[0])
        option = argument.partition("=")[0]
        if option not in value_options | switches:
            raise ValueError("Bazel option is outside the certified execution policy: " + option)
        if option in value_options and "=" not in argument and option != "--test_env":
            index += 1
            if index == len(arguments) or arguments[index].startswith("--"):
                raise ValueError("Bazel option requires a value: " + option)
            index += 1
            continue
        if argument == "--test_env":
            index += 1
            if index == len(arguments):
                raise ValueError("Bazel --test_env requires KEY=VALUE")
            test_environment = arguments[index]
        elif argument.startswith("--test_env="):
            test_environment = argument.removeprefix("--test_env=")
        else:
            index += 1
            continue
        if test_environment.partition("=")[0] == test_environment:
            raise ValueError("Bazel --test_env requires KEY=VALUE")
        key = test_environment.partition("=")[0]
        if key.startswith(("PYTHON", "LD_", "DYLD_", "GIT_CONFIG", "XDG_", "CODEX_", "CLAUDE_", "OPENCODE_", "TEST_", "YOMUX_NATIVE_")) or key in {*TEST_OUTPUT_FILE_ENV_KEYS, "MAC_CHROMIUM_TMPDIR", "PATH", "HOME", "RUNFILES_DIR", "RUNFILES_MANIFEST_FILE", "JAVA_RUNFILES", "BUILD_WORKSPACE_DIRECTORY", "BUILD_WORKING_DIRECTORY", "BAZEL_BINDIR", "CHROME_BIN", "CHROMEDRIVER_BIN", "PYTHONPATH", "PYTHONHOME",
                   "PYTHONUSERBASE", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
                   "DYLD_LIBRARY_PATH", "BASH_ENV", "ENV", "NODE_OPTIONS", "NODE_PATH", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "JAVA_HOME", "BUN_OPTIONS", "TMPDIR", "TMP", "TEMP", "SHELL",
                   *product_env_names("TEST_REVISION"), *product_env_names("TEST_RELEASE_CONTEXT"), *product_env_names("XTERM_PACKAGE_ROOT"), *product_env_names("E2E_EVIDENCE_DIR"), "CC", "CXX", "AR", "LD", "SDKROOT", "CODESIGN_ALLOCATE", "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "LIBRARY_PATH",
                   "GOMODCACHE", "GOPATH", "GOCACHE", "GOPROXY", "GOSUMDB", "GOTOOLCHAIN", "GOENV", "GOWORK", "GO111MODULE",
                   "GIT_EXEC_PATH", "GIT_TEMPLATE_DIR", "GIT_ATTR_NOSYSTEM", "SSH_AUTH_SOCK", "GNUPGHOME"} or key in INHERITED_CREDENTIAL_ENV_KEYS or key in PRODUCT_ROOT_KEYS or key.startswith((*product_env_names("BAZEL_"), *product_env_names("WORKTREE_WRITER"))):
            raise ValueError("Bazel option overrides a protected test input: " + key)
        index += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apparmor-profile", action="store_true")
    parser.add_argument("--install-apparmor", action="store_true")
    parser.add_argument("--identity", metavar="IMAGE_ID")
    parser.add_argument("--flags", action="store_true")
    parser.add_argument("--admit-runtime", action="store_true")
    parser.add_argument("--validate-options", action="store_true")
    parser.add_argument("--output-slots", action="store_true")
    parser.add_argument("--verify-native-bundle", nargs=3, metavar=("PAYLOAD", "MANIFEST", "SHA256"),
                        help="verify a prepared native bundle offline; does not admit a sandbox or execute tests")
    parser.add_argument("--qualified-native", action="store_true")
    parser.add_argument("options", nargs=argparse.REMAINDER)
    parser.add_argument("--inspect", nargs=5 + OUTPUT_USER_ROOT_SLOTS,
                        metavar=("CID", "IMAGE_ID", "CONTRACT", "ROOT", "CACHE",
                                 *(f"OUTPUT_ROOT_{index}" for index in range(OUTPUT_USER_ROOT_SLOTS))))
    args = parser.parse_args()
    args.options = command_arguments(args.options)
    try:
        if args.qualified_native:
            return bazel_native.run_native(args.options, sys.modules[__name__])
        if args.verify_native_bundle:
            bundle = verify_native_bundle(*args.verify_native_bundle)
            execution_event(effective="native-bundle", admission="not-run", bundle_verified=True,
                            bundle_digest=bundle.manifest_digest, platform=[bundle.system, bundle.machine],
                            coverage_complete=False)
        if args.apparmor_profile or args.install_apparmor:
            print(" ".join(verify_apparmor(install=args.install_apparmor)))
        if args.validate_options:
            validate_options(args.options)
        if args.identity:
            print(identity(args.identity))
        if args.flags:
            print("\n".join(SANDBOX_FLAGS))
        if args.output_slots:
            print(OUTPUT_USER_ROOT_SLOTS)
        if args.admit_runtime:
            admit_runtime()
            execution_event(effective="docker", admission="passed", hermetic=True, policy_digest=product_env("BAZEL_APPARMOR_SHA256"), image=product_env("BAZEL_IMAGE_ID"), policy=apparmor_profile())
        if args.inspect:
            cid, image, contract, root, cache, *output_roots = args.inspect
            record = json.loads(subprocess.check_output(["docker", "inspect", cid]))[0]
            inspect_container(record, image, contract, root, cache, *output_roots)
    except (ValueError, OSError, RuntimeError, StopIteration, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        detail = redact_local_service_text(error)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            detail += ": " + redact_local_service_text(error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else error.stderr)
        if args.qualified_native:
            execution_event(effective="qualified-native", admission="failed", coverage_complete=False,
                            failure_phase="admission", detail=detail)
            print("QUALIFIED NATIVE EXECUTION REFUSED: " + detail, file=sys.stderr)
        elif args.verify_native_bundle:
            execution_event(effective="native-bundle", admission="failed", bundle_verified=False,
                            coverage_complete=False, failure_phase="bundle", detail=detail)
            print("NATIVE BUNDLE VERIFICATION FAILED: " + detail, file=sys.stderr)
        else:
            execution_event(effective="docker", admission="failed", hermetic=False, failure_phase="admission", detail=detail)
            print("WARNING: HERMETIC DOCKER TESTING FAILED: " + detail, file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    sys.exit(main())
