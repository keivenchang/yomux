# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Typed, localizable filesystem failures shared by every filesystem entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..common import ErrorPayload
from ..common import error_payload


class FilesystemError(Exception):
    """A filesystem failure with stable user-message metadata and optional raw diagnostics."""

    def __init__(
        self,
        message: str,
        status: int = 400,
        *,
        message_key: str = "common.requestFailed",
        message_params: dict[str, Any] | None = None,
        diagnostic: object = "",
    ):
        super().__init__(message)
        self.status = int(status)
        self.message_key = str(message_key)
        self.message_params = dict(message_params or {})
        self.diagnostic = str(diagnostic or "")

    def payload(self, **fields: Any) -> ErrorPayload:
        return error_payload(
            self,
            message_key=self.message_key,
            message_params=self.message_params,
            diagnostic=self.diagnostic,
            status=self.status,
            **fields,
        )

    @classmethod
    def os_error(cls, error: OSError, status: int = 500) -> "FilesystemError":
        return cls(
            "filesystem operation failed",
            status=status,
            message_key="fs.error.operationFailed",
            diagnostic=error,
        )

    @classmethod
    def path_not_found(cls, path: object) -> "FilesystemError":
        return cls(
            f"path not found: {path}",
            status=404,
            message_key="common.pathNotFound",
            message_params={"path": str(path)},
        )

    @classmethod
    def is_directory(cls, path: object) -> "FilesystemError":
        return cls(
            f"is a directory: {path}",
            message_key="fs.error.isDirectory",
            message_params={"path": str(path)},
        )

    @classmethod
    def not_directory(cls, path: object) -> "FilesystemError":
        return cls(
            f"not a directory: {path}",
            message_key="fs.error.notDirectory",
            message_params={"path": str(path)},
        )

    @classmethod
    def target_exists(cls, path: object) -> "FilesystemError":
        return cls(
            f"target already exists: {path}",
            status=409,
            message_key="fs.error.targetExists",
            message_params={"path": str(path)},
        )

    @classmethod
    def file_too_large(cls, size: int, maximum: int, *, label: str = "file") -> "FilesystemError":
        return cls(
            f"{label} too large ({size} bytes; max {maximum})",
            status=413,
            message_key="fs.error.tooLarge",
            message_params={"label": str(label), "size": int(size), "max": int(maximum)},
        )

    @classmethod
    def outside_repo(cls, path: Path) -> "FilesystemError":
        return cls(
            f"path is outside repo: {path}",
            message_key="fs.error.outsideRepo",
            message_params={"path": str(path)},
        )
