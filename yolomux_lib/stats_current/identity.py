# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""One identity-text contract for current YO!stats boundaries."""

from __future__ import annotations

import hashlib
import re


# Compiled once, not rebuilt per call. The equivalent `any(ord(c) < 32 or ord(c) == 127
# for c in text)` genexpr ran 11.1M times and made 21.5M `ord()` calls in ONE 24h
# materialization, because every identity of every stored observation is re-checked on
# every fold. Measured on a 195k-observation live window, moving that loop into C took
# the cold build from 2.805s to 2.522s (best of three, no profiler attached). The scan
# is identical: verified equal on 50,012 strings including every boundary code point.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

MAX_EVENT_ID_BYTES = 512
MAX_IDENTITY_BYTES = 256
MAX_SERIES_COMPONENT_BYTES = 192


class IdentityValidationError(ValueError):
    """An identity cannot safely cross storage, materialization, or wire."""


def identity_text(
    value: object,
    name: str,
    *,
    maximum_bytes: int = MAX_IDENTITY_BYTES,
    strip: bool = False,
) -> str:
    if not isinstance(value, str):
        raise IdentityValidationError(f"{name} must be a non-empty string")
    normalized = value.strip() if strip else value
    if not normalized or not normalized.strip():
        raise IdentityValidationError(f"{name} must be a non-empty string")
    if _CONTROL_CHARACTERS.search(normalized) is not None:
        raise IdentityValidationError(f"{name} contains control characters")
    if len(normalized.encode("utf-8")) > maximum_bytes:
        raise IdentityValidationError(f"{name} exceeds {maximum_bytes} bytes")
    return normalized


def legacy_identity(
    value: object,
    scope: str,
    *,
    maximum_bytes: int = MAX_SERIES_COMPONENT_BYTES,
) -> tuple[str, bool]:
    """Preserve a safe legacy identity or replace it with a stable opaque key."""

    try:
        return identity_text(value, scope, maximum_bytes=maximum_bytes, strip=True), False
    except IdentityValidationError:
        encoded = str(value).encode("utf-8", errors="surrogatepass")
        digest = hashlib.sha256(encoded).hexdigest()
        return f"retired-{scope}:{digest}", True
