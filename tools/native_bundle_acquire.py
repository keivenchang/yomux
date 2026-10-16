#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Network-enabled native dependency preparation, separate from gate execution."""

import argparse
import base64
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_LAYOUTS = {}
COPY_PROGRESS = None
MAC_SYSTEM_LIBRARY_PREFIXES = ("/usr/lib/", "/System/Library/", "/System/iOSSupport/",
                               "/System/Volumes/Preboot/Cryptexes/OS/System/")
MAC_COMPILER_EXECUTABLES = frozenset({
    "clang", "clang++", "clang-cache", "clang-stat-cache", "ld", "ld-classic",
    "ar", "as", "libtool", "strip", "lipo", "nm", "nm-classic", "ranlib",
    "dsymutil", "codesign_allocate", "otool", "llvm-otool", "install_name_tool", "git", "git-receive-pack", "git-upload-pack",
    "git-upload-archive", "git-shell",
})
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bazel_execution_contract as contract
from tools import docker_image
from tools import native_bundle_prepare


def run(command, *, cwd=None, environment=None):
    subprocess.run([str(value) for value in command], cwd=cwd, env=environment, check=True)


def copy_source(source, destination, lock_helper):
    SOURCE_LAYOUTS[destination] = source.resolve()
    progress = json.loads(COPY_PROGRESS.read_text()) if COPY_PROGRESS and COPY_PROGRESS.exists() else {}
    key = str(destination)
    stamp = copy_stamp(source)
    if destination.exists() and progress.get(key) == [stamp, copy_stamp(destination)]:
        return
    if destination.exists() and copy_matches(source, destination):
        if COPY_PROGRESS:
            progress[key] = [stamp, copy_stamp(destination)]
            COPY_PROGRESS.write_text(json.dumps(progress, sort_keys=True))
        return
    documents = []
    if source.is_dir():
        for directory, subdirectories, leaves in os.walk(source, followlinks=True):
            ignored = copy_ignored(directory, subdirectories + leaves)
            subdirectories[:] = [name for name in subdirectories if name not in ignored]
            documents.extend(destination / (Path(directory) / name).relative_to(source)
                             for name in leaves if name not in ignored and Path(name).suffix in {".md", ".txt"})
    elif source.suffix in {".md", ".txt"}:
        documents = [destination]
    command = [sys.executable, str(Path(__file__).absolute()), "--copy-source", str(source), str(destination)]
    if lock_helper and documents:
        for path in documents:
            path.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(lock_helper), "run", *(str(path) for path in documents), "--agent", "native-tool-acquisition", "--", *command]
    run(command)
    if COPY_PROGRESS:
        progress[key] = [stamp, copy_stamp(destination)]
        COPY_PROGRESS.write_text(json.dumps(progress, sort_keys=True))


def copy_stamp(source):
    records = []
    if source.is_file():
        info = source.stat()
        return [info.st_size, info.st_mtime_ns, info.st_mode]
    for directory, directories, leaves in os.walk(source, followlinks=True):
        ignored = copy_ignored(directory, directories + leaves)
        directories[:] = [name for name in directories if name not in ignored]
        for name in leaves:
            if name not in ignored:
                path = Path(directory) / name
                info = path.stat()
                records.append((str(path.relative_to(source)), info.st_size, info.st_mtime_ns, info.st_mode))
    return hashlib.sha256(json.dumps(sorted(records)).encode()).hexdigest()


def copy_matches(source, destination):
    if source.is_file():
        return destination.is_file() and contract._sha256_file(source) == contract._sha256_file(destination)
    expected = set()
    for directory, directories, files in os.walk(source, followlinks=True):
        ignored = copy_ignored(directory, directories + files)
        directories[:] = [name for name in directories if name not in ignored]
        for name in files:
            if name in ignored:
                continue
            path = Path(directory) / name
            relative = path.relative_to(source)
            expected.add(relative)
            target = destination / relative
            if not target.is_file() or contract._sha256_file(path) != contract._sha256_file(target):
                return False
    actual = {path.relative_to(destination) for path in destination.rglob("*") if path.is_file()}
    return expected == actual


def copy_ignored(directory, names):
    if Path(directory).name == "site-packages":
        return [name for name in names if name != "pip" and not name.startswith("pip-")]
    return [name for name in names if name == "__pycache__" or name.endswith(".pyc")
            or ((Path(directory) / name).is_symlink() and not (Path(directory) / name).exists())]


