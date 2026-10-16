#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Compatibility helper for Go tests that need a generated version overlay."""

import argparse
import json
from pathlib import Path
import re

VERSION_ASSIGNMENT = re.compile(r'^version: "((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))"$', re.MULTILINE)


def generate_go_version(source: Path, output: Path) -> str:
    source_text = source.read_text(encoding="utf-8")
    versions = VERSION_ASSIGNMENT.findall(source_text)
    if len(versions) != 1:
        raise ValueError(f"expected exactly one version field in {source}")

    output.write_text(
        "// Code generated from version.yaml; DO NOT EDIT.\n\n"
        "package main\n\n"
        f'const version = "{versions[0]}"\n',
        encoding="utf-8",
    )
    return versions[0]


def generate_go_version_overlay(source: Path, output: Path, overlay: Path, target: Path) -> str:
    version = generate_go_version(source, output)
    overlay.write_text(
        json.dumps({"Replace": {str(target.resolve()): str(output.resolve())}}) + "\n",
        encoding="utf-8",
    )
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overlay", type=Path)
    parser.add_argument("--overlay-target", type=Path)
    args = parser.parse_args()
    if (args.overlay is None) != (args.overlay_target is None):
        parser.error("--overlay and --overlay-target must be provided together")

    if args.overlay is not None and args.overlay_target is not None:
        try:
            generate_go_version_overlay(args.source, args.output, args.overlay, args.overlay_target)
        except ValueError as error:
            parser.error(str(error))
    else:
        try:
            generate_go_version(args.source, args.output)
        except ValueError as error:
            parser.error(str(error))


if __name__ == "__main__":
    main()
