#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""One owner for Docker Bazel isolation, identity, and uncached admission."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import shutil
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yomux_lib.local_services.runtime import redact_local_service_text
from tools.product_environment import product_env_names, product_env, product_env_name
from tools.instance_isolation import INHERITED_CREDENTIAL_ENV_KEYS
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


APPARMOR_TEMPLATE = "docker/sandbox-apparmor.profile"


def apparmor_profile(root=ROOT):
    policy = (root / APPARMOR_TEMPLATE).read_bytes()
    return "yomux-bazel-" + hashlib.sha256(policy).hexdigest()[:24]


def verify_apparmor(install=False):
    # Compare the kernel's exported compiled policy with freshly compiled source;
    # a matching profile name alone cannot establish its effective permissions.
    name = apparmor_profile()
    with tempfile.TemporaryDirectory(prefix="yomux-apparmor-") as directory:
        policy = Path(directory) / "profile"
        policy.write_text((ROOT / APPARMOR_TEMPLATE).read_text().replace("@YOMUX_PROFILE@", name))
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



def validate_options(arguments):
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if (argument.startswith(("--sandbox", "--nosandbox", "--run_under", "--strategy", "--spawn_strategy",
                                 "--worker", "--dynamic", "--experimental_use_hermetic_linux_sandbox",
                                 "--noexperimental_use_hermetic_linux_sandbox", "--platform_suffix", "--action_env", "--host_action_env"))):
            raise ValueError("Bazel option would change the certified execution policy: " + argument.partition("=")[0])
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
        if key in {"PATH", "HOME", "CHROME_BIN", "CHROMEDRIVER_BIN"} or key in INHERITED_CREDENTIAL_ENV_KEYS or key.startswith(tuple(product_env_names('BAZEL_'))):
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
    parser.add_argument("options", nargs=argparse.REMAINDER)
    parser.add_argument("--inspect", nargs=5 + OUTPUT_USER_ROOT_SLOTS,
                        metavar=("CID", "IMAGE_ID", "CONTRACT", "ROOT", "CACHE",
                                 *(f"OUTPUT_ROOT_{index}" for index in range(OUTPUT_USER_ROOT_SLOTS))))
    args = parser.parse_args()
    try:
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
    except (ValueError, OSError, StopIteration, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        detail = redact_local_service_text(error)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            detail += ": " + redact_local_service_text(error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else error.stderr)
        execution_event(effective="docker", admission="failed", hermetic=False, failure_phase="admission", detail=detail)
        print("WARNING: HERMETIC DOCKER TESTING FAILED: " + detail, file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    sys.exit(main())
