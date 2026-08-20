# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Linux process-census performance contracts for current YO!stats."""

import os

from yolomux_lib.stats_current import process_memory


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
