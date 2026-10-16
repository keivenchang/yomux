#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Orchestrate qualified native execution using the shared execution contract."""

import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import json
import os
import pwd
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
import shutil
import sys
import time

if str(Path(__file__).absolute().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).absolute().parents[1]))

from tools import native_darwin_launcher
from tools.instance_isolation import private_directory
from tools.tmux_paths import SHORT_TMPDIR_ENV, NATIVE_ACTION_TMPDIR
from tools.product_environment import product_env
from tools.native_process_scope import NativeProcessScope, native_execution_scope


def offline_envelope(bundle, contract, home, source_root=None):
    if bundle.system == "Darwin":
        # Setup and non-test actions run without Seatbelt. The test entrypoint
        # applies the sole OS policy after allocating its fixture and journal.
        return [], json.dumps(native_execution_scope(bundle.system), sort_keys=True)
    source_root = source_root or contract.ROOT
    profile, compiled_digest = contract.verify_apparmor(root=source_root)
    # The outer network namespace also covers repository-rule subprocesses,
    # which are outside Bazel's action sandboxes. Only its loopback is usable.
    envelope = ["/usr/bin/aa-exec", "-p", profile, "--", "/usr/bin/unshare", "--user", "--map-current-user",
                "--net", "--mount", "--pid", "--fork", "--kill-child=KILL", "--uts", "--ipc", "--keep-caps", "--",
                "/usr/bin/python3", "-I", str(source_root / "tools/native_linux_envelope.py"), str(bundle.root)]
    policy = {"compiled_apparmor": compiled_digest, "command": [value.replace(str(source_root), "$SOURCE") for value in envelope],
              "os_primitives": {name: contract._sha256_file(Path(name)) for name in
                                ("/usr/bin/aa-exec", "/usr/bin/unshare", "/usr/bin/python3", "/usr/bin/setpriv", "/usr/bin/mount",
                                 "/usr/sbin/pivot_root")}}
    return envelope, json.dumps(policy, sort_keys=True)


def native_configuration(bundle, contract, home, source_root=None):
    flags = contract.native_flags(bundle, [home] if bundle.system == "Darwin" else [])
    envelope, policy = offline_envelope(bundle, contract, home, source_root)
    flags.append("--action_env=YOMUX_NATIVE_ENVELOPE=" + contract.hashlib.sha256(policy.encode()).hexdigest())
    identity = contract.native_identity(bundle, flags, source_root)
    return flags, envelope, identity


def native_short_root(bundle, contract, identity=None):
    if identity is None:
        home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
        _flags, _envelope, identity = native_configuration(bundle, contract, home)
    return Path("/private/tmp") / contract.hashlib.sha256((str(os.getuid()) + ":" + identity).encode()).hexdigest()[:4]


@contextmanager
def darwin_invocation_directory():
    root = Path(tempfile.mkdtemp(prefix="yn-")).resolve()
    try:
        yield str(root)
    finally:
        # Preserve uncached output and action temporary state if retirement
        # failed, just as a persistent output lease is quarantined.
        if not (root / "process-cleanup.quarantine").exists():
            shutil.rmtree(root)


def native_cache_root():
    cache_root = Path(product_env("NATIVE_CACHE_ROOT", "/tmp/yomux-native-cache-" + str(os.getuid())))
    if not cache_root.is_absolute():
        raise ValueError("YOMUX_NATIVE_CACHE_ROOT must be absolute")
    private_directory(cache_root)
    cache_root = cache_root.resolve(strict=True)
    return cache_root


