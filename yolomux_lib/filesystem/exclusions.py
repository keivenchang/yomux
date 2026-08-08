# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The one exclusion owner for ignored directories and configured excluded paths.

Every producer and consumer that decides whether a path may appear in a watch
revision, a reconciliation signature, browser filesystem history, a diagnostic
or an indexing unit routes that decision through :func:`path_exclusion_verdict`.
Before this owner existed the watch daemon and the search index each carried
their own copy of the rule, and the copies had already diverged: the daemon
admitted selected ``.git`` control files that the index always excluded, which
made an ignored pathname a transport signal.  A second copy of this rule is a
defect, not an optimisation.

The verdict is typed.  A caller that only learns "excluded" cannot report which
configured rule excluded the path, so every refusal carries a machine-readable
reason code and the exact detail that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import paths as paths_module

# Version-control metadata is never user content and is never admitted, even
# when a configuration omits it.  The configured set is still the general
# policy; this is only a floor, so dropping ".git" from ``skip_dirs`` cannot
# flood every watcher with object and log churn the way it would if exclusion
# depended solely on configuration.
ALWAYS_IGNORED_DIRECTORY_NAMES = frozenset({".git", ".hg", ".svn", ".jj"})

EXCLUSION_SKIP_DIR = "skip_dir"
EXCLUSION_SECRET = "secret_path"
EXCLUSION_CONFIGURED_PATH = "configured_exclude_path"
EXCLUSION_OUTSIDE_ROOTS = "outside_configured_roots"
EXCLUSION_UNRESOLVABLE = "unresolvable_path"


@dataclass(frozen=True)
class ExclusionVerdict:
    """Why one path is or is not admitted, in a form a caller can report."""

    excluded: bool
    reason_code: str = ""
    detail: str = ""

    def as_reason(self) -> dict[str, Any]:
        return {"excluded": self.excluded, "reason_code": self.reason_code, "detail": self.detail}


ADMITTED = ExclusionVerdict(excluded=False)


def _skip_dir_hit(
    path: Path,
    resolved: Path,
    skip_dirs: Iterable[str],
    relative_to: Path | None,
) -> str:
    """Return the ignored directory name that covers this path, or ``""``.

    ``relative_to`` scopes the check to the parts below one owning root.  The
    search index needs that scope because a configured index root may itself sit
    beneath a directory whose name is ignored, and indexing a root the user
    explicitly asked for is not the same as indexing an ignored subtree.  The
    watch daemon passes ``None`` and tests every part, including ancestors.
    """

    names = frozenset(skip_dirs) | ALWAYS_IGNORED_DIRECTORY_NAMES
    if relative_to is not None:
        try:
            candidates = resolved.relative_to(relative_to).parts
        except ValueError:
            return ""
    else:
        candidates = (*path.parts, *resolved.parts)
    for part in candidates:
        if part in names:
            return part
    return ""


def path_exclusion_verdict(
    path: Path,
    *,
    skip_dirs: Iterable[str] = (),
    resolved: Path | None = None,
    configured_roots: Sequence[str] = (),
    exclude_path: Callable[[Path], bool] | None = None,
    relative_to: Path | None = None,
) -> ExclusionVerdict:
    """Decide one path against the complete configured exclusion policy.

    No caller may re-admit a path this owner excludes.  In particular there is
    no exception for Git control files: ``.git`` is an ignored directory like
    ``.cache``, ``node_modules`` or a user-configured exclusion, and a change
    beneath any of them must not reach a revision, a generation bump, browser
    history, a diagnostic or an indexing unit.
    """

    if resolved is None:
        try:
            resolved = path.expanduser().resolve(strict=False)
        except OSError:
            return ExclusionVerdict(True, EXCLUSION_UNRESOLVABLE, str(path))
    skip_hit = _skip_dir_hit(path, resolved, skip_dirs, relative_to)
    if skip_hit:
        return ExclusionVerdict(True, EXCLUSION_SKIP_DIR, skip_hit)
    if paths_module._path_is_secret(path, resolved=resolved):
        return ExclusionVerdict(True, EXCLUSION_SECRET, str(resolved))
    if configured_roots and not any(
        paths_module._normalized_absolute_text_is_within(str(resolved), root)
        for root in configured_roots
    ):
        return ExclusionVerdict(True, EXCLUSION_OUTSIDE_ROOTS, str(resolved))
    if exclude_path is not None and bool(exclude_path(path)):
        return ExclusionVerdict(True, EXCLUSION_CONFIGURED_PATH, str(resolved))
    return ADMITTED
