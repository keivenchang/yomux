#!/usr/bin/env python3
"""Canonical identity and availability of the containerized test image."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import urllib.request
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from yomux_lib.local_services.runtime import redact_local_service_text
from tools.bazel_execution_contract import execution_event
from tools.product_environment import product_env, product_env_name, product_env_names
from tools.instance_isolation import normalize_yomux_environment

IMAGE_SCHEMA_VERSION = 5
IMAGE_REPOSITORY = "yomux-test"
IMAGE_INPUT_FILES = (
    Path("docker") / "Dockerfile.test",
    Path(".dockerignore"),
    Path("package.json"),
    Path(".bazelversion"),
    Path("docker/bazel-registry.json"),
    Path("docker/build-tools.json"),
    Path("docker/build-tools/go.mod"),
    Path("docker/build-tools/go.sum"),
    Path("MODULE.bazel"),
    Path("MODULE.bazel.lock"),
    Path("go.mod"),
    Path("go.sum"),
    Path("docker") / "requirements-test.txt",
    Path("docker") / "debian-packages.txt",
    Path("tools") / "docker_image.py",
    Path("package-lock.json"),
    Path("docker") / "agent-clients" / "package.json",
    Path("docker") / "agent-clients" / "package-lock.json",
    Path("static/vendor/xterm.js"),
    Path("static/vendor/xterm.css"),
    Path("static/vendor/xterm-addon-unicode11.js"),
)
IMAGE_INPUT_DIRECTORIES = ()
CONTAINER_MARKER_PATH = Path("/.dockerenv")
CONTAINER_MOUNTINFO_PATH = Path("/proc/self/mountinfo")
TEST_IMAGE_MARKER_PATH = Path("/opt/yomux-test-image.marker")
TEST_IMAGE_MARKER = "yomux-test-image-v1"
TEST_CONTAINER_ENV = product_env_name('TEST_CONTAINER')
TEST_CONTAINER_LEGACY_ENV = product_env_names('TEST_CONTAINER')[1]


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

    configured_uid = product_env('IMAGE_RUNNER_UID', environ=os.environ)
    configured_gid = product_env('IMAGE_RUNNER_GID', environ=os.environ)
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

    validate_image_inputs(repo_root)
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


def validate_image_inputs(repo_root: Path = REPO_ROOT) -> None:
    missing = [str(path) for path in image_input_paths(repo_root) if not (repo_root / path).is_file()]
    if missing:
        raise ValueError("missing required image inputs: " + ", ".join(missing))


def _module_sdk(repo_root: Path) -> tuple[str, str]:
    module = ast.parse((repo_root / "MODULE.bazel").read_text(encoding="utf-8"))
    downloads = [node for node in ast.walk(module) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "download"]
    defaults = [node for node in downloads if not any(item.arg == "name" for item in node.keywords)]
    if len(defaults) != 1:
        raise ValueError("MODULE.bazel must declare exactly one default Go SDK download")
    values = {item.arg: ast.literal_eval(item.value) for item in defaults[0].keywords}
    return values["version"], values["sdks"]["linux_amd64"][1]


def _download_verified(url: str, target: Path, expected_sha256: str | None = None) -> bool:
    if not target.exists():
        urllib.request.urlretrieve(url, target)
    if expected_sha256 is not None:
        return hashlib.sha256(target.read_bytes()).hexdigest() == expected_sha256
    return True


def _download_go_modules(pinned_go: Path, repo_root: Path, module_cache: Path) -> bool:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(module_cache.parent),
        "GOENV": "off",
        "GOFLAGS": "-mod=readonly",
        "GOMODCACHE": str(module_cache),
        "GOWORK": "off",
        "GOTOOLCHAIN": "local",
        "GOPROXY": "https://proxy.golang.org,direct",
        "GOSUMDB": "sum.golang.org",
    }
    source = module_cache.parent / "module-inputs"
    source.mkdir(exist_ok=True)
    for name in ("go.mod", "go.sum"):
        shutil.copyfile(repo_root / name, source / name)
    completed = subprocess.run(
        [str(pinned_go), "mod", "download", "-json"], cwd=source, env=environment,
        capture_output=True, text=True, check=False,
    )
    if completed.returncode:
        raise ValueError("Go module download exited " + str(completed.returncode) + ": " + redact_local_service_text(completed.stderr))
    for name in ("go.mod", "go.sum"):
        if (source / name).read_bytes() != (repo_root / name).read_bytes():
            raise ValueError("Go module preparation attempted to change locked " + name)
    sum_entries = (source / "go.sum").read_text(encoding="utf-8").splitlines()
    test_dependency_versions = set()
    for entry in sum_entries:
        fields = entry.split()
        if len(fields) != 3:
            raise ValueError("go.sum contains a malformed checksum entry")
        module, version, _checksum = fields
        if version.endswith("/go.mod"):
            version = version.removesuffix("/go.mod")
        test_dependency_versions.add((module, version))
    for module, version in sorted(test_dependency_versions):
        completed = subprocess.run(
            [str(pinned_go), "mod", "download", f"{module}@{version}"], cwd=source, env=environment,
            capture_output=True, text=True, check=False,
        )
        if completed.returncode:
            raise ValueError("Go module checksum seed failed for " + module + "@" + version + ": " + redact_local_service_text(completed.stderr))
    for name in ("go.mod", "go.sum"):
        if (source / name).read_bytes() != (repo_root / name).read_bytes():
            raise ValueError("Go module preparation attempted to change locked " + name)
    for path in (module_cache, *module_cache.rglob("*")):
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | stat.S_IWUSR)
    return True


def prepare_go_module_cache(destination: Path, repo_root: Path = REPO_ROOT) -> bool:
    """Acquire locked Go dependencies with the SDK pinned by MODULE.bazel."""

    validate_image_inputs(repo_root)
    destination.mkdir(parents=True, exist_ok=True)
    version, go_sha = _module_sdk(repo_root)
    pins_root = destination / "build-inputs"
    pins_root.mkdir(exist_ok=True)
    bazel_version = (repo_root / ".bazelversion").read_text().strip()
    go_archive = f"go{version}.linux-amd64.tar.gz"
    go_tarball = pins_root / "go.tar.gz"
    if not _download_verified(f"https://go.dev/dl/{go_archive}", go_tarball, go_sha):
        raise ValueError("Go SDK checksum mismatch")
    sdk_root = destination / "pinned-sdk"
    if not (sdk_root / "go/bin/go").is_file():
        sdk_root.mkdir(exist_ok=True)
        with tarfile.open(go_tarball, "r:gz") as archive:
            archive.extractall(sdk_root, filter="data")
    pinned_go = sdk_root / "go/bin/go"

    go_mod_cache = destination / "go-mod-cache"
    go_mod_cache.mkdir(exist_ok=True)
    if not _download_go_modules(pinned_go, repo_root, go_mod_cache):
        return False

    # Network acquisition is limited to this preparation step. Test execution
    # consumes these immutable seeds with networking disabled.
    build_tools = json.loads((repo_root / "docker/build-tools.json").read_text())
    bazel_path = pins_root / "bazel"
    bazel_url = f"https://releases.bazel.build/{bazel_version}/release/bazel-{bazel_version}-linux-x86_64"
    if not _download_verified(bazel_url, bazel_path, build_tools["bazel_sha256_linux_x86_64"]):
        raise ValueError("Bazel binary checksum mismatch")
    bazel_path.chmod(0o755)

    buildifier = pins_root / "buildifier"
    if not buildifier.exists():
        tool_source = destination / "tool-inputs"
        shutil.copytree(repo_root / "docker/build-tools", tool_source)
        install = subprocess.run(
            [str(pinned_go), "build", "-trimpath", "-mod=readonly", "-o", str(buildifier), "github.com/bazelbuild/buildtools/buildifier"],
            cwd=tool_source, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                  "HOME": str(destination), "GOENV": "off", "GOTOOLCHAIN": "local",
                                  "GOMODCACHE": str(destination / "tool-mod-cache"), "GOCACHE": str(destination / "tool-build-cache"),
                                  "GOPROXY": "https://proxy.golang.org", "GOSUMDB": "sum.golang.org"},
            capture_output=True, text=True, check=False)
        if install.returncode:
            raise ValueError("locked buildifier build exited " + str(install.returncode) + ": " + redact_local_service_text(install.stderr))
        for name in ("go.mod", "go.sum"):
            if (tool_source / name).read_bytes() != (repo_root / "docker/build-tools" / name).read_bytes():
                raise ValueError("buildifier preparation attempted to change locked " + name)
    buildifier.chmod(0o755)

    registry = json.loads((repo_root / "docker/bazel-registry.json").read_text())
    registry_tar = pins_root / "registry.tar.gz"
    if not registry_tar.exists():
        urllib.request.urlretrieve(
            f"https://github.com/bazelbuild/bazel-central-registry/archive/{registry['commit']}.tar.gz", registry_tar)
    if hashlib.sha256(registry_tar.read_bytes()).hexdigest() != registry["sha256"]:
        raise ValueError("Bazel registry checksum mismatch")
    registry_dir = pins_root / "registry"
    if not registry_dir.exists():
        registry_dir.mkdir()
        with tarfile.open(registry_tar, "r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                parts = Path(member.name).parts
                if len(parts) > 1:
                    member.name = str(Path(*parts[1:]))
                    archive.extract(member, registry_dir, filter="data")
    registry_tar.unlink()

    repository_cache = destination / "bazel-repository-cache"
    repository_cache.mkdir(exist_ok=True)
    repository_source = destination / "repository-inputs"
    def snapshot_ignore(directory, names):
        ignored = shutil.ignore_patterns(".git", "node_modules", ".venv", "__pycache__", "*.log")(directory, names)
        if Path(directory) == repo_root:
            ignored.update(name for name in names if name.startswith("bazel-"))
        return ignored

    shutil.copytree(repo_root, repository_source, ignore=snapshot_ignore, symlinks=True)
    fetch = subprocess.run(
        [str(pins_root / "bazel"), "--batch", "--bazelrc=/dev/null",
         "--output_user_root=" + str(pins_root / "output"), "fetch",
         "--repository_cache=" + str(repository_cache), "--registry=file://" + str(registry_dir),
         "--lockfile_mode=error", "--repo_env=GO_REPOSITORY_USE_HOST_MODCACHE=1",
         "--repo_env=GOMODCACHE=" + str(go_mod_cache), "//:go_tests"], cwd=repository_source,
        env={"PATH": "/usr/bin:/bin", "HOME": str(destination), "GOENV": "off",
             "GOWORK": "off", "GOMODCACHE": str(go_mod_cache), "GOPROXY": "off",
             "GOSUMDB": "off", "GOTOOLCHAIN": "local"}, check=False)
    if fetch.returncode:
        raise ValueError("offline Bazel repository preparation exited " + str(fetch.returncode))
    shutil.rmtree(pins_root / "output")
    shutil.rmtree(sdk_root)
    shutil.rmtree(destination / "tool-mod-cache", ignore_errors=True)
    for name in ("repository-inputs", "module-inputs", "tool-inputs", "tool-build-cache"):
        shutil.rmtree(destination / name)
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

    marker = product_env('TEST_CONTAINER', environ=os.environ)
    if marker != "1":
        return False
    if (
        not CONTAINER_MOUNTINFO_PATH.is_file()
        or not TEST_IMAGE_MARKER_PATH.is_file()
    ):
        return False
    try:
        if TEST_IMAGE_MARKER_PATH.read_text(encoding="ascii").strip() != TEST_IMAGE_MARKER:
            return False
        for line in CONTAINER_MOUNTINFO_PATH.read_text(encoding="utf-8").splitlines():
            fields, separator, filesystem = line.partition(" - ")
            if separator and fields.split()[4] == "/":
                if CONTAINER_MARKER_PATH.is_file() and filesystem.split()[0] in {"overlay", "fuse-overlayfs", "aufs"}:
                    return True
                # The hermetic action root hides /.dockerenv along with the outer
                # checkout and HOME. Admission ran before Bazel cache lookup.
                sandbox_root = fields.split()[3]
                if ("/sandbox/linux-sandbox/" in sandbox_root and
                        product_env('BAZEL_CONTRACT', environ=os.environ) and
                        product_env('BAZEL_IMAGE_ID', "", environ=os.environ).startswith("sha256:")):
                    return True
    except (OSError, IndexError):
        return False
    return False


def container_available(repo_root: Path = REPO_ROOT) -> tuple[bool, str]:
    """Return whether tests can route into Docker and an explicit reason when not."""

    if running_inside_container():
        return False, "already running inside the test container"
    if product_env('CHECK_CONTAINER', environ=os.environ) == "0":
        return False, f"{product_env_name('CHECK_CONTAINER')}=0"
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


def toolchain_build_args(repo_root: Path = REPO_ROOT) -> list[str]:
    """Read versions/checksums from their Bazel owners, without evaluating Starlark."""
    module = ast.parse((repo_root / "MODULE.bazel").read_text())
    downloads = [node for node in ast.walk(module) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "download"]
    default_downloads = [
        node for node in downloads
        if not any(item.arg == "name" for item in node.keywords)
    ]
    if len(default_downloads) != 1:
        raise ValueError("MODULE.bazel must declare exactly one default Go SDK download")
    pins = {item.arg: ast.literal_eval(item.value) for item in default_downloads[0].keywords}
    build_tools = json.loads((repo_root / "docker/build-tools.json").read_text())
    return ["BAZEL_VERSION=" + (repo_root / ".bazelversion").read_text().strip(),
            "BAZEL_SHA256=" + build_tools["bazel_sha256_linux_x86_64"],
            "GO_VERSION=" + pins["version"], "GO_SHA256=" + pins["sdks"]["linux_amd64"][1]]


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", action="store_true", help="print the fully qualified image reference")
    parser.add_argument("--fingerprint", action="store_true", help="print only the fingerprint")
    parser.add_argument("--exists", action="store_true", help="exit 0 when the image is already built")
    parser.add_argument(
        "--prepare-go-mod-cache",
        metavar="DIRECTORY",
        help="prepare pinned offline dependencies in a private DIRECTORY",
    )
    parser.add_argument("--toolchain-build-args", action="store_true")
    args = parser.parse_args(argv)
    if args.toolchain_build_args:
        print("\n".join(toolchain_build_args()))
        return 0

    if args.prepare_go_mod_cache:
        return 0 if prepare_go_module_cache(Path(args.prepare_go_mod_cache)) else 1
    if args.fingerprint:
        print(image_fingerprint())
        return 0
    if args.exists:
        return 0 if image_exists(image_name()) else 1
    print(image_name())
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        detail = redact_local_service_text(error)
        arguments = sys.argv[1:] if argv is None else argv
        phase = "image-input-validation" if any(flag in arguments for flag in ("--name", "--fingerprint")) else "image-preparation"
        execution_event(effective="docker", hermetic=False, failure_phase=phase, detail=detail)
        print("WARNING: HERMETIC DOCKER TESTING FAILED: " + detail, file=sys.stderr)
        return 78


if __name__ == "__main__":
    sys.exit(main())