def native_arguments(arguments, contract):
    arguments = contract.command_arguments(arguments)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-lock-helper", type=Path, default=product_env("DOCUMENT_LOCK_HELPER"))
    parser.add_argument("--session", type=Path)
    parser.add_argument("--lane", type=Path)
    parser.add_argument("--admission-only", action="store_true")
    parser.add_argument("--bundle", default=product_env("NATIVE_BUNDLE", ""))
    parser.add_argument("--jobs", type=int, default=product_env("BAZEL_JOBS") or os.cpu_count() or 2)
    parser.add_argument("--memory-mb", type=int, default=product_env("BAZEL_MEMORY_MB") or None)
    parser.add_argument("--target", default="//:go_tests")
    parser.add_argument("--cache", choices=("on", "off"), default=product_env("BAZEL_CACHE", "on"))
    parser.add_argument("--reuse", choices=("yes", "no", "default"), default="default")
    parser.add_argument("options", nargs=argparse.REMAINDER)
    args = parser.parse_args(arguments)
    args.options = contract.command_arguments(args.options)
    contract.validate_options(args.options)
    if args.cache not in {"on", "off"}:
        raise ValueError("native cache must be on or off")
    if not args.bundle:
        raise ValueError("prepared native bundle is missing; set YOMUX_NATIVE_BUNDLE to its payload")
    if args.jobs < 1 or (args.memory_mb is not None and args.memory_mb < 1024):
        raise ValueError("native jobs must be positive and memory must be at least 1024 MiB")
    if os.getuid() == 0:
        raise ValueError("qualified native execution requires a non-root runner")
    return args


def capture_git_fixture(source, workspace, revision, environment):
    """Export declared Git objects and index without operator configuration."""
    fixture = workspace / "tools/native_git_fixture"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    command = ["git", "-C", str(source)]
    manifest = json.loads((workspace / "tests/fixtures/refactor_characterization/v1.json").read_bytes())
    baseline = manifest["source"].removeprefix("git:")
    commits = {revision, baseline}
    legacy = subprocess.run([*command, "rev-parse", "--verify", "refs/tags/v0.6.10"],
                            env=environment, capture_output=True, text=True)
    if legacy.returncode == 0:
        legacy_object = legacy.stdout.strip()
        commits.add(subprocess.check_output([*command, "rev-parse", "refs/tags/v0.6.10^{commit}"], env=environment, text=True).strip())
    index = subprocess.check_output([*command, "ls-files", "--stage", "-z"], env=environment)
    objects = set(commits)
    for commit in sorted(commits):
        objects.update(subprocess.check_output([*command, "rev-list", "--objects", "--no-object-names", commit + "^{tree}"], env=environment, text=True).splitlines())
    for record in index.split(b"\0"):
        if record:
            mode, identity, _stage = record.partition(b"\t")[0].split()
            if mode != b"160000":
                objects.add(identity.decode())
    if legacy.returncode == 0:
        objects.add(legacy_object)
    subprocess.run(["git", "init", "--bare", "--object-format=sha1", "--template=", str(fixture)], env=environment, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    temporary_pack = fixture / "objects/pack/temporary.pack"
    # Git's named-output mode creates its temporary file in the source object
    # store. Stream into the fixture instead, across filesystems and read-only inputs.
    with temporary_pack.open("w+b") as stream:
        subprocess.run([*command, "pack-objects", "--stdout", "--threads=1", "--window=0"],
                       input="\n".join(sorted(objects)) + "\n", env=environment, text=True, check=True, stdout=stream)
        stream.seek(-20, os.SEEK_END)
        pack = temporary_pack.with_name("pack-" + stream.read(20).hex() + ".pack")
    temporary_pack.rename(pack)
    subprocess.run(["git", "index-pack", str(pack)], env=environment, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "--git-dir=" + str(fixture), "config", "core.bare", "false"], env=environment, check=True)
    subprocess.run(["git", "--git-dir=" + str(fixture), "update-index", "-z", "--index-info"], input=index, env=environment, check=True)
    (fixture / "HEAD").write_bytes((revision + "\n").encode())
    (fixture / "shallow").write_bytes(("\n".join(sorted(commits)) + "\n").encode())
    head = fixture / "refs/yomux/source"
    head.parent.mkdir(parents=True, exist_ok=True)
    head.write_bytes((revision + "\n").encode())
    if legacy.returncode == 0:
        tag = fixture / "refs/tags/v0.6.10"
        tag.parent.mkdir(parents=True, exist_ok=True)
        tag.write_bytes((legacy_object + "\n").encode())
    return fixture


