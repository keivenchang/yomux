"""Persistent single-writer service for Quick Open indexes.

HTTP/WebSocket servers submit coalesced dirty paths over a Unix-domain socket.
This process is the only component allowed to build or write a search index;
servers use read-only SQLite snapshots for queries.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import file_index
from .filesystem import search


INDEXER_PROTOCOL_VERSION = 1
INDEXER_DEBOUNCE_SECONDS = 2.0
INDEXER_MAX_DEBOUNCE_SECONDS = 15.0
INDEXER_CHURN_BACKOFF_MAX_SECONDS = 30.0
INDEXER_CHURN_WINDOW_SECONDS = 60.0
INDEXER_MAINTENANCE_SECONDS = 2.0
INDEXER_MAX_REQUEST_BYTES = 256 * 1024
INDEXER_SOCKET_NAME = "indexer.sock"
INDEXER_LOCK_NAME = "indexer.lock"
# macOS permits roughly 104 bytes for a Unix-domain socket path. Leave room
# below that ceiling because Python and platform C libraries differ slightly.
UNIX_SOCKET_SAFE_PATH_BYTES = 96


def _safe_socket_path(path: Path) -> Path:
    """Return a deterministic socket path that fits Unix-domain limits."""
    candidate = path.expanduser()
    if len(os.fsencode(str(candidate))) <= UNIX_SOCKET_SAFE_PATH_BYTES:
        return candidate
    digest = hashlib.sha256(str(candidate).encode("utf-8", errors="surrogateescape")).hexdigest()[:20]
    return Path(tempfile.gettempdir()) / f"yolomux-indexer-{os.getuid()}-{digest}.sock"


def default_socket_path() -> Path:
    return _safe_socket_path(file_index.INDEX_DIR / INDEXER_SOCKET_NAME)


def default_lock_path() -> Path:
    return default_socket_path().with_suffix(".lock")


def _read_request(connection: socket.socket) -> dict[str, Any] | None:
    chunks: list[bytes] = []
    while sum(len(chunk) for chunk in chunks) < INDEXER_MAX_REQUEST_BYTES:
        chunk = connection.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    try:
        value = json.loads(b"".join(chunks).decode("utf-8").splitlines()[0])
    except (IndexError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_response(connection: socket.socket, payload: dict[str, Any]) -> None:
    connection.sendall((json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))


class PersistentSearchIndexer:
    """One long-lived, local SQLite writer with a bounded dirty-path queue."""

    def __init__(self, socket_path: Path):
        self.socket_path = _safe_socket_path(socket_path)
        self.lock_path = self.socket_path.with_suffix(".lock")
        self.stop_event = threading.Event()
        self.pending_paths: dict[str, set[str]] = defaultdict(set)
        self.pending_due_at: dict[str, float] = {}
        self.pending_first_at: dict[str, float] = {}
        self.pending_reasons: dict[str, set[str]] = defaultdict(set)
        self.root_last_processed_at: dict[str, float] = {}
        self.root_churn_level: dict[str, int] = defaultdict(int)
        self.next_maintenance_at = 0.0

    def enqueue(self, root: str, paths: list[str], reason: str = "") -> dict[str, Any]:
        clean_root = str(Path(root).expanduser().resolve(strict=False))
        if not clean_root.startswith("/"):
            return {"ok": False, "error": "root must be absolute"}
        clean_paths = {
            str(Path(path).expanduser().resolve(strict=False))
            for path in paths
            if isinstance(path, str) and path.startswith("/")
        }
        now = time.monotonic()
        self.pending_paths[clean_root].update(clean_paths)
        self.pending_reasons[clean_root].add(str(reason or "index-request"))
        first_at = self.pending_first_at.setdefault(clean_root, now)
        churn_level = self.root_churn_level.get(clean_root, 0)
        delay = min(INDEXER_CHURN_BACKOFF_MAX_SECONDS, INDEXER_DEBOUNCE_SECONDS * (2 ** churn_level))
        # Use a trailing debounce to batch a save storm, but impose a maximum
        # wait so an actively changing repository is not starved indefinitely.
        self.pending_due_at[clean_root] = min(first_at + INDEXER_MAX_DEBOUNCE_SECONDS, now + delay)
        return {"ok": True, "accepted": True, "root": clean_root, "queued_paths": len(self.pending_paths[clean_root])}

    def unindex(self, root: str) -> dict[str, Any]:
        clean_root = str(Path(root).expanduser().resolve(strict=False))
        if not clean_root.startswith("/"):
            return {"ok": False, "error": "root must be absolute"}
        self.pending_paths.pop(clean_root, None)
        self.pending_due_at.pop(clean_root, None)
        self.pending_first_at.pop(clean_root, None)
        self.pending_reasons.pop(clean_root, None)
        file_index.unindex(Path(clean_root))
        return {"ok": True, "accepted": True, "root": clean_root}

    def schedule_maintenance_if_due(self, now: float) -> int:
        if now < self.next_maintenance_at:
            return 0
        started = file_index.schedule_refreshes(now=time.time())
        self.next_maintenance_at = now + INDEXER_MAINTENANCE_SECONDS
        return started

    def process_due(self) -> int:
        now = time.monotonic()
        roots = [root for root, due_at in self.pending_due_at.items() if due_at <= now]
        if not roots:
            self.schedule_maintenance_if_due(now)
            return 0
        processed = 0
        for root_text in sorted(roots):
            paths = sorted(self.pending_paths.pop(root_text, set()))
            self.pending_due_at.pop(root_text, None)
            self.pending_first_at.pop(root_text, None)
            self.pending_reasons.pop(root_text, None)
            previous = self.root_last_processed_at.get(root_text, 0.0)
            if previous and now - previous <= INDEXER_CHURN_WINDOW_SECONDS:
                self.root_churn_level[root_text] = min(self.root_churn_level.get(root_text, 0) + 1, 4)
            else:
                self.root_churn_level[root_text] = 0
            self.root_last_processed_at[root_text] = now
            root = Path(root_text)
            if not root.is_dir():
                continue
            # This process owns the in-memory index and its SQLite writer.
            search._ensure_search_index(root)
            if paths:
                search.reindex_roots_for_paths(paths, reason="persistent-indexer")
            else:
                self.schedule_maintenance_if_due(now)
            processed += 1
        return processed

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        action = str(request.get("action") or "")
        if action == "ping":
            return {"ok": True, "version": INDEXER_PROTOCOL_VERSION, "pid": os.getpid()}
        if action == "status":
            return {"ok": True, "status": file_index.runtime_diagnostics()}
        if action == "enqueue":
            raw_paths = request.get("paths", [])
            paths = raw_paths if isinstance(raw_paths, list) else []
            return self.enqueue(str(request.get("root") or ""), paths, str(request.get("reason") or ""))
        if action == "unindex":
            return self.unindex(str(request.get("root") or ""))
        if action == "shutdown":
            self.stop_event.set()
            return {"ok": True}
        return {"ok": False, "error": f"unknown indexer action: {action}"}

    def run(self) -> int:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.socket_path.parent, 0o700)
        except OSError:
            pass
        lock_fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return 0
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                server.bind(str(self.socket_path))
                os.chmod(self.socket_path, 0o600)
                server.listen(16)
                server.settimeout(0.1)
                while not self.stop_event.is_set():
                    try:
                        connection, _address = server.accept()
                    except TimeoutError:
                        self.process_due()
                        continue
                    with connection:
                        request = _read_request(connection)
                        _write_response(connection, self.handle(request) if request is not None else {"ok": False, "error": "invalid request"})
                    self.process_due()
            finally:
                server.close()
        finally:
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)
        return 0


class SearchIndexerClient:
    """Starts or reaches the one persistent indexer without exposing SQLite writes."""

    def __init__(self, socket_path: Path | None = None):
        self.socket_path = _safe_socket_path(socket_path or default_socket_path())
        self.lock = threading.Lock()
        self.process: subprocess.Popen[Any] | None = None

    def request(self, payload: dict[str, Any], timeout: float = 0.5) -> dict[str, Any]:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(timeout)
                client.connect(str(self.socket_path))
                _write_response(client, payload)
                response = _read_request(client)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return response if isinstance(response, dict) else {"ok": False, "error": "invalid indexer response"}

    def healthy(self) -> bool:
        response = self.request({"action": "ping"}, timeout=0.15)
        return bool(response.get("ok")) and int(response.get("version") or 0) == INDEXER_PROTOCOL_VERSION

    def ensure_started(self) -> bool:
        if self.healthy():
            return True
        with self.lock:
            if self.healthy():
                return True
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "yolomux_lib.search_indexer", "--serve", "--socket", str(self.socket_path)],
                    close_fds=True,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                return False
            self.process = process
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if self.healthy():
                    return True
                if process.poll() is not None:
                    return False
                time.sleep(0.03)
        return False

    def enqueue(self, root: str, paths: list[str], reason: str = "") -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "accepted": False, "error": "persistent indexer unavailable"}
        response = self.request({"action": "enqueue", "root": root, "paths": paths, "reason": reason})
        return {**response, "accepted": bool(response.get("ok"))}

    def unindex(self, root: str) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "accepted": False, "error": "persistent indexer unavailable"}
        response = self.request({"action": "unindex", "root": root})
        return {**response, "accepted": bool(response.get("ok"))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YOLOmux persistent Quick Open indexer")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--socket", default=str(default_socket_path()))
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required")
    return PersistentSearchIndexer(Path(args.socket)).run()


if __name__ == "__main__":
    raise SystemExit(main())
