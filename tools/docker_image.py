#!/usr/bin/env python3
"""Canonical identity and availability of the containerized test image."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
IMAGE_SCHEMA_VERSION = 4
IMAGE_REPOSITORY = "yomux-test"
IMAGE_INPUT_FILES = (
    Path("docker") / "Dockerfile.test",
    Path("docker") / "run-tests.sh",
    Path(".dockerignore"),
    Path("package.json"),
    Path(".bazelversion"),
    Path("MODULE.bazel"),
    Path("BUILD.bazel"),
    Path("WORKSPACE"),
    Path("go.mod"),
    Path("go.sum"),
    Path("docker") / "requirements-test.txt",
    Path("docker") / "debian-packages.txt",
    Path("package-lock.json"),
    Path("docker") / "agent-clients" / "package.json",
    Path("docker") / "agent-clients" / "package-lock.json",
    Path("tools") / "docker_image.py",
    Path("static/vendor/xterm.js"),
    Path("static/vendor/xterm.css"),
    Path("static/vendor/xterm-addon-unicode11.js"),
    Path("tests") / "test_agent_status_docker.py",
    Path("tools") / "fake_llm_server.py",
)
IMAGE_INPUT_DIRECTORIES = (Path("cmd"), Path("internal"))
CONTAINER_MARKER_PATH = Path("/.dockerenv")
CONTAINER_MOUNTINFO_PATH = Path("/proc/self/mountinfo")
TEST_IMAGE_MARKER_PATH = Path("/opt/yomux-test-image.marker")
TEST_IMAGE_MARKER = "yomux-test-image-v1"
TEST_CONTAINER_ENV = "YOMUX_TEST_CONTAINER"
TEST_CONTAINER_LEGACY_ENV = "YOLOMUX_TEST_CONTAINER"


def docker_daemon_is_rootless() -> bool:
    """Return whether the active Docker daemon uses a rootless user namespace."""

    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "info", "--format", "{{json .SecurityOptions}}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return probe.returncode == 0 and "name=rootless" in probe.stdout


def image_runner_identity() -> tuple[str, str]:
    """Return the UID/GID used by Dockerfile.test's runner account."""

    configured_uid = os.environ.get("YOMUX_IMAGE_RUNNER_UID", os.environ.get("YOLOMUX_IMAGE_RUNNER_UID"))
    configured_gid = os.environ.get("YOMUX_IMAGE_RUNNER_GID", os.environ.get("YOLOMUX_IMAGE_RUNNER_GID"))
    if configured_uid is None or configured_gid is None:
        rootless = docker_daemon_is_rootless()
        default_uid = "1000" if rootless else str(os.getuid())
        default_gid = "1000" if rootless else str(os.getgid())
    else:
        default_uid = default_gid = ""
    uid = default_uid if configured_uid is None else configured_uid
    gid = default_gid if configured_gid is None else configured_gid
    if not uid.isdigit() or not gid.isdigit():
        raise ValueError("YOMUX_IMAGE_RUNNER_UID/GID must be numeric")
    return uid, gid


