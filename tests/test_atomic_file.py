# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import os
import pytest

from yomux_lib.atomic_file import file_lock
from yomux_lib.infra.atomic_file import atomic_write_text


def test_file_lock_touches_persistent_lock_file_on_acquisition(tmp_path):
    path = tmp_path / "settings.yaml"
    lock_path = tmp_path / ".settings.yaml.lock"
    lock_path.touch()
    os.utime(lock_path, ns=(1_000_000_000, 1_000_000_000))

    with file_lock(path):
        assert lock_path.stat().st_mtime_ns > 1_000_000_000


def test_exclusive_atomic_publication_keeps_the_existing_complete_file(tmp_path):
    path = tmp_path / "secret"
    atomic_write_text(path, "first", mode=0o600, overwrite=False)
    with pytest.raises(FileExistsError):
        atomic_write_text(path, "second", mode=0o600, overwrite=False)
    assert path.read_text() == "first"
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [path]
