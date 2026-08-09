# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The offered nightly-prune times, owned by a module that imports nothing.

These three values are leaf data: a default, a step, and the list of choices
derived from them.  They live outside ``stats_current`` because both
``workspace.settings`` and ``stats_current.prune_schedule`` need them, and an
edge between those two packages closes an import cycle.

The cycle, measured on 2026-08-08, was
``approvald -> yolo_rules -> settings -> workspace.settings ->
stats_current.prune_schedule -> stats_current -> storage -> filesystem.io_ops ->
filesystem -> search -> settings (partially initialised)``.  Every leg except the
``workspace.settings -> prune_schedule`` one predates July; that last edge closed
the ring.  The visible symptom was that ``python3 -m yolomux_lib.approvald``
died on import, so every local service that starts as its own process could not
start at all -- while the in-process test suite stayed green, because it imports
the whole package once in dependency order and never spawns a module alone.

This module must import nothing from ``yolomux_lib``.  A single import here
would rebuild the ring it exists to break, and the next symptom would again be
invisible to any test that does not spawn a real child process.
"""

from __future__ import annotations

from typing import Final

DEFAULT_PRUNE_LOCAL_TIME: Final[str] = "02:30"
PRUNE_LOCAL_TIME_STEP_MINUTES: Final[int] = 30
# The offered choices, in clock order.  One owner: settings validates against
# this tuple and the Preferences dropdown renders it.
PRUNE_LOCAL_TIME_CHOICES: Final[tuple[str, ...]] = tuple(
    f"{hour:02d}:{minute:02d}"
    for hour in range(24)
    for minute in range(0, 60, PRUNE_LOCAL_TIME_STEP_MINUTES)
)
