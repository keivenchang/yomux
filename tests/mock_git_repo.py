# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Fixture-owned Git repositories with realistic Differ working-tree states."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class MockGitRepository:
    root: Path
    modified: Path
    untracked: Path
    deleted: Path
    renamed_from: Path
    renamed_to: Path
    large_diff: Path

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def status_lines(self) -> tuple[str, ...]:
        return tuple(self.git("status", "--short").stdout.splitlines())

    def differ_files(self, session: str) -> list[dict[str, object]]:
        def row(path: Path, status: str, **extra: object) -> dict[str, object]:
            stat = path.stat() if path.exists() else None
            return {
                "session": session,
                "agent": "codex",
                "status": status,
                "repo": str(self.root),
                "path": path.relative_to(self.root).as_posix(),
                "abs_path": str(path),
                "mtime": stat.st_mtime if stat else 0,
                "size": stat.st_size if stat else 0,
                "added": 1,
                "removed": 1,
                **extra,
            }

        return [
            row(self.modified, "M"),
            row(self.untracked, "?"),
            row(self.deleted, "D"),
            row(self.renamed_to, "R", old_path=self.renamed_from.relative_to(self.root).as_posix()),
            row(self.large_diff, "M", added=800, removed=600),
        ]


def create_mock_git_repository(root: Path) -> MockGitRepository:
    """Create two commits plus modified, untracked, deleted, renamed, and large-diff files."""
    root.mkdir(parents=True)
    repo = MockGitRepository(
        root=root,
        modified=root / "modified.txt",
        untracked=root / "untracked.txt",
        deleted=root / "deleted.txt",
        renamed_from=root / "renamed-before.txt",
        renamed_to=root / "renamed-after.txt",
        large_diff=root / "large-diff.txt",
    )
    repo.git("init", "-q")
    repo.git("config", "user.name", "Gate Fixture")
    repo.git("config", "user.email", "gate@example.invalid")
    (root / "README.md").write_text("mock Differ repository\n", encoding="utf-8")
    repo.git("add", "README.md")
    repo.git("commit", "-q", "-m", "fixture root")

    repo.modified.write_text("committed modified file\n", encoding="utf-8")
    repo.deleted.write_text("committed deleted file\n", encoding="utf-8")
    repo.renamed_from.write_text("committed renamed file\n", encoding="utf-8")
    repo.large_diff.write_text(
        "".join(f"before {index:04d} {'x' * 96}\n" for index in range(600)),
        encoding="utf-8",
    )
    repo.git("add", "modified.txt", "deleted.txt", "renamed-before.txt", "large-diff.txt")
    repo.git("commit", "-q", "-m", "fixture tracked files")

    repo.modified.write_text("working modified file\n", encoding="utf-8")
    repo.untracked.write_text("working untracked file\n", encoding="utf-8")
    repo.deleted.unlink()
    repo.git("mv", "renamed-before.txt", "renamed-after.txt")
    repo.large_diff.write_text(
        "".join(f"after {index:04d} {'y' * 96}\n" for index in range(800)),
        encoding="utf-8",
    )

    statuses = repo.status_lines()
    assert any(line.startswith(" M modified.txt") for line in statuses), statuses
    assert any(line.startswith("?? untracked.txt") for line in statuses), statuses
    assert any(line.startswith(" D deleted.txt") for line in statuses), statuses
    assert any(line.startswith("R  renamed-before.txt -> renamed-after.txt") for line in statuses), statuses
    assert any(line.startswith(" M large-diff.txt") for line in statuses), statuses
    assert int(repo.git("rev-list", "--count", "HEAD").stdout.strip()) == 2
    return repo
