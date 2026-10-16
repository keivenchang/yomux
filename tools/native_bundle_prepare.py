#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Snapshot a prepared platform layout into an independently checksum-locked bundle.

Dependency acquisition happens before this command. Execution never calls this
preparer. A layout contains bin/, Python packages, dependency seeds, and either
a Linux rootfs/ or a macOS SDK. The manifest covers every runtime leaf and mode.
"""

import argparse
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
from email.parser import BytesParser

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bazel_execution_contract as contract


def copy_layout(source, destination):
    shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
    # Rootfs symlinks address their virtual filesystem, never the preparing
    # host. Rebase absolute links before the offline verifier resolves them.
    filesystem = destination / "rootfs"
    for directory, directories, leaves in os.walk(destination, followlinks=False):
        for name in list(directories):
            if (Path(directory) / name).is_symlink():
                directories.remove(name)
                leaves.append(name)
        for name in leaves:
            path = Path(directory) / name
            if path.is_symlink():
                target = Path(os.readlink(path))
                if target.is_absolute():
                    if path.is_relative_to(filesystem):
                        target = filesystem / str(target).lstrip("/")
                    elif target.is_relative_to(source):
                        target = destination / target.relative_to(source)
                    else:
                        raise ValueError("layout contains a host symlink: " + str(path))
                    path.unlink()
                    path.symlink_to(os.path.relpath(target, path.parent))


def snapshot(source, destination, lock_helper):
    command = [sys.executable, str(Path(__file__).resolve()), "--copy-layout", str(source), str(destination)]
    documents = [destination / path.relative_to(source) for path in source.rglob("*") if path.suffix in {".md", ".txt"}]
    if lock_helper and documents:
        for path in documents:
            path.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(lock_helper), "run", *(str(path) for path in documents), "--agent", "native-bundle-preparation", "--", *command]
    subprocess.run(command, check=True)


def layout_leaves(payload, directories):
    for directory, subdirectories, leaves in os.walk(payload, followlinks=False, onerror=contract._raise_bundle_walk_error):
        path = Path(directory)
        directory_mode = 0o700 if path == payload else 0o755
        if stat.S_IMODE(path.stat().st_mode) != directory_mode:
            path.chmod(directory_mode)
        directories[path.relative_to(payload).as_posix()] = directory_mode
        for name in list(subdirectories):
            if (path / name).is_symlink():
                subdirectories.remove(name)
                leaves.append(name)
        for name in leaves:
            yield path / name


def manifest_leaf(payload, source):
    relative = source.relative_to(payload).as_posix()
    metadata = source.lstat()
    package_name = None
    if stat.S_ISLNK(metadata.st_mode):
        record = {"symlink": os.readlink(source)}
    elif stat.S_ISREG(metadata.st_mode):
        mode = 0o555 if metadata.st_mode & 0o111 else 0o444
        if stat.S_IMODE(metadata.st_mode) != mode:
            source.chmod(mode)
        record = {"mode": mode, "sha256": contract._sha256_file(source)}
        if (source.name == "METADATA" and source.parent.name.endswith(".dist-info")
                and "site-packages" in source.parts
                and relative.startswith(("python/lib/", "rootfs/usr/local/lib/"))):
            metadata = BytesParser().parsebytes(source.read_bytes())
            if not metadata["Name"] or not metadata["Version"]:
                raise ValueError("Python distribution metadata is incomplete: " + relative)
            package_name = metadata["Name"]
    else:
        raise ValueError("layout contains a non-file input: " + relative)
    return relative, record, package_name


def manifest_for(payload):
    files = {}
    directories = {}
    packages = {}
    for relative, record, package_name in contract.native_input_results(
            partial(manifest_leaf, payload), layout_leaves(payload, directories)):
        files[relative] = record
        if package_name is not None:
            packages[package_name] = relative
    tools = contract.required_native_tools(platform.system())
    manifest = {"schema": 1, "platform": {"system": platform.system(), "machine": platform.machine()},
                "files": files, "directories": directories,
                "executables": {name: "bin/" + name for name in sorted(tools)}, "python_packages": packages}
    if platform.system() == "Darwin":
        manifest["sdk"] = {
            "os": subprocess.check_output(["/usr/bin/sw_vers", "-buildVersion"], text=True).strip(),
            "path": subprocess.check_output(["/usr/bin/xcrun", "--show-sdk-path"], text=True).strip(),
            "version": subprocess.check_output(["/usr/bin/xcrun", "--show-sdk-version"], text=True).strip(),
        }
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--document-lock-helper", type=Path)
    parser.add_argument("--freeze", action="store_true", help="lock an existing OUTPUT/payload layout in place")
    parser.add_argument("--copy-layout", nargs=2, type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.copy_layout:
        copy_layout(*args.copy_layout)
        return 0
    if args.output is None or (args.layout is None and not args.freeze):
        parser.error("--output and either --layout or --freeze are required")
    output = args.output.resolve()
    output.mkdir(mode=0o700, exist_ok=args.freeze)
    if (output / "manifest.json").exists() or (output / "manifest.sha256").exists():
        raise ValueError("refusing to replace an existing bundle lock")
    payload = output / "payload"
    if not args.freeze:
        snapshot(args.layout.resolve(strict=True), payload, args.document_lock_helper)
    manifest = manifest_for(payload)
    data = json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n"
    digest = hashlib.sha256(data).hexdigest()
    path = output / "manifest.json"
    candidate = output / "candidate-manifest.json"
    candidate.write_bytes(data)
    contract.verify_native_bundle(payload, candidate, digest)
    os.replace(candidate, path)
    (output / "manifest.sha256").write_bytes((digest + "\n").encode())
    print(json.dumps({"payload": str(payload), "manifest_sha256": digest, "files": len(manifest["files"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