def image_fingerprint(repo_root: Path = REPO_ROOT) -> str:
    """Return a stable short hash of every input that determines the image."""

    digest = hashlib.sha256()
    digest.update(f"schema={IMAGE_SCHEMA_VERSION}\n".encode("utf-8"))
    runner_uid, runner_gid = image_runner_identity()
    digest.update(f"runner_uid={runner_uid}\nrunner_gid={runner_gid}\n".encode("utf-8"))
    for relative in image_input_paths(repo_root):
        path = repo_root / relative
        digest.update(f"file={relative.as_posix()}\n".encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def image_input_paths(repo_root: Path = REPO_ROOT) -> tuple[Path, ...]:
    """Return every file copied into or used to prefetch the test image."""

    paths = list(IMAGE_INPUT_FILES)
    for directory in IMAGE_INPUT_DIRECTORIES:
        paths.extend(
            path.relative_to(repo_root)
            for path in sorted((repo_root / directory).rglob("*"))
            if path.is_file()
        )
    return tuple(paths)


def prepare_go_module_cache(destination: Path, repo_root: Path = REPO_ROOT) -> bool:
    """Copy only this checkout's already-cached Go modules into a build context."""

    # Bazel scrubbed test actions do not always provide HOME. Go then returns
    # empty GOPATH/GOMODCACHE values even though the invoking user's cache is
    # available, causing the Docker wrapper to fall back to a network build.
    go_environment = os.environ.copy()
    if not go_environment.get("HOME"):
        go_environment["HOME"] = str(Path.home())
    try:
        configured_cache = Path(
            subprocess.check_output(
                ["go", "env", "GOMODCACHE"], cwd=repo_root, env=go_environment, text=True, stderr=subprocess.DEVNULL
            ).strip()
        )
        configured_gopath = subprocess.check_output(
            ["go", "env", "GOPATH"], cwd=repo_root, env=go_environment, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return False
    candidates = [configured_cache]
    candidates.extend(Path(entry) / "pkg" / "mod" for entry in configured_gopath.split(os.pathsep) if entry)
    unique_candidates = []
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)

    completed = None
    module_cache = None
    for candidate in unique_candidates:
        environment = os.environ.copy()
        environment.update(GOMODCACHE=str(candidate), GOWORK="off", GOPROXY="off", GOSUMDB="off")
        probe = subprocess.run(
            ["go", "mod", "download", "-json"],
            cwd=repo_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            completed = probe
            module_cache = candidate
            break
    if completed is None or module_cache is None:
        return False

    destination.mkdir(parents=True, exist_ok=True)
    cache_root = module_cache.resolve()
    decoder = json.JSONDecoder()
    position = 0
    while position < len(completed.stdout):
        while position < len(completed.stdout) and completed.stdout[position].isspace():
            position += 1
        if position == len(completed.stdout):
            break
        try:
            module, position = decoder.raw_decode(completed.stdout, position)
        except json.JSONDecodeError:
            return False
        for key in ("Info", "GoMod", "Zip", "Dir"):
            source_value = module.get(key)
            if not source_value:
                continue
            source = Path(source_value)
            try:
                relative = source.resolve().relative_to(cache_root)
            except (OSError, ValueError):
                return False
            target = destination / relative
            if source.is_dir():
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    shutil.copytree(source, target, symlinks=True)
                    for copied in (target, *target.rglob("*")):
                        if not copied.is_symlink():
                            copied.chmod(copied.stat().st_mode | stat.S_IWUSR)
            elif source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                target.chmod(target.stat().st_mode | stat.S_IWUSR)
            else:
                return False
    return True


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

    marker = os.environ.get(TEST_CONTAINER_ENV, os.environ.get(TEST_CONTAINER_LEGACY_ENV))
    if marker != "1":
        return False
    if (
        not CONTAINER_MARKER_PATH.is_file()
        or not CONTAINER_MOUNTINFO_PATH.is_file()
        or not TEST_IMAGE_MARKER_PATH.is_file()
    ):
        return False
    try:
        if TEST_IMAGE_MARKER_PATH.read_text(encoding="ascii").strip() != TEST_IMAGE_MARKER:
            return False
        for line in CONTAINER_MOUNTINFO_PATH.read_text(encoding="utf-8").splitlines():
            fields, separator, filesystem = line.partition(" - ")
            if separator and fields.split()[4] == "/" and filesystem.split()[0] in {"overlay", "fuse-overlayfs", "aufs"}:
                return True
    except (OSError, IndexError):
        return False
    return False


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
    parser.add_argument(
        "--prepare-go-mod-cache",
        metavar="DIRECTORY",
        help="copy this checkout's cached Go modules into DIRECTORY",
    )
    args = parser.parse_args(argv)

    if args.prepare_go_mod_cache:
        return 0 if prepare_go_module_cache(Path(args.prepare_go_mod_cache)) else 1
    if args.fingerprint:
        print(image_fingerprint())
        return 0
    if args.exists:
        return 0 if image_exists(image_name()) else 1
    print(image_name())
    return 0


if __name__ == "__main__":
    sys.exit(main())
