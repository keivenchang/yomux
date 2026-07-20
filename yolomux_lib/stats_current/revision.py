# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""One content revision shared by the current stats daemon and client."""

from __future__ import annotations

import hashlib
from pathlib import Path

_CURRENT_MODULES = (
    "families.py",
    "identity.py",
    "materializer.py",
    "migration.py",
    "pricing.py",
    "protocol.py",
    "resolution.py",
    "revision.py",
    "service.py",
    "storage.py",
    "usage.py",
)


def _current_code_revision() -> str:
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for relative in _CURRENT_MODULES:
        digest.update((root / relative).resolve().read_bytes())
    return digest.hexdigest()[:12]


CURRENT_CODE_REVISION = _current_code_revision()