class NativeGateSession:
    """Own one verified bundle, source snapshot, Git context, and admission."""

    def __init__(self, arguments, contract):
        self.args = native_arguments(arguments, contract)
        self.contract = contract
        self.cleanup = ExitStack()
        self.admitted = False

    def __enter__(self):
        try:
            contract = self.contract
            payload = Path(self.args.bundle).resolve(strict=True)
            digest = (payload.parent / "manifest.sha256").read_text().strip()
            self.bundle = contract.verify_native_bundle(payload, payload.parent / "manifest.json", digest)
            home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
            if self.bundle.system == "Darwin" and payload.is_relative_to(home):
                raise ValueError("macOS native bundles must be outside operator HOME; prepare them in job scratch")
            root = Path(self.cleanup.enter_context(tempfile.TemporaryDirectory(prefix="ng-"))).resolve()
            self.path = root / "session.json"
            workspace = root / "source"
            workspace.mkdir()
            revision = contract.repository_head_sha(contract.ROOT)
            context = contract.repository_release_context(contract.ROOT)
            if revision is None or context["revision"] != revision:
                raise ValueError("checkout revision changed before native admission")
            paths = subprocess.check_output(["git", "-C", str(contract.ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                env=contract.repository_git_environment(), text=True).split("\0")
            sources = []
            for relative in sorted(set(paths) - {""}):
                source = contract.ROOT / relative
                if source.is_file():
                    if not source.resolve().is_relative_to(contract.ROOT.resolve()):
                        raise ValueError("native source symlink escapes checkout: " + relative)
                    destination = workspace / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    sources.append((source, destination))
            documents = [(str(source), str(destination)) for source, destination in sources if destination.suffix in {".md", ".txt"}]
            if self.args.document_lock_helper and documents:
                subprocess.run([sys.executable, str(self.args.document_lock_helper), "run",
                    *(destination for _source, destination in documents), "--agent", "native-source-snapshot", "--",
                    sys.executable, "-I", "-c",
                    "import json,shutil,sys;[shutil.copy2(source,destination) for source,destination in json.loads(sys.argv[1])]",
                    json.dumps(documents)], check=True)
            hashes = {}
            for source, destination in sources:
                if not self.args.document_lock_helper or destination.suffix not in {".md", ".txt"}:
                    shutil.copy2(source, destination)
                hashes[destination.relative_to(workspace).as_posix()] = {"sha256": contract._sha256_file(destination), "mode": stat.S_IMODE(destination.lstat().st_mode)}
            fixture = capture_git_fixture(contract.ROOT, workspace, revision, contract.repository_git_environment())
            if fixture is not None:
                for path in sorted(fixture.rglob("*")):
                    if path.is_file():
                        hashes[path.relative_to(workspace).as_posix()] = {"sha256": contract._sha256_file(path), "mode": stat.S_IMODE(path.stat().st_mode)}
            if contract.repository_release_context(contract.ROOT) != context:
                raise ValueError("checkout context changed while capturing native snapshot")
            self.flags, self.envelope, self.identity = native_configuration(self.bundle, contract, home, workspace)
            self.record = {"bundle": str(payload), "digest": digest, "workspace": str(workspace),
                           "revision": revision, "release_context": context, "identity": self.identity,
                           "source_digest": contract.hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
                           "flags": self.flags, "envelope": self.envelope, "admitted": False}
            self.publish()
            return self
        except BaseException:
            self.cleanup.close()
            raise

    def publish(self):
        self.path.write_bytes(json.dumps(self.record).encode())
        self.path.chmod(0o600)

    def admit(self, targets):
        # The same lane lifecycle proves offline tools, tidy, and the entire
        # selected graph before any concurrent lane can consume a test result.
        path = self.path.parent / "admission.json"
        path.write_bytes(json.dumps([{"target": target, "options": []} for target in sorted(set(targets))]).encode())
        path.chmod(0o600)
        status = run_native(["--bundle", str(self.bundle.root), "--session", str(self.path), "--lane", str(path),
                             "--jobs", str(self.args.jobs), "--admission-only"], self.contract)
        if status:
            raise ValueError("native gate admission failed")
        self.record["admitted"] = self.admitted = True
        self.publish()

    def __exit__(self, kind, value, traceback):
        try:
            self.contract.verify_native_bundle(self.bundle.root, self.bundle.root.parent / "manifest.json", self.record["digest"])
        finally:
            self.cleanup.close()


def shared_lane_options(steps):
    """Keep test configuration stable while each target selects its own command."""
    if len(steps) < 2 or any(not step["target"].startswith("//:qualified_") for step in steps):
        return steps
    descriptors = {}
    common = None
    for step in steps:
        arguments = [option for option in step["options"] if option.startswith("--test_arg=")]
        remaining = [option for option in step["options"] if not option.startswith("--test_arg=")]
        if len(arguments) != 1 or (common is not None and remaining != common):
            return steps
        common = remaining
        key = step["target"].removeprefix("//:qualified_").replace("_", "-")
        descriptors[key] = json.loads(arguments[0].partition("=")[2])
    options = [*common, "--test_arg=" + json.dumps({"steps": descriptors}, sort_keys=True, separators=(",", ":"))]
    return [{**step, "options": options} for step in steps]


class NativeLaneSession:
    """Reuse private preparation, one execution worker, and one Bazel server."""

    def __init__(self, args, contract, invocation_scope=None):
        self.args = args
        self.contract = contract
        self.invocation_scope = invocation_scope

    def run(self):
        args, contract, invocation_scope = self.args, self.contract, self.invocation_scope
        session = native_darwin_launcher.private_request(args.session)
        payload = Path(session["bundle"])
        if payload != Path(args.bundle).resolve(strict=True):
            raise ValueError("lane bundle differs from gate bundle")
        manifest = payload.parent / "manifest.json"
        digest = session["digest"]
        inventory = json.loads(manifest.read_bytes())
        bundle = contract.NativeBundle(payload, digest, inventory["platform"]["system"], inventory["platform"]["machine"],
                                       {name: payload / relative for name, relative in inventory["executables"].items()})
        home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
        if bundle.system == "Darwin" and invocation_scope is None:
            return native_darwin_launcher.run_native(self.arguments(), contract, bundle, fixture_base=native_short_root(bundle, contract, session["identity"]),
                                                      source_root=Path(session["workspace"]))
        flags, envelope, identity = list(session["flags"]), list(session["envelope"]), session["identity"]
        revision, release_context = session["revision"], session["release_context"]
        flags.append("--test_env=YOMUX_TEST_REVISION=" + revision)
        flags.append("--test_env=YOMUX_TEST_RELEASE_CONTEXT=" + json.dumps(release_context, sort_keys=True))
        short_root = native_short_root(bundle, contract, identity) if bundle.system == "Darwin" else None
        if short_root is not None:
            private_directory(short_root)
            flags.extend(("--action_env=TMPDIR=" + str(short_root),
                          "--host_action_env=TMPDIR=" + str(short_root),
                          "--test_env=" + SHORT_TMPDIR_ENV + "=" + str(short_root)))
        cache_root = native_cache_root()
        cache = cache_root / identity
        private_directory(cache)
        with ExitStack() as cleanup:
            temporary = cleanup.enter_context(darwin_invocation_directory() if bundle.system == "Darwin"
                                              else tempfile.TemporaryDirectory(prefix="yn-"))
            root = Path(temporary).resolve()
            (root / "home").mkdir(mode=0o700)
            workspace = Path(session["workspace"])
            environment = {
                "HOME": str(root / "home"), "PATH": str(payload / "bin"),
                "TMPDIR": "/tmp" if bundle.system == "Linux" else str(root), "TZ": "America/Los_Angeles", "LANG": "C.UTF-8",
                "GOPROXY": "off", "GOSUMDB": "off", "GOTOOLCHAIN": "local", "GOWORK": "off",
                "YOMUX_NATIVE_OUTER_MOUNTS": json.dumps({"temporary": str(root), "workspace": str(workspace), "cache": str(cache), "action_temporary": NATIVE_ACTION_TMPDIR}),
            }
            bazel = bundle.executables["bazel"]
            flags += ["--platform_suffix=" + identity,
                      "--action_env=YOMUX_BAZEL_CONTRACT=" + identity,
                      "--test_env=YOMUX_BAZEL_CONTRACT=" + identity,
                      "--test_env=YOLOMUX_BAZEL_CONTRACT=" + identity,
                      "--jobs=" + str(args.jobs), "--symlink_prefix=" + str(root / "bazel-")]
            if args.memory_mb is not None:
                flags.append("--local_resources=memory=" + str(args.memory_mb))
            # Repository cache consumers need private lock metadata. A seed must
            # already contain the entire graph; downloads remain disabled below.
            for seed, destination, setting in (("bazel-repository-cache", "repositories", "--repository_cache="),
                                                ("go-mod-cache", "go-modules", "--repo_env=GOMODCACHE=")):
                directory = root / destination
                shutil.copytree(payload / seed, directory)
                for parent, _subdirectories, files in os.walk(directory):
                    Path(parent).chmod(0o700)
                    for name in files:
                        path = Path(parent) / name
                        path.chmod(0o700 if path.stat().st_mode & 0o111 else 0o600)
                flags.append(setting + str(directory))
            steps = native_darwin_launcher.private_request(args.lane) if args.lane else [{"target": args.target, "options": args.options}]
            for step in steps:
                contract.validate_options(step["options"])
            steps = shared_lane_options(steps)
            request = {"root": str(root), "workspace": str(workspace), "cache": str(cache), "flags": flags,
                       "bazel": str(bazel), "steps": steps, "system": bundle.system, "home": str(home),
                       "admitted": session["admitted"], "admission_only": args.admission_only,
                       "reuse": args.cache == "on" and args.reuse != "no" and any(step.get("reuse", True) for step in steps), "cache_on": args.cache == "on",
                       "identity": identity, "revision": revision, "source_digest": session["source_digest"]}
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(16)
                request.update(host_pid=str(os.getpid()), host_port=str(listener.getsockname()[1]))
                if bundle.system == "Linux":
                    request_path = root / "worker.json"
                    request_path.write_bytes(json.dumps(request).encode())
                    request_path.chmod(0o600)
                    result = subprocess.run([*envelope, str(bundle.executables["python3"]), "-I", "-B",
                        str(workspace / "tools/bazel_native.py"), "--worker", str(request_path)],
                        cwd=workspace, env=environment)
                    contract.execution_event(effective="qualified-native", worker_exit_code=result.returncode,
                                             process_cleanup_boundary="linux-pid-namespace", coverage_complete=False)
                    return result.returncode
                try:
                    return execute_lane(request, environment, contract, envelope=envelope,
                        retire=lambda: native_darwin_launcher.cleanup_invocation(invocation_scope, "lease-finally"))
                finally:
                    native_darwin_launcher.cleanup_invocation(invocation_scope, "lane-finally")

    def arguments(self):
        args = self.args
        return ["--bundle", args.bundle, "--session", str(args.session), "--jobs", str(args.jobs),
                "--target", args.target, "--cache", args.cache, "--reuse", args.reuse,
                *(["--memory-mb", str(args.memory_mb)] if args.memory_mb else []),
                *(["--lane", str(args.lane)] if args.lane else []),
                *(["--admission-only"] if args.admission_only else []), "--", *args.options]


def run_native(arguments, contract, *, invocation_scope=None):
    arguments = contract.command_arguments(arguments)
    args = native_arguments(arguments, contract)
    if args.session is None:
        with NativeGateSession(arguments, contract) as gate:
            return run_native(["--session", str(gate.path), *arguments], contract, invocation_scope=invocation_scope)
    return NativeLaneSession(args, contract, invocation_scope).run()


def validate_lightweight_graph(report, contract):
    fragments = {str(item["id"]): item for item in report["pathFragments"]}
    counts = dict.fromkeys(("common", "node", "go", "git_tmux", "browsers", "clients", "compiler", "xterm"), 0)
    for artifact in report["artifacts"]:
        parts = []
        fragment = fragments[str(artifact["pathFragmentId"])]
        while True:
            parts.append(fragment["label"])
            if not fragment.get("parentId"):
                break
            fragment = fragments[str(fragment["parentId"])]
        path = "/".join(reversed(parts))
        if path.endswith("cmd/yomux-go/yomux-go_/yomux-go"):
            raise ValueError("syntax/static graph includes the compiled Go server")
        for group in counts:
            if "/inputs/" + group + "/" in path:
                counts[group] += 1
    if not counts["common"] or not counts["node"] or any(counts[group] for group in counts if group not in {"common", "node"}):
        raise ValueError("syntax/static graph has missing runtime inputs or unnecessary tool groups: " + json.dumps(counts, sort_keys=True))
    contract.execution_event(effective="qualified-native", lightweight_input_groups=counts, coverage_complete=False)


def execute_lane(request, environment, contract, *, envelope=(), process_scope=None, retire=None):
    run = subprocess.run if process_scope is None else process_scope.run
    root, workspace, cache = (Path(request[key]) for key in ("root", "workspace", "cache"))
    flags = request["flags"]
    version = subprocess.check_output([*envelope, request["bazel"], "--version"], env=environment, text=True).strip()
    if version != "bazel " + (workspace / ".bazelversion").read_text().strip():
        raise ValueError("native Bazel version differs from .bazelversion")
    startup = [*envelope, request["bazel"], "--ignore_all_rc_files"]
    with ExitStack() as cleanup:
        output = root / "output"
        quarantine = root / "process-cleanup.quarantine" if request["system"] == "Darwin" else None
        if request["reuse"]:
            for slot in range(128):
                descriptor = os.open(cache / ("slot-" + str(slot) + ".lock"), os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
                    os.close(descriptor)
                    raise ValueError("native output lease must be a private owned regular file")
                candidate = os.fdopen(descriptor, "a")
                try:
                    fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    candidate.close()
                    continue
                quarantine_path = cache / ("slot-" + str(slot) + ".quarantine")
                if quarantine_path.exists():
                    candidate.close()
                    continue
                cleanup.enter_context(candidate)
                quarantine = quarantine_path
                output = cache / ("output-" + str(slot))
                break
            else:
                raise ValueError("all native output slots are owned by active runs")
        startup += ["--output_user_root=" + str(output)]
        # Keep the output lease until descendants retire, even if shutdown fails.
        retire = process_scope.retire if process_scope is not None else retire
        if retire is not None:
            def retire_worker():
                try:
                    if retire() is False:
                        raise ValueError("native worker processes survived cleanup")
                except BaseException:
                    # Failed cleanup must never publish this mutable output slot.
                    if request["system"] == "Darwin":
                        (root / "process-cleanup.quarantine").write_bytes(b"native process retirement failed\n")
                    if quarantine is not None:
                        quarantine.write_bytes(b"native process retirement failed\n")
                    raise
            cleanup.callback(retire_worker)
        cleanup.callback(run, [*startup, "shutdown"], cwd=workspace, env=environment, timeout=30, check=True)
        probe_root = root / "probe"
        probe_root.mkdir(mode=0o700)
        credentials = probe_root / "credentials"
        if request["system"] == "Darwin":
            probe = cleanup.enter_context(tempfile.NamedTemporaryFile(prefix=".yomux-native-probe-", dir=request["home"]))
            credentials = Path(probe.name)
        credentials.write_bytes(b"synthetic host credential probe\n")
        host_probe = probe_root / "host-file"
        host_probe.write_bytes(b"synthetic undeclared host input\n")
        probe_flags = ["--test_env=" + key + "=" + value for key, value in {
            "YOMUX_NATIVE_CREDENTIAL_PROBE": str(credentials), "YOMUX_NATIVE_HOST_PROBE": str(host_probe),
            "YOMUX_NATIVE_HOST_PID": request["host_pid"], "YOMUX_NATIVE_HOST_PORT": request["host_port"]}.items()]
        if request["system"] == "Linux":
            probe_flags.append("--sandbox_block_path=" + str(credentials))
        admission = run([*startup, "test", "//:native_sandbox_admission" if request["admitted"] else "//:native_runtime_admission",
            *([] if request["admitted"] else ["//:go_module_closure_test"]), *flags, *probe_flags,
            *(["--test_arg=--probe-only"] if request["admitted"] else []),
            "--cache_test_results=no", "--test_output=all"], cwd=workspace, env=environment, text=True, capture_output=True, timeout=1800)
        report = admission.stdout + admission.stderr
        print(report, end="", flush=True)
        probes = [json.loads(line.removeprefix("YOMUX_NATIVE_PROBE ")) for line in report.splitlines() if line.startswith("YOMUX_NATIVE_PROBE ")]
        cleanups = [json.loads(line.removeprefix("YOMUX_NATIVE_CLEANUP ")) for line in report.splitlines() if line.startswith("YOMUX_NATIVE_CLEANUP ")]
        if admission.returncode or len(probes) != 1:
            raise ValueError("uncached native sandbox admission failed")
        contract.validate_native_probe(probes[0], request["system"])
        expected_cleanups = 1 if request["admitted"] else 2
        if request["system"] == "Linux" and (len(cleanups) != expected_cleanups or any(record.get("retired") is not True for record in cleanups)):
            raise ValueError("native sandbox process cleanup was not proved")
        if not request["admitted"]:
            graph = run([*startup, "build", "--nobuild", "//:go_tests",
                *(step["target"] for step in request["steps"]), *flags], cwd=workspace, env=environment, timeout=1800)
            if graph.returncode:
                raise ValueError("complete native dependency graph could not be admitted offline")
            inventory = run([*startup, "aquery", "deps(set(//:qualified_py_compile //:qualified_static_build //:qualified_node_yomux_syntax //:qualified_node_wall_syntax))",
                *flags, "--output=jsonproto"], cwd=workspace, env=environment, timeout=1800, capture_output=True, text=True)
            if inventory.returncode:
                raise ValueError("syntax/static action inputs could not be inspected: " + inventory.stderr)
            validate_lightweight_graph(json.loads(inventory.stdout), contract)
        if request["admission_only"]:
            return 0
        for index, step in enumerate(request["steps"]):
            contract.execution_event(effective="qualified-native", admission="passed", contract=request["identity"],
                platform=request["system"], isolation=probes[0], coverage_scope=step["target"],
                worker_pid=os.getpid(), output_user_root=str(output),
                **native_execution_scope(request["system"]),
                source_revision=request["revision"], source_digest=request["source_digest"], coverage_complete=False,
                process_cleanup_boundary="darwin-resource-coalition" if request["system"] == "Darwin" else "linux-pid-namespace")
            started = time.monotonic()
            cache_on = request["cache_on"] and step.get("cache", "on") == "on"
            reuse = request["reuse"] and step.get("reuse", True)
            result = run([*startup, "test", step["target"], *step["options"], *flags,
                *(["--test_timeout=1800"] if step["target"].startswith("//:qualified_") else []),
                *(["--jobs=" + str(step["jobs"])] if "jobs" in step else []),
                *(["--local_resources=memory=" + str(step["memory_mb"])] if step.get("memory_mb") else []),
                *(["--disk_cache=" + str(cache / "actions")] if cache_on else []),
                "--test_output=errors", "--test_summary=terse", "--cache_test_results=" + ("yes" if reuse else "no")],
                cwd=workspace, env=environment, timeout=1800)
            print("YOMUX_NATIVE_STEP " + json.dumps({"index": index, "returncode": result.returncode,
                                                    "seconds": time.monotonic() - started}), flush=True)
            if result.returncode:
                return result.returncode
        return 0


if __name__ == "__main__":
    # -I workers load only the frozen checkout, never the operator's Python path.
    sys.path.insert(0, str(Path(__file__).absolute().parents[1]))
    from tools import bazel_execution_contract
    if sys.argv[1:2] != ["--worker"] or len(sys.argv) != 3:
        raise ValueError("native worker requires one private request")
    scope = NativeProcessScope(private_supervisor=True)
    try:
        status = execute_lane(native_darwin_launcher.private_request(Path(sys.argv[2])), dict(os.environ), bazel_execution_contract,
                              process_scope=scope)
    finally:
        retired = scope.retire()
        print("YOMUX_NATIVE_WORKER_CLEANUP " + json.dumps({"retired": retired, "boundary": "linux-private-subreaper"}), flush=True)
        if not retired:
            raise ValueError("native worker processes survived cleanup")
    sys.exit(status)
