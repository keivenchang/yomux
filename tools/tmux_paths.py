# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Private short paths for Unix-socket-backed test fixtures."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


def short_tmux_socket_path(prefix: str) -> Path:
    """Create a private socket directory below /tmp and return its short socket path."""

    directory = tempfile.mkdtemp(prefix=f"{prefix}-{os.getpid()}-", dir="/tmp")
    return Path(directory) / "s"
