#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Generate declared runtime trees without an ambient shell or PATH."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def copy_runtime_tree(source, destination, *, writable=False):
    shutil.copytree(source, destination, symlinks=False, dirs_exist_ok=True)
    if writable:
        # Bazel normalizes cached inputs to read-only modes. Private fixtures
        # must own writable copies while retaining executable source scripts.
        destination.chmod(0o700)
        for path in destination.rglob("*"):
            path.chmod(0o700 if path.is_dir() or path.stat().st_mode & 0o111 else 0o600)


def runtime_tree(output, pairs):
    if len(pairs) % 2:
        raise ValueError("runtime tree inputs must be source/relative pairs")
    output.mkdir(parents=True, exist_ok=True)
    for source, relative in zip(pairs[::2], pairs[1::2], strict=True):
        relative_path = Path("static" if relative == "@frontend@" else relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("runtime tree input escapes its declared output")
        destination = output / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if Path(source).is_dir():
            copy_runtime_tree(source, destination)
        else:
            shutil.copy2(source, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("tree", "frontend", "version"))
    parser.add_argument("output", type=Path)
    parser.add_argument("inputs", nargs="*")
    args = parser.parse_args()
    if args.mode == "tree":
        runtime_tree(args.output, args.inputs)
    elif args.mode == "frontend":
        command = [sys.executable, args.inputs[0], "--source-root", ".", "--output-directory", str(args.output)]
        subprocess.run(command, check=True)
        subprocess.run([*command, "--check"], check=True)
    else:
        subprocess.run([sys.executable, args.inputs[0], "--source", args.inputs[1], "--output", str(args.output)], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
