# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Action-private Linux fixture storage shared by execution and admission."""

import json
import subprocess
import sys


def linux_fixture_storage_flags(*directories):
    # Bazel 9.2 mounts tmpfs before pivot_root. The self-bind carries that fresh
    # mount into the hermetic root; tmpfs alone leaves its /tmp disk-backed.
    return tuple(flag for directory in directories for flag in (
        "--sandbox_tmpfs_path=" + directory,
        "--sandbox_add_mount_pair=" + directory + ":" + directory,
    ))


def linux_sandbox_mount_arguments(flags):
    arguments = []
    for flag in flags:
        name, _, value = flag.partition("=")
        if name == "--sandbox_tmpfs_path":
            arguments.extend(("-e", value))
        elif name == "--sandbox_add_mount_pair":
            source, separator, destination = value.partition(":")
            arguments.extend(("-M", source))
            if separator:
                arguments.extend(("-m", destination))
    return arguments


def require_tmpfs(directories):
    filesystems = {}
    for directory in directories:
        # Query the effective kernel filesystem, since covered mounts can make
        # mount inventories report a filesystem other than the one in use.
        filesystem = subprocess.check_output(
            ["stat", "--file-system", "--format=%T", "--", str(directory)],
            text=True, timeout=5,
        ).strip()
        if filesystem != "tmpfs":
            raise ValueError("fixture storage is not action-private tmpfs: " + str(directory))
        filesystems[str(directory)] = filesystem
    return filesystems


if __name__ == "__main__":
    print(json.dumps(require_tmpfs(sys.argv[1:])))
