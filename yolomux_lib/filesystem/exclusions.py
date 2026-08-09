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

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
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


INDEX_EXCLUDE_GLOB_PREFIX = "glob:"
INDEX_EXCLUDE_REGEX_PREFIX = "regex:"


def _canonical_text_tuple(value: Any) -> tuple[str, ...] | None:
    """Return ``value`` as a tuple of non-empty strings, or None if ANY member is unusable.

    ONE validator for both policy fields, with exactly TWO outcomes: a fully valid tuple, or
    None.  There is deliberately no path on which a malformed member is skipped and the
    survivors become a quietly different, more permissive policy -- that is what a chain of
    per-field type checks does, and it is how ``{"skip_dir_names": [123]}`` previously produced
    an empty policy that admitted `.cache` and `node_modules`.  Dropping one member is worse
    than rejecting the payload: the worker would then judge by a policy the web owner never
    signed, so its answer and its cache identity would disagree.

    A string is itself a sequence, so it is refused explicitly rather than iterated into
    characters.  Whitespace is tolerated because the settings owner already strips it; a member
    that is empty after stripping cannot have come from that owner and is treated as corruption.
    """

    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        return None
    canonical: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        text = item.strip()
        if not text:
            return None
        canonical.append(text)
    return tuple(canonical)


@dataclass(frozen=True)
class CompiledExclusionPolicy:
    """One :class:`ExclusionPolicy` bound to a root, ready to judge paths under it.

    Compiling is root-dependent -- a plain configured path rule applies only when the root
    actually contains it -- so a caller compiles once per root and reuses the result.
    """

    skip_dirs: frozenset[str]
    rules: tuple[tuple[str, str, "Path | re.Pattern[str]"], ...]
    root: Path

    @property
    def rule_values(self) -> list[str]:
        """The configured rules that survived compilation, as stable ``kind:value`` text."""

        return [f"{kind}:{value}" for kind, value, _matcher in self.rules]

    def matches_configured_rule(self, path: Path) -> bool:
        return any(_exclude_rule_matches(rule, path, self.root) for rule in self.rules)

    def excluded(self, path: Path, *, resolved: Path | None = None) -> bool:
        """Whether this policy refuses ``path``, via the one verdict owner below."""

        return path_exclusion_verdict(
            path,
            skip_dirs=self.skip_dirs,
            resolved=resolved,
            exclude_path=self.matches_configured_rule,
            relative_to=self.root,
        ).excluded