def copy_action(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=False, ignore=copy_ignored, dirs_exist_ok=True, copy_function=copy_leaf)
    else:
        copy_leaf(source, destination)


def copy_leaf(source, destination):
    target = Path(destination)
    if target.exists():
        target.chmod(target.stat().st_mode | 0o200)
    return shutil.copy(source, destination)


def executable(name, source, payload):
    target = payload / "bin" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    relative = os.path.relpath(source, target.parent)
    if target.is_symlink():
        if os.readlink(target) == relative:
            return
        target.unlink()
    target.symlink_to(relative)


def shell_wrapper(name, payload, body):
    path = payload / "bin" / name
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\nroot=\"$(cd -- \"$(dirname -- \"$0\")/..\" && pwd)\"\n" + body + "\n")
    path.chmod(0o755)


def mac_agent_executables(payload):
    modules = payload / "agents/node_modules"
    native_clients = {
        "claude": ("@anthropic-ai/claude-code", "@anthropic-ai/claude-code-darwin-arm64", "claude"),
        "opencode": ("opencode-ai", "opencode-darwin-arm64", "bin/opencode"),
    }
    sources = {"codex": modules / ".bin/codex"}
    for name, (package, native_package, relative) in native_clients.items():
        package_info = json.loads((modules / package / "package.json").read_bytes())
        native_info = json.loads((modules / native_package / "package.json").read_bytes())
        if native_info["version"] != package_info["optionalDependencies"][native_package]:
            raise ValueError("native agent package differs from pinned dependency: " + name)
        source = modules / native_package / relative
        if not source.is_file() or not os.access(source, os.X_OK):
            raise ValueError("native agent executable is unavailable: " + name)
        sources[name] = source
    return sources


def wheel_documents(wheels, target):
    documents = []
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                if name.endswith((".md", ".txt")):
                    parts = Path(name).parts
                    if parts and parts[0].endswith(".data") and len(parts) > 2:
                        parts = parts[2:]
                    documents.append(target.joinpath(*parts))
    return documents


