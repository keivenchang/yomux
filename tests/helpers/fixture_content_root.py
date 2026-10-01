# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Private content roots for generated browser fixture pages."""

from __future__ import annotations

import atexit
import os
import re
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote


_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class FixtureContentRoot:
    """Own generated pages outside the repository and mount repo assets read-only."""

    repo_root: Path
    root: Path
    namespace: str
    _sequence: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def create(cls, repo_root: Path, *, parent: Path | None = None) -> "FixtureContentRoot":
        namespace = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        root = Path(tempfile.mkdtemp(prefix=f"yolomux-browser-fixtures-{namespace}-", dir=parent))
        owner = cls(Path(repo_root).resolve(), root.resolve(), namespace)
        atexit.register(owner.cleanup)
        return owner

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def write_page(self, worker: str, filename: str, html: str) -> Path:
        clean_worker = _SAFE_FILENAME.sub("-", str(worker or "main")).strip(".-") or "main"
        clean_filename = _SAFE_FILENAME.sub("-", Path(filename).name).strip(".-") or "fixture.html"
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        page = self.root / f"browser-fixture-{clean_worker}-{sequence}-{clean_filename}"
        page.write_text(html, encoding="utf-8")
        return page

    def url_path(self, page: Path) -> str:
        resolved = Path(page).resolve()
        if resolved.parent != self.root:
            raise ValueError("fixture page is outside its content root")
        return f"/{resolved.name}"

    def resolve_request_path(self, request_path: str) -> Path | None:
        """Resolve a URL path without granting writes or traversal into either root."""

        relative = Path(unquote(request_path).split("?", 1)[0].lstrip("/"))
        if relative.is_absolute() or ".." in relative.parts:
            return None
        if relative.parts and relative.parts[0] == "static":
            candidate = (self.repo_root / relative).resolve()
            static_root = (self.repo_root / "static").resolve()
            if candidate != static_root and static_root not in candidate.parents:
                return None
            return candidate if candidate.is_file() else None
        candidate = (self.root / relative).resolve()
        if candidate.parent != self.root:
            return None
        return candidate if candidate.is_file() else None
