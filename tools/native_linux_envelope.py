#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Enter the verified filesystem inside an owned user/mount/PID/network scope."""

import fcntl
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys


def mount(arguments):
    subprocess.run(["/usr/bin/mount", *map(str, arguments)], check=True)


def bind(source, destination, *, readonly):
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.touch(exist_ok=True)
    mount(["--bind", source, destination])
    if readonly:
        mount(["-o", "remount,bind,ro", destination])


def main():
    bundle = Path(sys.argv[1])
    configuration = json.loads(os.environ.pop("YOMUX_NATIVE_OUTER_MOUNTS"))
    temporary = Path(configuration["temporary"])
    root = temporary / "outer-root"
    root.mkdir(exist_ok=True)
    mount(["--make-rprivate", "/"])
    mount(["-t", "tmpfs", "-o", "mode=0755", "tmpfs", root])
    for name in ("usr", "bin", "lib", "lib64", "etc", "opt"):
        bind(bundle / "rootfs" / name, root / name, readonly=True)
    for name in ("tmp", "dev", "dev/pts", "dev/shm", "proc", "sys", "run", "home", configuration["action_temporary"].lstrip("/")):
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in ("null", "zero", "full", "random", "urandom"):
        bind(Path("/dev") / name, root / "dev" / name, readonly=True)
    mount(["-t", "devpts", "-o", "newinstance,ptmxmode=0666,mode=0600", "devpts", root / "dev/pts"])
    (root / "dev/ptmx").symlink_to("pts/ptmx")
    mount(["-t", "tmpfs", "-o", "mode=1777,nosuid,nodev", "tmpfs", root / "dev/shm"])
    (root / "dev/fd").symlink_to("/proc/self/fd")
    for name, descriptor in (("stdin", 0), ("stdout", 1), ("stderr", 2)):
        (root / "dev" / name).symlink_to("fd/" + str(descriptor))
    mount(["-t", "proc", "-o", "nosuid,nodev,noexec", "proc", root / "proc"])
    # Nested user namespaces may mount sysfs only when a complete permitted
    # instance remains visible. Create it before detaching the original root.
    mount(["-t", "sysfs", "-o", "ro,nosuid,nodev,noexec", "sysfs", root / "sys"])
    for path in (temporary, Path(configuration["cache"])):
        bind(path, root / str(path).lstrip("/"), readonly=False)
    for path in (bundle.parent, Path(configuration["workspace"])):
        bind(path, root / str(path).lstrip("/"), readonly=True)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        flags = fcntl.ioctl(connection, 0x8913, struct.pack("16sH14x", b"lo", 0))
        value = struct.unpack("16sH14x", flags)[1] | 1
        fcntl.ioctl(connection, 0x8914, struct.pack("16sH14x", b"lo", value))
    socket.sethostname(b"yomux-native")
    # A plain chroot prevents further user namespaces on Linux. Pivot the
    # private mount namespace instead, then detach its original host root.
    old_root = root / ".old-root"
    old_root.mkdir()
    subprocess.run(["/usr/sbin/pivot_root", str(root), str(old_root)], check=True)
    os.chdir("/")
    subprocess.run(["/usr/bin/umount", "--lazy", "/.old-root"], check=True)
    Path("/.old-root").rmdir()
    os.chdir(configuration["workspace"])
    os.execv("/usr/bin/setpriv", ["/usr/bin/setpriv", "--no-new-privs", "--inh-caps=-all",
        "--ambient-caps=-all", "--bounding-set=-all", "--", *sys.argv[2:]])


if __name__ == "__main__":
    main()
