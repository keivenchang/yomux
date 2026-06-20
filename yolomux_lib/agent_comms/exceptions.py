# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared agent communication exceptions."""


class TransportInterrupted(RuntimeError):
    """Raised inside a managed transport when a caller cancels the active turn."""

