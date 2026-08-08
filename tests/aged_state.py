"""Recipe-generated, sanitized state for restart and multi-step regression tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import json
import os
import time
from typing import Any

from yolomux_lib.infra import common
from yolomux_lib.infra.host_identity import current_host_identity
from yolomux_lib.infra.host_partition import host_partitioned_state_dir
from yolomux_lib.local_services.rpc import encode_metadata
from yolomux_lib.local_services.rpc import LOCAL_RPC_MAX_METADATA_BYTES
from yolomux_lib.local_services.rpc import new_envelope
from yolomux_lib.stats_current import storage
from yolomux_lib.stats_current.storage import Observation
from yolomux_lib.workspace import session_files


LIVE_EVENT_DENSITY = {
    "state_changed": 10_366,
    "background_refresh_fallback": 476,
    "native_filesystem_watch_loss": 451,
    "stale_owner_heartbeat": 466,
    "request_completed": 203,
}


@dataclass(frozen=True)
class AgedStateRecipeResult:
    name: str
    paths: tuple[Path, ...]
    details: Mapping[str, Any]


class AgedStateRoot:
    """One fixture-owned root whose semantic age conditions are independently selectable."""

    def __init__(self, root: Path, *, home_dir: Path, state_dir: Path, cache_dir: Path, runtime_dir: Path):
        self.root = Path(root)
        self.home_dir = Path(home_dir)
        self.state_dir = Path(state_dir)
        self.cache_dir = Path(cache_dir)
        self.runtime_dir = Path(runtime_dir)
        self.workspace_dir = self.home_dir / "dev"
        self.host_state_dir = host_partitioned_state_dir(self.state_dir)
        self.results: dict[str, AgedStateRecipeResult] = {}
        self._open_stats_stores: list[storage.Store] = []
        for path in (self.root, self.home_dir, self.state_dir, self.cache_dir, self.runtime_dir, self.workspace_dir, self.host_state_dir):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def available_recipes(self) -> tuple[str, ...]:
        return (
            "coexisting_transcript_caches",
            "eof_transcript_cursor",
            "stats_wal",
            "event_history",
            "stale_owner_epochs",
            "finder_resource_history",
            "rpc_metadata_boundaries",
        )

    def apply(self, recipe: str, **options: Any) -> AgedStateRecipeResult:
        """Apply one named recipe once without creating any unrequested condition."""

        name = str(recipe or "")
        if name not in self.available_recipes:
            raise ValueError(f"unknown aged-state recipe {name!r}; expected one of {self.available_recipes}")
        if name in self.results:
            raise ValueError(f"aged-state recipe {name!r} was already applied")
        builders = {
            "coexisting_transcript_caches": self._coexisting_transcript_caches,
            "eof_transcript_cursor": self._eof_transcript_cursor,
            "stats_wal": self._stats_wal,
            "event_history": self._event_history,
            "stale_owner_epochs": self._stale_owner_epochs,
            "finder_resource_history": self._finder_resource_history,
            "rpc_metadata_boundaries": self._rpc_metadata_boundaries,
        }
        result = builders[name](**options)
        for path in result.paths:
            self._assert_owned(path)
        self.results[name] = result
        return result

    def close(self) -> None:
        while self._open_stats_stores:
            self._open_stats_stores.pop().close()

    def _assert_owned(self, path: Path) -> None:
        target = Path(path).resolve(strict=False)
        if not target.is_relative_to(self.root.resolve(strict=False)):
            raise AssertionError(f"aged-state recipe escaped fixture root {self.root}: {path}")

    @staticmethod
    def _write_json(path: Path, value: Any, *, mode: int = 0o600) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        path.chmod(mode)
        return path

    def _coexisting_transcript_caches(
        self,
        *,
        shared_count: int = 1_524,
        host_count: int = 993,
    ) -> AgedStateRecipeResult:
        if shared_count < 0 or host_count < 0:
            raise ValueError("transcript cache counts must be nonnegative")
        version = session_files._TRANSCRIPT_SCAN_STORE_VERSION
        shared_dir = self.state_dir / f"transcript-scan-cache-v{version}"
        host_dir = self.host_state_dir / f"transcript-scan-cache-v{version}"
        aged_at = 1_700_000_000
        for scope, directory, count in (("shared", shared_dir, shared_count), ("host", host_dir, host_count)):
            directory.mkdir(parents=True, exist_ok=True)
            for index in range(count):
                record = {
                    "schema_version": version,
                    "identity": ["codex", 7, 1, index + 1, str(self.workspace_dir / f"synthetic-{scope}-{index:04d}.jsonl")],
                    "state": {"offset": 128, "size": 128, "prefix_digest": f"fixture-{index:08d}"},
                }
                path = self._write_json(directory / f"{scope}-{index:04d}.json", record)
                os.utime(path, (aged_at + index, aged_at + index))
        return AgedStateRecipeResult(
            "coexisting_transcript_caches",
            (shared_dir, host_dir),
            {"shared_count": shared_count, "host_count": host_count, "version": version},
        )

    def _eof_transcript_cursor(self) -> AgedStateRecipeResult:
        transcript = self.workspace_dir / "transcripts" / "synthetic-rollout.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": str(self.workspace_dir)}}) + "\n", encoding="utf-8")
        identity = session_files.codex_transcript_scan_cache_key(transcript, str(self.workspace_dir))
        if identity is None:
            raise AssertionError("fixture transcript did not produce a scan identity")
        state = session_files.new_codex_transcript_scan_state()
        size = transcript.stat().st_size
        state.update({
            "offset": size,
            "size": size,
            "prefix_digest": session_files.transcript_scan_prefix_digest(transcript),
        })
        if not session_files.persist_transcript_scan_state(identity, state, force=True):
            raise AssertionError("fixture EOF cursor was not persisted")
        cursor = session_files.transcript_scan_store_path(identity)
        return AgedStateRecipeResult(
            "eof_transcript_cursor",
            (transcript, cursor),
            {"identity": list(identity), "offset": size, "size": size},
        )

    def _stats_wal(
        self,
        *,
        minimum_wal_bytes: int = 64 * 1024,
        payload_bytes: int = 2_048,
    ) -> AgedStateRecipeResult:
        if minimum_wal_bytes < 1 or payload_bytes < 1:
            raise ValueError("WAL and payload sizes must be positive")
        database = storage.default_database_path(self.state_dir)
        store = storage.Store.open(database)
        self._open_stats_stores.append(store)
        connection = store._database
        if connection is None:
            raise AssertionError("fixture stats store closed during setup")
        connection.execute("PRAGMA wal_autocheckpoint = 0")
        wal_path = Path(f"{database}-wal")
        index = 0
        padding = "x" * payload_bytes
        while not wal_path.exists() or wal_path.stat().st_size < minimum_wal_bytes:
            batch = tuple(
                Observation(
                    f"aged-event-{index + offset}",
                    "browser",
                    "aged-fixture",
                    1_700_000_000.0 + index + offset,
                    "aged-epoch",
                    7,
                    {"kind": "aged-fixture", "padding": padding},
                )
                for offset in range(32)
            )
            store.append_batch(observations=batch)
            index += len(batch)
            if index > 100_000:
                raise AssertionError(f"fixture WAL did not reach {minimum_wal_bytes} bytes")
        return AgedStateRecipeResult(
            "stats_wal",
            (database, wal_path),
            {"database_bytes": database.stat().st_size, "wal_bytes": wal_path.stat().st_size, "observations": index},
        )

    def _event_history(
        self,
        *,
        counts: Mapping[str, int] | None = None,
    ) -> AgedStateRecipeResult:
        selected = dict(LIVE_EVENT_DENSITY if counts is None else counts)
        if not selected or any(not str(name) or int(count) < 0 for name, count in selected.items()):
            raise ValueError("event-history counts must contain nonnegative named totals")
        event_path = common.event_log_path(self.state_dir)
        event_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        sequence = 0
        for event_type, count in selected.items():
            for occurrence in range(int(count)):
                sequence += 1
                lines.append(json.dumps({
                    "time": f"2026-07-{1 + (sequence // 86_400):02d}T00:00:00Z",
                    "session": f"fixture-{occurrence % 4}",
                    "type": str(event_type),
                    "message": f"sanitized {event_type}",
                    "details": {"fixture": True, "sequence": sequence},
                }, sort_keys=True, separators=(",", ":")))
        event_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return AgedStateRecipeResult(
            "event_history",
            (event_path,),
            {"counts": selected, "lines": sequence},
        )

    def _stale_owner_epochs(self, *, epoch_count: int = 4) -> AgedStateRecipeResult:
        if epoch_count < 1:
            raise ValueError("owner epoch count must be positive")
        owner_dir = self.runtime_dir / "background-owner"
        generations_dir = owner_dir / "generations"
        generations_dir.mkdir(parents=True, exist_ok=True)
        identity = current_host_identity()
        records = {}
        paths = []
        stale_heartbeat = time.time() - 3_600.0
        for index in range(epoch_count):
            generation_id = f"170000000000000000{index}-2000000000-aged{index:02d}"
            record = {
                **identity.process_record_fields(
                    pid=2_000_000_000,
                    start_identity=f"aged-start-{index}",
                    display_hostname="aged-fixture-host",
                    instance_nonce=f"aged-nonce-{index}",
                ),
                "generation_id": generation_id,
                "started_at_ns": 1_700_000_000_000_000_000 + index,
                "nonce": f"aged-nonce-{index}",
                "port": 79_000 + index,
                "project_root": str(self.workspace_dir),
                "control_socket": str(self.runtime_dir / "control" / f"aged-{index}.sock"),
                "priority": 0,
                "capabilities": {},
                "roles": ["session-files", "watch-roots"],
                "last_heartbeat": stale_heartbeat - index,
                "owner": index == epoch_count - 1,
                "status": "owner" if index == epoch_count - 1 else "released",
                "counters": {"owner_acquired": 1, "owner_released": int(index < epoch_count - 1)},
            }
            path = self._write_json(generations_dir / f"{generation_id}.json", record)
            paths.append(path)
            records[generation_id] = record
        index_path = self._write_json(generations_dir / "index.json", {"version": 1, "records": records})
        owner_path = self._write_json(owner_dir / "owner.json", records[next(reversed(records))])
        return AgedStateRecipeResult(
            "stale_owner_epochs",
            tuple([owner_path, index_path, *paths]),
            {"epochs": epoch_count, "stale_heartbeat": stale_heartbeat},
        )

    def _finder_resource_history(
        self,
        *,
        top_level_entries: int = 99,
        nested_entries: int = 8,
    ) -> AgedStateRecipeResult:
        if top_level_entries < 2 or nested_entries < 1:
            raise ValueError("Finder history needs at least two top-level and one nested entry")
        dev_root = self.workspace_dir
        subdirectory = dev_root / "ai-config"
        subdirectory.mkdir(parents=True, exist_ok=True)
        for index in range(top_level_entries - 1):
            sibling = dev_root / ("ant" if index == 0 else f"project-{index:03d}")
            sibling.mkdir(parents=True, exist_ok=True)
        for index in range(nested_entries):
            nested = subdirectory / f"nested-{index:02d}"
            if index % 2:
                nested.write_text(f"sanitized fixture row {index}\n", encoding="utf-8")
            else:
                nested.mkdir(parents=True, exist_ok=True)
        deleted_root = self.home_dir / "deleted-root"
        deleted_root.mkdir(parents=True, exist_ok=True)
        deleted_root.rmdir()
        first_target = self.home_dir / "repoint-target-a"
        second_target = self.home_dir / "repoint-target-b"
        first_target.mkdir(parents=True, exist_ok=True)
        second_target.mkdir(parents=True, exist_ok=True)
        repointed_root = self.home_dir / "repointed-root"
        repointed_root.symlink_to(first_target, target_is_directory=True)
        repointed_root.unlink()
        repointed_root.symlink_to(second_target, target_is_directory=True)
        manifest = self.state_dir / "aged-fixture" / "finder-history.json"
        history = {
            "root": str(self.home_dir),
            "dev_root": str(dev_root),
            "subdirectory": str(subdirectory),
            "nested_probe": str(subdirectory / "nested-01"),
            "deleted_root": str(deleted_root),
            "repointed_root": str(repointed_root),
            "repointed_target": str(second_target),
            "expansion_records": [
                {"phase": "expand-dev", "expanded": [str(dev_root)]},
                {"phase": "open-subdirectory", "expanded": [str(dev_root), str(subdirectory)]},
                {"phase": "collapse-dev", "expanded": [str(subdirectory)]},
                {"phase": "reexpand-dev", "expanded": [str(dev_root), str(subdirectory)]},
            ],
        }
        self._write_json(manifest, history)
        return AgedStateRecipeResult(
            "finder_resource_history",
            (dev_root, subdirectory, deleted_root, repointed_root, second_target, manifest),
            {**history, "top_level_entries": top_level_entries, "nested_entries": nested_entries},
        )

    @staticmethod
    def _metadata_payload(target_size: int) -> tuple[dict[str, Any], int]:
        payload = {
            "action": "submit",
            "task": "tabber_activity_view",
            "payload": {
                "sessions": {
                    "fixture": {
                        "info": {"session": "fixture", "panes": [], "agents": []},
                        "recent_paths_by_agent": [[{"path": "/fixture/repo/file.py", "mtime": 1_700_000_000.0, "status": "M"}]],
                        "transcript_views_by_path": {},
                    }
                },
                "watch_roots": ["/fixture/repo"],
                "repositories": [{"root": "/fixture/repo", "branch": "main", "status": "M"}],
                "padding": "",
            },
        }
        envelope = new_envelope("jobd", "submit", payload, timeout_seconds=0.5, trace_id="a" * 32)
        base_size = len(encode_metadata(envelope))
        if base_size > target_size:
            raise AssertionError(f"production-shaped metadata base {base_size} exceeds target {target_size}")
        payload["payload"]["padding"] = "x" * (target_size - base_size)
        encoded_size = len(encode_metadata(new_envelope("jobd", "submit", payload, timeout_seconds=0.5, trace_id="a" * 32)))
        if encoded_size != target_size:
            raise AssertionError(f"metadata recipe produced {encoded_size} bytes, expected {target_size}")
        return payload, encoded_size

    def _rpc_metadata_boundaries(self) -> AgedStateRecipeResult:
        below_payload, below_size = self._metadata_payload(LOCAL_RPC_MAX_METADATA_BYTES - 1)
        above_payload, above_size = self._metadata_payload(LOCAL_RPC_MAX_METADATA_BYTES + 1)
        directory = self.state_dir / "aged-fixture" / "rpc-metadata"
        below_path = self._write_json(directory / "below.json", below_payload)
        above_path = self._write_json(directory / "above.json", above_payload)
        return AgedStateRecipeResult(
            "rpc_metadata_boundaries",
            (below_path, above_path),
            {"below_bytes": below_size, "above_bytes": above_size, "limit_bytes": LOCAL_RPC_MAX_METADATA_BYTES},
        )
