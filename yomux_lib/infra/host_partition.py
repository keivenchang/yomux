# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""One place that decides where a host's private state lives.

`/home` can be exported and mounted by another machine at the same absolute
path, so a default under the shared root puts two hosts on one file. For SQLite
that is not merely a race: WAL requires every user of a database to be on one
host and is unsupported on a network filesystem.

Every store that needs a per-host location calls here. It is deliberately one
function rather than a convention each store re-implements -- divergent copies
of one value is the defect shape this codebase keeps hitting, and a store that
partitioned differently from its readers would be invisible until data went
missing.
"""

from __future__ import annotations

from pathlib import Path

from yomux_lib.infra.host_identity import current_host_identity
from yomux_lib.infra.host_identity import HostIdentity

HOST_PARTITION_DIRNAME = "hosts"


def host_namespaced_path(path: Path | str, host_identity: HostIdentity | None = None) -> Path:
    """Return one host-qualified path, or preserve an unqualified compatibility path."""

    target = Path(path)
    return host_identity.namespaced_path(target.parent, target.name) if host_identity else target


def host_partitioned_state_dir(state_dir: Path | str) -> Path:
    """Return this host's private subdirectory of a possibly-shared root.

    The key is the stable host ID, never the display hostname: hostnames are
    reused when a machine is renamed or cloned, so they cannot be a durable
    storage key.
    """

    return Path(state_dir) / HOST_PARTITION_DIRNAME / current_host_identity().stable_host_id
