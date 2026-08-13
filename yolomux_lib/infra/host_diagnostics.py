# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Bounded operator diagnostics for mutable roots and local storage.

This module deliberately consumes the existing common-root, HostIdentity, and
filesystem-classification owners rather than reconstructing their logic.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from typing import Iterable
from typing import Mapping

from ..chat.chat_store import CHAT_DATABASE_NAME
from ..chat.chat_store import default_chat_database_path
from ..login_rate_limit import LOGIN_THROTTLE_DATABASE_NAME
from ..login_rate_limit import default_login_throttle_database_path
from ..observability.pricing_catalog import PricingPaths
from ..observability.pricing_catalog import default_pricing_cache_dir
from ..search.file_index import default_index_dir
from ..stats_current.service import default_database_path
from ..stats_current.storage import DATABASE_FILENAME
from . import common
from .filesystem_preflight import FilesystemClassification
from .filesystem_preflight import NETWORK_FILESYSTEM_ESCAPE_HATCH
from .filesystem_preflight import classify_filesystem
from .host_identity import HostIdentity
from .host_identity import HostIdentityError
from .host_identity import LateHostIdentityOverrideError
from .host_identity import current_host_identity
from .host_partition import HOST_PARTITION_DIRNAME


HOST_IDENTITY_UNAVAILABLE = "HostIdentity could not be established"
RUNTIME_ROOT_UNAVAILABLE = "No host-local runtime root has been configured; legacy state root still owns runtime files"


@dataclass(frozen=True)
class RootLayout:
    """Resolved root inputs consumed from the current root owner."""

    config: Path
    state: Path
    cache: Path
    runtime: Path | None

    @classmethod
    def current(cls) -> "RootLayout":
        return cls(common.CONFIG_DIR, common.STATE_DIR, common.YOLOMUX_CACHE_DIR, common.RUNTIME_DIR)


@dataclass(frozen=True)
class DatabasePartition:
    """One configured live-WAL database partition."""

    name: str
    path: Path
    partition_key: str = ""


@dataclass(frozen=True)
class RejectedMutablePath:
    """A preflight refusal supplied by the server's mutable-root boundary."""

    path: Path
    reason: str


@dataclass(frozen=True)
class _RootDiagnostic:
    kind: str
    path: Path | None
    classification: FilesystemClassification | None
    reason: str = ""

    def payload(self, *, admin: bool) -> dict[str, object]:
        if self.path is None or self.classification is None:
            return {
                "kind": self.kind,
                "filesystem_type": "undetermined",
                "network_filesystem": None,
                "determined": False,
                "reason": self.reason,
            }
        result: dict[str, object] = {
            "kind": self.kind,
            "filesystem_type": self.classification.filesystem_type,
            "network_filesystem": self.classification.is_network,
            "determined": self.classification.determined,
        }
        if admin:
            result["path"] = str(self.path)
        return result


@dataclass(frozen=True)
class HostDiagnosticsReport:
    identity: HostIdentity | None
    identity_reason: str
    identity_reason_code: str
    roots: tuple[_RootDiagnostic, ...]
    database_partitions: tuple[DatabasePartition, ...]
    rejected_paths: tuple[RejectedMutablePath, ...]
    escape_hatch_enabled: bool

    def payload(self, *, admin: bool) -> dict[str, object]:
        """Serialize diagnostics, withholding absolute paths outside admin routes."""
        return {
            "identity": _identity_payload(self.identity, reason=self.identity_reason),
            "identity_reason_code": self.identity_reason_code,
            "roots": [root.payload(admin=admin) for root in self.roots],
            "database_partitions": [_database_payload(item, admin=admin) for item in self.database_partitions],
            "rejected_mutable_paths": [_rejection_payload(item, admin=admin) for item in self.rejected_paths],
            "network_filesystem_escape_hatch": self.escape_hatch_enabled,
        }


def _identity_value(value: str | None, *, reason: str) -> dict[str, object]:
    return {"value": value} if value else {"value": None, "reason": reason}


def _identity_payload(identity: HostIdentity | None, *, reason: str) -> dict[str, object]:
    if identity is None:
        return {
            "stable_host_id": _identity_value(None, reason=reason),
            "display_hostname": _identity_value(None, reason=reason),
            "boot_id": _identity_value(None, reason=reason),
            "process_start_identity": _identity_value(None, reason=reason),
            "stable_host_id_source": _identity_value(None, reason=reason),
        }
    return {
        "stable_host_id": _identity_value(identity.stable_host_id, reason=reason),
        "display_hostname": _identity_value(identity.display_hostname, reason=reason),
        "boot_id": _identity_value(identity.boot_id, reason=reason),
        "process_start_identity": _identity_value(identity.process_start_identity, reason=reason),
        "stable_host_id_source": _identity_value(identity.stable_host_id_source, reason=reason),
    }


