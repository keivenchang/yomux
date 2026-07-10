# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import os

from yolomux_lib.atomic_file import file_lock


def test_file_lock_touches_persistent_lock_file_on_acquisition(tmp_path):
    path = tmp_path / "settings.yaml"
    lock_path = tmp_path / ".settings.yaml.lock"
    lock_path.touch()
    os.utime(lock_path, ns=(1_000_000_000, 1_000_000_000))

    with file_lock(path):
        assert lock_path.stat().st_mtime_ns > 1_000_000_000
