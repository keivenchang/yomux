#!/usr/bin/env python3
"""Canonical identity and availability of the containerized test image."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
IMAGE_SCHEMA_VERSION = 2
IMAGE_REPOSITORY = "yolomux-test"
IMAGE_INPUT_FILES = (
    Path("docker") / "Dockerfile.test",
    Path("package.json"),
)
CONTAINER_MARKER_PATH = Path("/.dockerenv")


def image_fingerprint(repo_root: Path = REPO_ROOT) -> str:
    """Return a stable short hash of every input that determines the image."""

    digest = hashlib.sha256()
    digest.update(f"schema={IMAGE_SCHEMA_VERSION}\n".encode("utf-8"))
    for relative in IMAGE_INPUT_FILES:
        path = repo_root / relative
        digest.update(f"file={relative.as_posix()}\n".encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def image_name(repo_root: Path = REPO_ROOT) -> str:
    """Return the image reference for this exact source state."""

    return f"{IMAGE_REPOSITORY}:{image_fingerprint(repo_root)}"


def image_exists(name: str) -> bool:
    """Return whether the local daemon holds this exact image."""

    return subprocess.run(
        ["docker", "image", "inspect", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def running_inside_container() -> bool:
    """Return whether this process is already the containerized run."""

    return CONTAINER_MARKER_PATH.exists() or os.environ.get("YOLOMUX_CHECK_IN_CONTAINER") == "1"


def container_available(repo_root: Path = REPO_ROOT) -> tuple[bool, str]:
    """Return whether tests can route into Docker and an explicit reason when not."""

    if running_inside_container():
        return False, "already running inside the test container"
    if os.environ.get("YOLOMUX_CHECK_CONTAINER") == "0":
        return False, "YOLOMUX_CHECK_CONTAINER=0"
    if not (repo_root / "docker" / "run-tests.sh").exists():
        return False, "docker/run-tests.sh is missing from this checkout"
    if shutil.which("docker") is None:
        return False, "no docker binary on PATH"
    probe = subprocess.run(
        ["docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode != 0:
        return False, "docker daemon is not reachable"
    return True, "docker is available"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", action="store_true", help="print the fully qualified image reference")
    parser.add_argument("--fingerprint", action="store_true", help="print only the fingerprint")
    parser.add_argument("--exists", action="store_true", help="exit 0 when the image is already built")
    args = parser.parse_args(argv)

    if args.fingerprint:
        print(image_fingerprint())
        return 0
    if args.exists:
        return 0 if image_exists(image_name()) else 1
    print(image_name())
    return 0


if __name__ == "__main__":
    sys.exit(main())