def _database_payload(partition: DatabasePartition, *, admin: bool) -> dict[str, object]:
    path = partition.path.expanduser().resolve(strict=False)
    try:
        status = "active" if stat.S_ISREG(path.stat().st_mode) else "not_created"
        reason = ""
    except FileNotFoundError:
        status = "not_created"
        reason = ""
    except OSError as error:
        status = "undetermined"
        reason = type(error).__name__
    result: dict[str, object] = {"name": partition.name, "status": status}
    if partition.partition_key:
        result["partition_key"] = partition.partition_key
    result["path" if admin else "path_name"] = str(path) if admin else path.name
    if reason:
        result["reason"] = reason
    return result


def _rejection_payload(rejected: RejectedMutablePath, *, admin: bool) -> dict[str, object]:
    path = rejected.path.expanduser().resolve(strict=False)
    result: dict[str, object] = {"reason": rejected.reason}
    result["path" if admin else "path_name"] = str(path) if admin else path.name
    return result


def current_database_partitions(roots: RootLayout, *, identity: HostIdentity | None = None) -> tuple[DatabasePartition, ...]:
    """Consume the existing database naming owners without scanning arbitrary trees."""
    state = roots.state.expanduser()
    cache = roots.cache.expanduser()
    if identity is None:
        chat_database = default_chat_database_path(state)
        login_throttle_database = default_login_throttle_database_path(state)
        stats_database = default_database_path(state)
        pricing_root = default_pricing_cache_dir(cache)
        index_dir = default_index_dir(state)
        partition_key = ""
    else:
        # Diagnostics must report a rejected late override. Re-resolving the
        # identity through each store's path owner would turn that report into
        # the very exception the report is meant to expose.
        state_partition = state / HOST_PARTITION_DIRNAME / identity.stable_host_id
        cache_partition = cache / HOST_PARTITION_DIRNAME / identity.stable_host_id
        chat_database = state_partition / CHAT_DATABASE_NAME
        login_throttle_database = state_partition / LOGIN_THROTTLE_DATABASE_NAME
        stats_database = state_partition / DATABASE_FILENAME
        pricing_root = cache_partition / "model-pricing"
        index_dir = state_partition / "search_index"
        partition_key = identity.stable_host_id
    partitions = [
        DatabasePartition("chat", chat_database, partition_key=partition_key),
        DatabasePartition("login-throttle", login_throttle_database, partition_key=partition_key),
        DatabasePartition(
            "stats-current",
            stats_database,
            partition_key=partition_key,
        ),
        DatabasePartition(
            "model-pricing",
            PricingPaths.from_root(pricing_root).database,
            partition_key=partition_key,
        ),
    ]
    partitions.append(DatabasePartition("search-index", index_dir, partition_key=partition_key))
    try:
        partitions.extend(
            DatabasePartition(f"search-index:{path.stem}", path, partition_key=partition_key)
            for path in sorted(index_dir.glob("*.sqlite3"))
        )
    except OSError:
        pass
    return tuple(partitions)


def collect_host_diagnostics(
    *,
    roots: RootLayout | None = None,
    identity: HostIdentity | None = None,
    classifier: Callable[[Path], FilesystemClassification] | None = None,
    database_partitions: Iterable[DatabasePartition] | None = None,
    rejected_paths: Iterable[RejectedMutablePath] = (),
    environ: Mapping[str, str] | None = None,
) -> HostDiagnosticsReport:
    """Collect current diagnostics without creating directories or mutable state."""
    layout = roots or RootLayout.current()
    if identity is None:
        try:
            resolved_identity = current_host_identity()
            identity_reason = ""
            identity_reason_code = ""
        except LateHostIdentityOverrideError as error:
            resolved_identity = error.identity
            identity_reason = str(error)
            identity_reason_code = error.reason_code
        except HostIdentityError:
            resolved_identity = None
            identity_reason = HOST_IDENTITY_UNAVAILABLE
            identity_reason_code = "host_identity_unavailable"
    else:
        resolved_identity = identity
        identity_reason = ""
        identity_reason_code = ""
    active_classifier = classify_filesystem if classifier is None else classifier
    root_items = (("config", layout.config), ("state", layout.state), ("cache", layout.cache))
    diagnostics = tuple(
        _RootDiagnostic(kind, path.expanduser().resolve(strict=False), active_classifier(path))
        for kind, path in root_items
    )
    runtime = (
        _RootDiagnostic("runtime", None, None, RUNTIME_ROOT_UNAVAILABLE)
        if layout.runtime is None
        else _RootDiagnostic("runtime", layout.runtime.expanduser().resolve(strict=False), active_classifier(layout.runtime))
    )
    values = os.environ if environ is None else environ
    if database_partitions is None:
        try:
            resolved_partitions = current_database_partitions(layout, identity=resolved_identity)
        except HostIdentityError:
            resolved_partitions = ()
    else:
        resolved_partitions = tuple(database_partitions)
    return HostDiagnosticsReport(
        identity=resolved_identity,
        identity_reason=identity_reason,
        identity_reason_code=identity_reason_code,
        roots=(*diagnostics, runtime),
        database_partitions=resolved_partitions,
        rejected_paths=tuple(rejected_paths),
        escape_hatch_enabled=values.get(NETWORK_FILESYSTEM_ESCAPE_HATCH) == "1",
    )
