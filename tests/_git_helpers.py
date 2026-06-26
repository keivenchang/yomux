"""Shared git scaffolding for the suite's git-backed tests.

The git tests (test_filesystem, test_session_files, test_metadata, test_activity_summary) each used to
carry their own `git()` / `_git()` runner or open-coded `subprocess.run(["git", ...], cwd=...)` blocks,
including the same three-line `init + config user.email/user.name` identity dance. This module owns the
one runner (`-C`-based so it needs no cwd) and the identity-init helper so the per-test setup is just the
state that test actually needs (which files, branches, commits) instead of boilerplate.

Not collected by pytest (no `test_` prefix); imported by sibling modules under the default prepend
import mode, where the tests dir is on sys.path.
"""

import subprocess


def git(repo, *args):
    """Run `git -C <repo> <args>`, raising on failure; returns the CompletedProcess (for `.stdout`)."""
    return subprocess.run(
        ["git", "-c", "init.defaultBranch=master", "-C", str(repo), *args],
        capture_output=True,
        check=True,
        text=True,
    )


def init_repo(repo):
    """`git init` + deterministic test identity — the block that was copy-pasted across the git tests."""
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