def install_wheels(wheels, target):
    # pip --target moves whole directories into place and would remove adjacent
    # document leases. Extract the locked wheels in place instead.
    for wheel in sorted(wheels.glob("*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                parts = Path(entry.filename).parts
                if not parts or Path(entry.filename).is_absolute() or ".." in parts:
                    raise ValueError("unsafe Python wheel member")
                if parts[0].endswith(".data") and len(parts) > 2:
                    parts = parts[2:]
                destination = target.joinpath(*parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    destination.chmod(0o644)
                destination.write_bytes(archive.read(entry))
                destination.chmod(0o755 if (entry.external_attr >> 16) & 0o111 else 0o644)


def locked_run(command, documents, lock_helper, *, cwd=None, environment=None):
    if lock_helper and documents:
        for path in documents:
            path.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(lock_helper), "run", *(str(path) for path in sorted(set(documents))), "--agent", "native-dependency-install", "--", *command]
    run(command, cwd=cwd, environment=environment)


def archive_member_matches(archive, member, existing, *, linkname=None):
    if member.isdir():
        return existing.is_dir() and not existing.is_symlink()
    if member.issym():
        return existing.is_symlink() and os.readlink(existing) == (member.linkname if linkname is None else linkname)
    if member.isfile() and existing.is_file() and not existing.is_symlink() and existing.stat().st_size == member.size:
        with archive.extractfile(member) as expected, existing.open("rb") as actual:
            return all(expected.read(1024 * 1024) == actual.read(1024 * 1024)
                       for _ in range(member.size // (1024 * 1024) + 1))
    return False


def extract_locked_archive(source, destination, lock_helper, *, strip_components=0):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(source) as archive:
        names = []
        for member in archive.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("unsafe preparation archive member")
            if len(path.parts) > strip_components:
                existing = destination.joinpath(*path.parts[strip_components:])
                if not archive_member_matches(archive, member, existing):
                    names.append(member.name)
    descriptor = destination.parent / ("extract-" + hashlib.sha256(str(destination).encode()).hexdigest()[:12] + ".json")
    for offset in range(0, len(names), 3000):
        selected = names[offset:offset + 3000]
        documents = [destination / Path(*Path(name).parts[strip_components:])
                     for name in selected if Path(name).suffix in {".md", ".txt"}]
        descriptor.write_text(json.dumps({"names": selected, "strip_components": strip_components}))
        locked_run([sys.executable, Path(__file__).absolute(), "--extract-archive", source, destination, descriptor],
                   documents, lock_helper)


def prepare_xterm_fixture(payload, stage, lock_helper):
    if (payload.parent / "manifest.json").exists():
        raise ValueError("refusing to change a frozen native bundle")
    if lock_helper is None:
        raise ValueError("xterm package extraction requires a document lock helper")
    fixture = payload / "xterm"
    for name in ("package.json", "package-lock.json"):
        copy_source(ROOT / name, fixture / name, lock_helper)
    package = json.loads((fixture / "package.json").read_bytes())
    lock = json.loads((fixture / "package-lock.json").read_bytes())
    packages = lock["packages"]
    if package["dependencies"] != packages[""]["dependencies"]:
        raise ValueError("xterm package dependencies differ from their lock")
    archives = stage / "xterm-archives"
    archives.mkdir(mode=0o700, parents=True, exist_ok=True)
    for relative, record in packages.items():
        if not relative:
            continue
        path = contract._bundle_path(relative)
        if path.parts[0] != "node_modules":
            raise ValueError("unexpected xterm package lock path")
        integrity = record["integrity"]
        if not integrity.startswith("sha512-") or not record["resolved"].startswith("https://registry.npmjs.org/"):
            raise ValueError("xterm package requires a locked npm SHA-512 archive")
        expected = base64.b64decode(integrity.removeprefix("sha512-"), validate=True)
        archive = archives / (hashlib.sha256(integrity.encode()).hexdigest() + ".tgz")
        if archive.exists():
            content = archive.read_bytes()
        else:
            with urllib.request.urlopen(record["resolved"], timeout=30) as response:
                content = response.read()
        if hashlib.sha512(content).digest() != expected:
            raise ValueError("xterm package archive differs from npm lock integrity: " + relative)
        if not archive.exists():
            archive.write_bytes(content)
        extract_locked_archive(archive, fixture / path, lock_helper, strip_components=1)
    for name, path in contract.xterm_fixture_paths(fixture / "node_modules").items():
        if not path.is_file() or contract._sha256_file(path) != contract._sha256_file(ROOT / "static/vendor" / name):
            raise ValueError("upstream xterm package differs from tracked vendor asset: " + name)


def macho_output(arguments, source):
    # Apple's classic otool interprets parentheses in a path as archive-member
    # syntax. Chrome helper names use them; an inherited fd names the same bytes.
    with source.open("rb") as descriptor:
        return subprocess.check_output(["otool", *arguments, "/dev/fd/" + str(descriptor.fileno())],
                                       pass_fds=(descriptor.fileno(),), text=True)


def macho_dependency(name, source):
    """Resolve dyld names against the source layout before it is relocated."""
    def expand(value):
        return value.replace("@loader_path", str(source.parent)).replace("@executable_path", str(source.parent)).replace("@@HOMEBREW_PREFIX@@", "/opt/homebrew").replace("@@HOMEBREW_CELLAR@@", "/opt/homebrew/Cellar")
    if name.startswith("@rpath/"):
        commands = macho_output(["-l"], source)
        roots = re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset", commands)
        candidates = [Path(expand(root)) / name.removeprefix("@rpath/") for root in roots]
        existing = [path.resolve() for path in candidates if path.is_file()]
        if not existing:
            # Shared libraries can inherit the executable's rpaths. Standalone
            # Python carries these libraries beside each other under lib/.
            sibling = source.parent / name.removeprefix("@rpath/")
            if sibling.is_file():
                existing = [sibling.resolve()]
        if not existing:
            raise ValueError("unresolved Mach-O dependency: " + name + " from " + str(source))
        return existing[0]
    return Path(expand(name))


def macho_runtime_seed(path, payload):
    relative = path.relative_to(payload)
    if relative.parts[0] == "sdk":
        return False
    if relative.parts[0] != "compiler":
        return True
    return (relative.parent == Path("compiler/bin") and relative.name in MAC_COMPILER_EXECUTABLES
            or relative.is_relative_to("compiler/libexec/git-core")
            or relative.is_relative_to("compiler/libexec/as")
            or relative.is_relative_to("compiler/lib/clang"))


def macho_rpaths(path):
    return re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset", macho_output(["-l"], path))


def relocate_rpaths(path, payload):
    changed = False
    for name in dict.fromkeys(macho_rpaths(path)):
        if not name.startswith("/") or (name.rstrip("/") + "/").startswith(MAC_SYSTEM_LIBRARY_PREFIXES):
            continue
        source = Path(name)
        mapped = [(destination / source.relative_to(layout), layout)
                  for destination, layout in SOURCE_LAYOUTS.items()
                  if layout.is_dir() and source.is_relative_to(layout)]
        if mapped:
            target, _layout = max(mapped, key=lambda value: len(value[1].parts))
            relative = "@loader_path/" + os.path.relpath(target, path.parent)
            run(["install_name_tool", "-rpath", name, relative, path])
        elif source.is_relative_to(payload):
            relative = "@loader_path/" + os.path.relpath(source, path.parent)
            run(["install_name_tool", "-rpath", name, relative, path])
        else:
            run(["install_name_tool", "-delete_rpath", name, path])
        changed = True
    return changed


def relocated_dependency_matches(name, path, target, payload):
    if name.startswith("@loader_path/"):
        return (path.parent / name.removeprefix("@loader_path/")).resolve() == target
    if not name.startswith("@rpath/"):
        return False
    contexts = [(path, path.parent)]
    # Extension modules inherit the standalone interpreter's relative rpaths.
    python = payload / "python/bin/python3"
    if path.is_relative_to(payload / "python") and python.is_file():
        contexts.append((python, python.parent))
    for context, executable_dir in contexts:
        for root in macho_rpaths(context):
            if root.startswith("@loader_path/"):
                directory = context.parent / root.removeprefix("@loader_path/")
            elif root.startswith("@executable_path/") and context == python:
                directory = executable_dir / root.removeprefix("@executable_path/")
            else:
                continue
            if (directory / name.removeprefix("@rpath/")).resolve() == target:
                return True
    return False


def relocate_macho(payload):
    """Copy non-system shared libraries once and make references relocatable."""
    library_root = payload / "native-libraries"
    library_root.mkdir(exist_ok=True)
    copied = {}
    pending = [path for path in payload.rglob("*")
               if path.is_file() and not path.is_symlink() and macho_runtime_seed(path, payload)]
    queued = set(pending)
    for path in pending:
        if not (path.stat().st_mode & 0o111 or path.suffix in {".dylib", ".so"} or "Frameworks" in path.parts):
            continue
        with path.open("rb") as source:
            header = source.read(8)
        magic = header[:4]
        if magic not in {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}:
            continue
        # Java class files share CAFEBABE, followed by a version instead of a
        # universal Mach-O architecture count.
        if magic in {b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"} and not 0 < int.from_bytes(header[4:], "big") <= 32:
            continue
        dependencies = [row for row in macho_output(["-L"], path).splitlines() if row.startswith("\t")]
        identities = [row for row in macho_output(["-D"], path).splitlines()
                      if row and not re.fullmatch(r"/dev/fd/\d+( \(architecture [^)]+\))?:", row)]
        changed = relocate_rpaths(path, payload)
        layouts = [destination for destination in SOURCE_LAYOUTS if path == destination or path.is_relative_to(destination)]
        original = path
        if layouts:
            destination = max(layouts, key=lambda value: len(value.parts))
            original = SOURCE_LAYOUTS[destination] / path.relative_to(destination) if path != destination else SOURCE_LAYOUTS[destination]
        for row in dependencies:
            name = row.strip().split(" (", 1)[0]
            if name in identities:
                continue
            if name.startswith(MAC_SYSTEM_LIBRARY_PREFIXES):
                continue
            local = Path(name.replace("@loader_path", str(path.parent)).replace("@executable_path", str(path.parent)))
            if local.is_absolute() and local.is_file() and local.resolve().is_relative_to(payload):
                source = local.resolve()
            else:
                source = macho_dependency(name, original)
            source = source.resolve(strict=True)
            mapped = [(destination / source.relative_to(layout), layout, destination)
                      for destination, layout in SOURCE_LAYOUTS.items()
                      if layout.is_dir() and source.is_relative_to(layout)]
            if mapped:
                target, layout, destination = max(mapped, key=lambda value: len(value[1].parts))
                if target not in queued:
                    pending.append(target)
                    queued.add(target)
                if relocated_dependency_matches(name, path, target, payload):
                    continue
            elif source.is_relative_to(payload):
                target = source
            else:
                if source not in copied:
                    identity = hashlib.sha256(str(source).encode()).hexdigest()[:12]
                    target = library_root / (identity + "-" + source.name)
                    copy_leaf(source, target)
                    copied[source] = target
                    SOURCE_LAYOUTS[target] = source
                    pending.append(target)
                target = copied[source]
            relative = "@loader_path/" + os.path.relpath(target, path.parent)
            if relative == name:
                continue
            run(["install_name_tool", "-change", name, relative, path])
            changed = True
        platform_executable = any(original.is_relative_to(directory) for directory in
                                  ("/bin", "/sbin", "/usr/bin", "/usr/sbin"))
        if changed or platform_executable:
            # Platform signatures only authorize execution at their protected
            # OS location. Copied utilities need their own ad-hoc signature.
            run(["codesign", "--force", "--sign", "-", path])


def prepare_linux_network_files(filesystem):
    # Exported images omit Docker's generated hosts file. The offline runtime
    # still needs deterministic localhost resolution for fixture clients.
    for name, content in {"hosts": b"127.0.0.1 localhost\n::1 localhost\n", "resolv.conf": b""}.items():
        path = filesystem / "etc" / name
        if path.is_symlink():
            path.unlink()
        path.write_bytes(content)


def prepare_linux(output, image, lock_helper):
    """Export immutable preparation inputs; execution never invokes Docker."""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("Linux preparation requires an immutable Docker image digest")
    actual = subprocess.check_output(["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True).strip()
    if actual != image:
        raise ValueError("Linux preparation image differs from its checksum lock")
    payload = output / "payload"
    payload.mkdir(exist_ok=True)
    filesystem = payload / "rootfs"
    filesystem.mkdir(exist_ok=True)
    archive_path = output / "rootfs.tar"
    if not archive_path.exists():
        container = subprocess.check_output(["docker", "create", "--network=none", "--entrypoint=/bin/true", image], text=True).strip()
        try:
            run(["docker", "export", "--output", archive_path, container])
        finally:
            run(["docker", "rm", container])
    provenance = output / "source-image.json"
    source_lock = {"image": image, "archive": contract._sha256_file(archive_path)}
    if provenance.exists() and json.loads(provenance.read_text()) != source_lock:
        raise ValueError("exported runtime differs from its preparation lock")
    provenance.write_text(json.dumps(source_lock, sort_keys=True))
    with tarfile.open(archive_path) as archive:
        members = []
        for member in archive.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise ValueError("unsafe preparation image member")
            if not name.parts or name.parts[0] in {"dev", "proc", "sys", "run", "tmp", "home", "root"}:
                continue
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                continue
            if member.issym() and member.linkname.startswith("/"):
                member.linkname = os.path.relpath(filesystem / member.linkname.lstrip("/"), (filesystem / name).parent)
            existing = filesystem / name
            if archive_member_matches(archive, member, existing):
                continue
            members.append(member)
        # Bounded chunks keep leases short without reparsing the full archive
        # once for every registry directory.
        members.sort(key=lambda member: (len(Path(member.name).parts), member.name))
        for offset in range(0, len(members), 3000):
            selected = members[offset:offset + 3000]
            documents = [filesystem / member.name for member in selected if Path(member.name).suffix in {".md", ".txt"}]
            if documents and lock_helper:
                for document in documents:
                    document.parent.mkdir(parents=True, exist_ok=True)
                descriptor = output / "extract-members.json"
                descriptor.write_text(json.dumps([member.name for member in selected]))
                locked_run([sys.executable, Path(__file__).absolute(), "--extract-archive", archive_path, filesystem, descriptor],
                    documents, lock_helper)
            else:
                archive.extractall(filesystem, members=selected, filter="data")
    prepare_linux_network_files(filesystem)
    (payload / "bin").mkdir(exist_ok=True)
    search = ("usr/local/go/bin", "usr/local/bin", "opt/yomux-agent-clients/node_modules/.bin", "usr/bin", "bin")
    aliases = {"chrome": "chromium", "cc": "gcc"}
    for name in sorted(contract.required_native_tools("Linux")):
        candidates = [filesystem / directory / aliases.get(name, name) for directory in search]
        candidate = next((path for path in candidates if path.is_file()), None)
        if candidate is None:
            raise ValueError("preparation image lacks required native executable: " + name)
        executable(name, candidate, payload)
    for name, source in {"go-mod-cache": "yomux-go-mod-cache", "bazel-repository-cache": "yomux-bazel-repository-cache",
                         "bazel-registry": "yomux-bazel-registry"}.items():
        target = payload / name
        relative = "rootfs/opt/" + source
        if not target.is_symlink():
            target.symlink_to(relative)
        elif os.readlink(target) != relative:
            raise ValueError("native dependency seed alias differs from its recipe")


def prepare_mac(output, lock_helper):
    global COPY_PROGRESS
    payload = output / "payload"
    payload.mkdir(exist_ok=True)
    (payload / "bin").mkdir(exist_ok=True)
    stage = output / "acquisition"
    stage.mkdir(exist_ok=True)
    COPY_PROGRESS = stage / "copy-progress.json"
    (stage / "home").mkdir(mode=0o700, exist_ok=True)
    environment = {"PATH": os.environ["PATH"], "HOME": str(stage / "home"), "TZ": "America/Los_Angeles",
                   "HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_CACHE": str(stage / "brew-cache"),
                   "HOMEBREW_API_CACHE": str(stage / "brew-api"), "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    copy_source(Path(sys.base_prefix), payload / "python", lock_helper)
    python = payload / "python/bin/python3"
    if not python.is_file():
        raise ValueError("preparation requires a relocatable Python distribution with bin/python3")
    executable("python3", python, payload)
    node = Path(shutil.which("node") or "").resolve(strict=True)
    copy_source(node, payload / "bin/node", lock_helper)
    go = Path(shutil.which("go") or "").resolve(strict=True)
    go_root = Path(subprocess.check_output([str(go), "env", "GOROOT"], text=True).strip())
    copy_source(go_root, payload / "go", lock_helper)
    executable("go", payload / "go/bin/go", payload)
    go_version, _checksum = docker_image._module_sdk(ROOT)
    if not subprocess.check_output([str(payload / "bin/go"), "version"], text=True).startswith("go version go" + go_version + " "):
        raise ValueError("preparing Go SDK differs from MODULE.bazel")
    java_home = Path("/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home").resolve(strict=True)
    copy_source(java_home, payload / "jdk", lock_helper)
    version = (ROOT / ".bazelversion").read_text().strip()
    bazel = stage / ("bazel-" + version + "-darwin-arm64")
    checksum = json.loads((ROOT / "docker/build-tools.json").read_text())["bazel_sha256_darwin_arm64"]
    if not docker_image._download_verified("https://releases.bazel.build/" + version + "/release/" + bazel.name,
                                         bazel, checksum):
        raise ValueError("downloaded native Bazel differs from its repository checksum lock")
    bazel.chmod(0o755)
    copy_source(bazel, payload / "bazel-native", lock_helper)
    shell_wrapper("bazel", payload, 'JAVA_HOME="$root/jdk" exec "$root/bazel-native" "$@"')
    compiler = Path(subprocess.check_output(["/usr/bin/xcrun", "--find", "clang"], text=True).strip())
    copy_source(compiler.parent.parent, payload / "compiler", lock_helper)
    sdk = Path(subprocess.check_output(["/usr/bin/xcrun", "--show-sdk-path"], text=True).strip()).resolve(strict=True)
    copy_source(sdk, payload / "sdk", lock_helper)
    shell_wrapper("cc", payload, 'exec "$root/compiler/bin/clang" -isysroot "$root/sdk" -B"$root/compiler/bin" "$@"')
    shell_wrapper("c++", payload, 'exec "$root/compiler/bin/clang++" -isysroot "$root/sdk" -B"$root/compiler/bin" "$@"')
    for name in ("ld", "ar", "as", "libtool", "strip", "lipo", "nm", "ranlib", "otool", "install_name_tool", "codesign_allocate"):
        source = payload / "compiler/bin" / name
        if source.is_file():
            executable(name, source, payload)
    bottle_info = json.loads(subprocess.check_output(["brew", "info", "--json=v2", "coreutils"], env=environment, text=True))
    bottle = bottle_info["formulae"][0]["bottle"]["stable"]["files"]["arm64_tahoe"]
    run(["brew", "fetch", "--quiet", "--bottle-tag=arm64_tahoe", "coreutils"], environment=environment)
    bottles = sorted({path.resolve() for path in (stage / "brew-cache").rglob("*") if path.is_file() and contract._sha256_file(path) == bottle["sha256"]})
    if len(bottles) != 1:
        raise ValueError("downloaded coreutils bottle differs from its source checksum")
    coreutils = stage / "coreutils"
    coreutils.mkdir(exist_ok=True)
    extract_locked_archive(bottles[0], coreutils, lock_helper)
    coreutils_bin = next(coreutils.glob("coreutils/*/bin"))
    utilities = {path.name[1:]: path for path in coreutils_bin.iterdir() if path.name.startswith("g") and path.is_file()}
    flock_info = json.loads(subprocess.check_output(["brew", "info", "--json=v2", "flock"], env=environment, text=True))
    flock_bottle = flock_info["formulae"][0]["bottle"]["stable"]["files"]["arm64_tahoe"]
    run(["brew", "fetch", "--quiet", "--bottle-tag=arm64_tahoe", "flock"], environment=environment)
    flock_archives = {path.resolve() for path in (stage / "brew-cache").rglob("*") if path.is_file() and contract._sha256_file(path) == flock_bottle["sha256"]}
    if len(flock_archives) != 1:
        raise ValueError("downloaded flock bottle differs from its source checksum")
    flock_root = stage / "flock"
    flock_root.mkdir(exist_ok=True)
    extract_locked_archive(flock_archives.pop(), flock_root, lock_helper)
    copy_source(next(flock_root.glob("flock/*/bin/flock")), payload / "bin/flock", lock_helper)
    special = {"python3", "go", "bazel", "node", "cc", "c++", "chrome", "chromedriver", "claude", "codex", "opencode", "buildifier", "mypy", "ld", "ar", "as", "strip", "nm", "ranlib", "flock", "lipo", "otool", "install_name_tool", "codesign_allocate"}
    for name in sorted(contract.required_native_tools("Darwin") - special):
        if name in contract.NATIVE_DARWIN_PLATFORM_TOOLS:
            source = Path(contract.NATIVE_DARWIN_PLATFORM_TOOLS[name])
        else:
            source = (payload / "compiler/bin/git") if name == "git" else utilities.get(name) or Path(shutil.which(name) or "")
        if not source.is_file():
            raise ValueError("required preparation tool is unavailable: " + name)
        copy_source(source.resolve(), payload / "bin" / name, lock_helper)
    wheels = stage / "wheels"
    wheels.mkdir(exist_ok=True)
    run([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--requirement", ROOT / "docker/requirements-test.txt", "--dest", wheels], environment=environment)
    target = payload / "python/lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    archives = sorted(wheels.glob("*.whl"))
    locked_run([sys.executable, Path(__file__).absolute(), "--install-wheels", wheels, target],
               wheel_documents(archives, target), lock_helper, environment=environment)
    shell_wrapper("mypy", payload, 'exec "$root/bin/python3" -m mypy "$@"')
    prepare_xterm_fixture(payload, stage, lock_helper)
    clients = payload / "agents"
    clients.mkdir(exist_ok=True)
    copy_source(ROOT / "docker/agent-clients/package.json", clients / "package.json", lock_helper)
    copy_source(ROOT / "docker/agent-clients/package-lock.json", clients / "package-lock.json", lock_helper)
    run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--omit=dev"], cwd=clients, environment=environment)
    # Ignoring install scripts leaves native-client launchers as placeholders.
    # Their integrity-locked platform packages already contain the real binaries.
    for name, source in mac_agent_executables(payload).items():
        executable(name, source, payload)
    chrome_cache = Path.home() / ".cache/selenium/chrome/mac-arm64"
    driver_cache = Path.home() / ".cache/selenium/chromedriver/mac-arm64"
    matching = sorted(set(path.name for path in chrome_cache.iterdir()) & set(path.name for path in driver_cache.iterdir()))
    if not matching:
        raise ValueError("preparation requires a cached matching Chrome-for-Testing/browser driver pair")
    version = matching[-1]
    copy_source(chrome_cache / version, payload / "chrome", lock_helper)
    copy_source(driver_cache / version / "chromedriver", payload / "bin/chromedriver", lock_helper)
    executable("chrome", payload / "chrome/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing", payload)
    build_source = stage / "build-tools"
    copy_source(ROOT / "docker/build-tools", build_source, lock_helper)
    environment.update(GOTOOLCHAIN="local", GOWORK="off", GOENV="off", GOCACHE=str(stage / "go-build"),
                       GOMODCACHE=str(stage / "go-tools-modules"), GOPROXY="https://proxy.golang.org", GOSUMDB="sum.golang.org")
    run([payload / "bin/go", "build", "-trimpath", "-mod=readonly", "-o", payload / "bin/buildifier", "github.com/bazelbuild/buildtools/buildifier"], cwd=build_source, environment=environment)
    docker_image._download_go_modules(payload / "bin/go", ROOT, payload / "go-mod-cache")
    registry = json.loads((ROOT / "docker/bazel-registry.json").read_bytes())
    archive_path = stage / "registry.tar.gz"
    urllib.request.urlretrieve("https://github.com/bazelbuild/bazel-central-registry/archive/" + registry["commit"] + ".tar.gz", archive_path)
    if contract._sha256_file(archive_path) != registry["sha256"]:
        raise ValueError("Bazel registry differs from its repository checksum lock")
    registry_root = payload / "bazel-registry"
    registry_root.mkdir(exist_ok=True)
    extract_locked_archive(archive_path, registry_root, lock_helper, strip_components=1)
    relocate_macho(payload)
    repository_cache = payload / "bazel-repository-cache"
    repository_cache.mkdir(exist_ok=True)
    run([payload / "bin/bazel", "--batch", "--bazelrc=/dev/null", "--output_user_root=" + str(stage / "bazel-output"), "fetch",
         "--repository_cache=" + str(repository_cache), "--registry=file://" + str(registry_root), "--lockfile_mode=off",
         "--repo_env=YOMUX_DIAGNOSTIC_PYTHON=" + str(payload / "bin/python3"),
         "--repo_env=GOMODCACHE=" + str(payload / "go-mod-cache"), "--repo_env=GO_REPOSITORY_USE_HOST_MODCACHE=1", "//:go_tests"], cwd=ROOT, environment=environment)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--document-lock-helper", type=Path)
    parser.add_argument("--linux-image", help="immutable sha256 image digest used only for Linux dependency preparation")
    parser.add_argument("--resume", action="store_true", help="resume an unfrozen macOS layout, checking copied bytes")
    parser.add_argument("--extract-archive", nargs=3, type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--copy-source", nargs=2, type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--install-wheels", nargs=2, type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--prepare-xterm", nargs=2, type=Path, metavar=("PAYLOAD", "STAGE"),
                        help="prepare the locked upstream xterm package fixture without freezing a bundle")
    args = parser.parse_args()
    if args.prepare_xterm:
        prepare_xterm_fixture(*args.prepare_xterm, args.document_lock_helper)
        return 0
    if args.install_wheels:
        install_wheels(*args.install_wheels)
        return 0
    if args.extract_archive:
        archive_path, destination, descriptor = args.extract_archive
        selection = json.loads(descriptor.read_text())
        names = set(selection["names"] if isinstance(selection, dict) else selection)
        strip_components = selection["strip_components"] if isinstance(selection, dict) else 0
        with tarfile.open(archive_path) as archive:
            members = [member for member in archive.getmembers() if member.name in names]
            for member in members:
                member.name = str(Path(*Path(member.name).parts[strip_components:]))
                if member.islnk() and strip_components:
                    member.linkname = str(Path(*Path(member.linkname).parts[strip_components:]))
                if member.issym() and member.linkname.startswith("/"):
                    member.linkname = os.path.relpath(destination / member.linkname.lstrip("/"), (destination / member.name).parent)
            archive.extractall(destination, members=members, filter="data")
        return 0
    if args.copy_source:
        copy_action(*args.copy_source)
        return 0
    if args.output is None:
        parser.error("--output is required")
    if platform.system() not in {"Darwin", "Linux"}:
        parser.error("native preparation requires Linux or macOS")
    args.output.mkdir(mode=0o700, exist_ok=args.resume)
    if (args.output / "manifest.json").exists():
        raise ValueError("refusing to change a frozen native bundle")
    if platform.system() == "Linux":
        if not args.linux_image:
            parser.error("Linux preparation requires --linux-image")
        prepare_linux(args.output.resolve(), args.linux_image, args.document_lock_helper)
    else:
        if platform.machine() != "arm64":
            parser.error("automatic macOS acquisition currently requires arm64")
        prepare_mac(args.output.resolve(), args.document_lock_helper)
    run([sys.executable, ROOT / "tools/native_bundle_prepare.py", "--output", args.output, "--freeze"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
