# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Tests for the disposable Docker image identity."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import docker_image


@pytest.mark.parametrize(("capacity", "status", "accepted"), [("137438953472", 0, True), ("", 0, False), ("unknown", 0, False), ("0", 0, False), ("137438953472", 1, False)])
def test_test_container_shared_memory_uses_daemon_ram_or_fails_explicitly(capacity, status, accepted):
    source = (Path(__file__).resolve().parents[1] / "docker/run-tests.sh").read_text()
    sizing = source[source.index('shared_memory_bytes="$('):source.index("# --rm provides")]
    script = 'set -e\nCAPACITY="$1"\nSTATUS="$2"\ndocker() { printf "%s" "$CAPACITY"; return "$STATUS"; }\n'
    script += sizing + '\nprintf "%s" "$shared_memory_bytes"\n'
    result = subprocess.run(["bash", "-c", script, "shm-test", capacity, str(status)], capture_output=True, text=True)
    assert (result.returncode == 0) is accepted
    if accepted:
        assert result.stdout == capacity


def _fixture_root(root: Path) -> Path:
    for relative in docker_image.IMAGE_INPUT_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture input\n")
    return root


def test_fingerprint_changes_when_any_image_input_changes(tmp_path):
    baseline_root = _fixture_root(tmp_path / "baseline")
    baseline = docker_image.image_fingerprint(baseline_root)
    for index, relative in enumerate(docker_image.IMAGE_INPUT_FILES):
        changed_root = _fixture_root(tmp_path / f"changed-{index}")
        changed_path = changed_root / relative
        changed_path.write_bytes(changed_path.read_bytes() + b"changed\n")
        assert docker_image.image_fingerprint(changed_root) != baseline, relative


def test_prepare_go_module_cache_falls_back_from_empty_configured_cache(tmp_path, monkeypatch):
    configured = tmp_path / "configured-cache"
    fallback = tmp_path / "gopath" / "pkg" / "mod"
    module = fallback / "example.com" / "module@v1.0.0"
    module.mkdir(parents=True)
    (module / "go.mod").write_text("module example.com/module\n", encoding="utf-8")
    destination = tmp_path / "destination"
    monkeypatch.delenv("HOME", raising=False)
    observed_home = []

    def fake_check_output(command, *, env, **_kwargs):
        observed_home.append(env["HOME"])
        return {
            ("go", "env", "GOMODCACHE"): f"{configured}\n",
            ("go", "env", "GOPATH"): f"{fallback.parent.parent}\n",
        }[tuple(command)]

    monkeypatch.setattr(
        docker_image.subprocess,
        "check_output",
        fake_check_output,
    )
    probes = []

    def fake_run(command, *, env, **_kwargs):
        assert command == ["go", "mod", "download", "-json"]
        probes.append(env["GOMODCACHE"])
        if env["GOMODCACHE"] == str(configured.resolve()):
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"Dir": str(module), "GoMod": str(module / "go.mod")}),
        )

    monkeypatch.setattr(docker_image.subprocess, "run", fake_run)
    assert docker_image.prepare_go_module_cache(destination, tmp_path) is True
    assert observed_home and all(observed_home)
    assert probes == [str(configured.resolve()), str(fallback.resolve())]
    assert (destination / "example.com" / "module@v1.0.0" / "go.mod").is_file()
