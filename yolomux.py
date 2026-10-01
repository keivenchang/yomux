#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Deprecated command alias for :mod:`yomux`."""

from __future__ import annotations

import sys
from yomux import main as _yomux_main


def main() -> int:
    """Warn about the legacy command and entrypoint, then delegate to YOmux."""
    print("WARNING: the yolomux command and entrypoint are deprecated; use yomux.", file=sys.stderr)
    return _yomux_main()


if __name__ == "__main__":
    raise SystemExit(main())
