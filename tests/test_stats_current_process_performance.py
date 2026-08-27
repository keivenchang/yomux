# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Linux process-census performance contracts for current YO!stats."""

import os

from yolomux_lib.stats_current import families, process_memory


def _write_stat(root, pid, comm, *, started_at_ticks=12_345):
    process = root / str(pid)
    process.mkdir(exist_ok=True)
    fields = ["S", *("0" for _ in range(21))]
    fields[11] = "100"
    fields[12] = "50"
    fields[19] = str(started_at_ticks)
    fields[21] = "10"
    (process / "stat").write_text(
        f"{pid} ({comm}) {' '.join(fields)}\n",
        encoding="utf-8",
    )
    return process


def test_linux_census_reuses_binary_without_a_second_proc_read(tmp_path, monkeypatch):
    process = _write_stat(tmp_path, 123, "python3.12")
    os.symlink("/usr/bin/python3.12", process / "exe")
    cache = {}
    readlinks = 0
    original_readlink = process_memory.os.readlink

    def counted_readlink(path):
        nonlocal readlinks
        readlinks += 1
        return original_readlink(path)

    monkeypatch.setattr(process_memory.os, "readlink", counted_readlink)

    first = process_memory._linux_process_census(tmp_path, binary_cache=cache)
    second = process_memory._linux_process_census(tmp_path, binary_cache=cache)

    assert first == second
    assert first is not None and first[0].binary == "python"
    assert readlinks == 1
    assert not (process / "comm").exists()


def test_linux_census_refreshes_binary_after_exec_name_changes(tmp_path, monkeypatch):
    process = _write_stat(tmp_path, 123, "python3.12")
    os.symlink("/usr/bin/python3.12", process / "exe")
    cache = {}
    readlinks = 0
    original_readlink = process_memory.os.readlink

    def counted_readlink(path):
        nonlocal readlinks
        readlinks += 1
        return original_readlink(path)

    monkeypatch.setattr(process_memory.os, "readlink", counted_readlink)
    first = process_memory._linux_process_census(tmp_path, binary_cache=cache)

    _write_stat(tmp_path, 123, "node")
    (process / "exe").unlink()
    os.symlink("/usr/bin/node", process / "exe")
    second = process_memory._linux_process_census(tmp_path, binary_cache=cache)

    assert first is not None and first[0].binary == "python"
    assert second is not None and second[0].binary == "node"
    assert readlinks == 2


# --- the read path -----------------------------------------------------------------------------
# Every stored process payload proves its binary keys are already normalized by re-deriving
# them, so one full build asks the same question hundreds of thousands of times about a few
# hundred distinct names. Measured on a copy of the live store: 348,151 calls, 279 distinct.

_MEMORY_FAMILY = families.FAMILY_BY_NAME["system_memory"]
_PROBE_BINARIES = ("zzz-probe-alpha", "zzz-probe-beta", "zzz-probe-gamma")


def _memory_payload(binaries):
    return {
        "used_bytes": 1_000,
        "capacity_bytes": 2_000,
        "process_memory_bytes": {binary: 10 for binary in binaries},
    }


def test_repeated_binary_keys_are_derived_once_not_once_per_observation(monkeypatch):
    """A key seen 40 times must cost one derivation, not 40."""

    observations = 40
    constructions = 0
    original_path = process_memory.Path

    def counting_path(*args, **options):
        nonlocal constructions
        constructions += 1
        return original_path(*args, **options)

    monkeypatch.setattr(process_memory, "Path", counting_path)

    for _ in range(observations):
        _MEMORY_FAMILY.validate_payload(_memory_payload(_PROBE_BINARIES))

    derivations = observations * len(_PROBE_BINARIES)
    assert derivations == 120, "the validator did not ask as many times as this test assumes"
    assert constructions <= len(_PROBE_BINARIES), (
        f"{constructions} path parses for {len(_PROBE_BINARIES)} distinct binaries "
        f"across {derivations} derivations"
    )


def test_the_binary_derivation_cache_is_bounded(monkeypatch):
    """This queue exists to remove unbounded caches; do not let this one become one."""

    bound = process_memory.NORMALIZED_BINARY_CACHE_ENTRIES
    assert process_memory._normalized_process_binary.cache_info().maxsize == bound

    for index in range(bound * 2):
        process_memory.normalize_process_binary(f"zzz-flood-{index}")

    assert process_memory._normalized_process_binary.cache_info().currsize <= bound


def test_deriving_a_binary_is_a_pure_function_of_its_text(monkeypatch):
    """Values that are equal and hash alike must not share one cache entry."""

    # `1 == True` and `hash(1) == hash(True)`, so a cache keyed on the caller's object
    # would answer one with the other. The coercion to text happens before the cache.
    assert process_memory.normalize_process_binary(1) == "1"
    assert process_memory.normalize_process_binary(True) == "true-3cbc87c7"
    assert process_memory.normalize_process_binary(0) == ""
    assert process_memory.normalize_process_binary(False) == ""
    assert process_memory.normalize_process_binary(None) == ""
    # Unhashable: a cache keyed on the argument would raise TypeError here.
    assert process_memory.normalize_process_binary([1]) == "1-080a9ed4"
    assert process_memory.normalize_process_binary("python3.12") == "python"
    assert process_memory.normalize_process_binary("/usr/bin/node") == "node"
    assert process_memory.normalize_process_binary("  claude  ") == "claude"
    assert process_memory.normalize_process_binary("/bin/sh (deleted)") == "sh"