@dataclass(frozen=True)
class ExclusionPolicy:
    """The configured exclusion policy as DATA: serializable, root-independent, signed.

    Two consumers need the same policy in two processes.  The Finder index compiles it against an
    index root in the web process; Differ has to ship it to the ``jobd`` worker, which has no
    access to settings and must not look any up.  Keeping the policy as raw names and raw rule
    text -- never compiled matchers or closures -- is what lets it cross that boundary unchanged,
    and ``signature`` is what lets a cache identity notice that it changed.  Reading settings is
    the caller's job: :meth:`from_settings` takes the mapping, it does not fetch one.
    """

    skip_dir_names: tuple[str, ...] = ()
    exclude_rules: tuple[str, ...] = ()

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any] | None, defaults: Sequence[str] = ()) -> "ExclusionPolicy":
        values = settings if isinstance(settings, Mapping) else {}
        raw_names = values.get("index_exclude_dir_names", list(defaults))
        if not isinstance(raw_names, list):
            raw_names = list(defaults)
        names: set[str] = set()
        for raw_name in raw_names:
            if not isinstance(raw_name, str):
                continue
            name = raw_name.strip()
            if not name or name in {".", ".."} or "/" in name or "\\" in name:
                continue
            names.add(name)
        raw_rules = values.get("index_exclude_paths", [])
        rules = tuple(sorted({rule.strip() for rule in raw_rules if isinstance(rule, str) and rule.strip()})) if isinstance(raw_rules, list) else ()
        return cls(skip_dir_names=tuple(sorted(names)), exclude_rules=rules)

    @classmethod
    def from_payload(cls, payload: Any) -> "ExclusionPolicy | None":
        """Rebuild a policy that travelled through a task payload, or None if it did not arrive.

        ABSENCE OF A RULE IS NOT PERMISSION.  This used to coerce a missing or malformed payload
        into an EMPTY policy, which admits everything -- so an older queued job, a truncated task
        or any deserialization failure silently reverted Differ to listing `.cache` and
        `node_modules`.  Returning ``None`` makes "no policy arrived" a distinct answer the caller
        must resolve, instead of a permissive one it cannot notice.

        A policy that legitimately excludes nothing is still a policy: it carries both keys as
        lists, so an empty configuration stays distinguishable from an absent one.
        """

        if not isinstance(payload, Mapping):
            return None
        names = _canonical_text_tuple(payload.get("skip_dir_names"))
        rules = _canonical_text_tuple(payload.get("exclude_rules"))
        if names is None or rules is None:
            return None
        return cls(skip_dir_names=names, exclude_rules=rules)

    def as_payload(self) -> dict[str, list[str]]:
        return {"skip_dir_names": list(self.skip_dir_names), "exclude_rules": list(self.exclude_rules)}

    def without_directory_names(self, names: Iterable[str]) -> "ExclusionPolicy":
        """Return this policy minus specific directory names, for a documented consumer exception."""

        removed = {str(name) for name in names}
        return ExclusionPolicy(
            skip_dir_names=tuple(name for name in self.skip_dir_names if name not in removed),
            exclude_rules=self.exclude_rules,
        )

    @property
    def signature(self) -> str:
        """A stable digest of the policy. Two policies differ here iff they can judge differently."""

        return hashlib.sha256(
            json.dumps(self.as_payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]

    def compiled_for(self, root: Path) -> CompiledExclusionPolicy:
        compiled = [
            rule for raw_rule in self.exclude_rules
            if (rule := _exclude_rule(raw_rule, root)) is not None
        ]
        compiled.sort(key=lambda rule: (rule[0], rule[1]))
        return CompiledExclusionPolicy(
            skip_dirs=frozenset(self.skip_dir_names),
            rules=tuple(compiled),
            root=root,
        )


def default_exclusion_policy(names: Sequence[str], rules: Sequence[str]) -> ExclusionPolicy:
    """The policy to judge by when no configuration was supplied. Not a second policy source."""

    return ExclusionPolicy.from_settings(
        {"index_exclude_dir_names": list(names), "index_exclude_paths": list(rules)},
        names,
    )


def _exclude_rule(raw_rule: str, root: Path) -> tuple[str, str, "Path | re.Pattern[str]"] | None:
    value = str(raw_rule or "").strip()
    if not value:
        return None
    if value.startswith(INDEX_EXCLUDE_GLOB_PREFIX):
        pattern = value.removeprefix(INDEX_EXCLUDE_GLOB_PREFIX).strip().replace("\\", "/").lstrip("/")
        return ("glob", pattern, Path(".")) if pattern else None
    if value.startswith(INDEX_EXCLUDE_REGEX_PREFIX):
        pattern = value.removeprefix(INDEX_EXCLUDE_REGEX_PREFIX).strip()
        if not pattern:
            return None
        try:
            return "regex", pattern, re.compile(pattern)
        except re.error:
            return None
    candidate = Path(value).expanduser().resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return "path", str(candidate), candidate


def _exclude_rule_matches(rule: tuple[str, str, "Path | re.Pattern[str]"], path: Path, root: Path) -> bool:
    kind, _value, matcher = rule
    try:
        relative_path = path.expanduser().resolve(strict=False).relative_to(root).as_posix()
    except ValueError:
        return False
    if kind == "path":
        assert isinstance(matcher, Path)
        try:
            path.expanduser().resolve(strict=False).relative_to(matcher)
            return True
        except ValueError:
            return False
    if kind == "glob":
        pattern = _value
        # Try the directory form too: a familiar rule such as `glob:**/.uploads/**`
        # must prune `.uploads` itself, not merely reject files after walking it.
        candidates = (relative_path, f"_/{relative_path}", f"{relative_path}/", f"_/{relative_path}/")
        return any(fnmatchcase(candidate, pattern) for candidate in candidates)
    assert isinstance(matcher, re.Pattern)
    return matcher.search(relative_path) is not None


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
