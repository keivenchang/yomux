# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Host-private path metadata for the native YO!chat store."""

from __future__ import annotations

from pathlib import Path

from ..common import STATE_DIR
from ..infra.host_partition import host_partitioned_state_dir


CHAT_DATABASE_NAME = "yochat.sqlite3"


def default_chat_database_path(state_dir: Path | None = None) -> Path:
    """Keep this host's live chat WAL separate from the legacy shared file."""
    return host_partitioned_state_dir(state_dir or STATE_DIR) / CHAT_DATABASE_NAME
