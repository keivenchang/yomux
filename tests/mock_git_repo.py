# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Fixture-owned Git repositories with realistic Differ working-tree states."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class GitRepositoryFixture:
    root: Path

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            capture_output=True,
            text=True,
        )


@dataclass(frozen=True)
class MockGitRepository(GitRepositoryFixture):
    modified: Path
    untracked: Path
    deleted: Path
    renamed_from: Path
    renamed_to: Path
    large_diff: Path

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


@dataclass(frozen=True)
class GitHistoryRepository(GitRepositoryFixture):
    scope: Path
    root_sha: str
    outside_sha: str
    changes_sha: str
    feature_sha: str
    main_sha: str
    merge_sha: str
    deleted: Path
    renamed_from: Path
    renamed_to: Path
    copy_source: Path
    copy_target: Path
    binary: Path
    mode_only: Path
    hostile: Path


def create_git_history_repository(root: Path) -> GitHistoryRepository:
    """Create root, scoped, merge, binary, rename, copy, mode, and hostile-name history."""
    root.mkdir(parents=True)
    fixture = GitRepositoryFixture(root=root)
    fixture.git("init", "-q")
    fixture.git("config", "user.name", "History Fixture")
    fixture.git("config", "user.email", "history@example.invalid")
    scope = root / "scope"
    scope.mkdir()
    deleted = scope / "deleted.txt"
    renamed_from = scope / "renamed-before.txt"
    renamed_to = scope / "renamed-after.txt"
    copy_source = scope / "copy-source.txt"
    copy_target = scope / "copy-target.txt"
    binary = scope / "binary.dat"
    mode_only = scope / "mode-only.sh"
    hostile = scope / "tab\tline\nユニコード.txt"
    (root / "root.txt").write_text("root\n", encoding="utf-8")
    (scope / "kept.txt").write_text("before\n", encoding="utf-8")
    deleted.write_text("delete me\n", encoding="utf-8")
    renamed_from.write_text("rename me\n", encoding="utf-8")
    copy_source.write_text("copy me\n", encoding="utf-8")
    binary.write_bytes(b"\x00before\n")
    mode_only.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fixture.git("add", "--", ".")
    fixture.git("commit", "-q", "-m", "root commit")
    root_sha = fixture.git("rev-parse", "HEAD").stdout.strip()

    (root / "outside.txt").write_text("outside scope\n", encoding="utf-8")
    fixture.git("add", "--", "outside.txt")
    fixture.git("commit", "-q", "-m", "outside scope")
    outside_sha = fixture.git("rev-parse", "HEAD").stdout.strip()

    (scope / "kept.txt").write_text("after\nextra\n", encoding="utf-8")
    deleted.unlink()
    fixture.git("mv", "--", str(renamed_from.relative_to(root)), str(renamed_to.relative_to(root)))
    copy_target.write_text(copy_source.read_text(encoding="utf-8"), encoding="utf-8")
    binary.write_bytes(b"\x00after\n")
    mode_only.chmod(0o755)
    hostile.write_text("hostile path\n", encoding="utf-8")
    fixture.git("add", "--", ".")
    fixture.git("commit", "-q", "-m", "scoped history changes", "-m", "Preserve every path and count.")
    changes_sha = fixture.git("rev-parse", "HEAD").stdout.strip()

    main_branch = fixture.git("branch", "--show-current").stdout.strip()
    fixture.git("checkout", "-q", "-b", "history-feature")
    (scope / "feature.txt").write_text("feature\n", encoding="utf-8")
    fixture.git("add", "--", "scope/feature.txt")
    fixture.git("commit", "-q", "-m", "feature side")
    feature_sha = fixture.git("rev-parse", "HEAD").stdout.strip()

    fixture.git("checkout", "-q", main_branch)
    (scope / "main.txt").write_text("main\n", encoding="utf-8")
    fixture.git("add", "--", "scope/main.txt")
    fixture.git("commit", "-q", "-m", "main side")
    main_sha = fixture.git("rev-parse", "HEAD").stdout.strip()
    fixture.git("merge", "-q", "--no-ff", "history-feature", "-m", "merge feature")
    merge_sha = fixture.git("rev-parse", "HEAD").stdout.strip()

    return GitHistoryRepository(
        root=root,
        scope=scope,
        root_sha=root_sha,
        outside_sha=outside_sha,
        changes_sha=changes_sha,
        feature_sha=feature_sha,
        main_sha=main_sha,
        merge_sha=merge_sha,
        deleted=deleted,
        renamed_from=renamed_from,
        renamed_to=renamed_to,
        copy_source=copy_source,
        copy_target=copy_target,
        binary=binary,
        mode_only=mode_only,
        hostile=hostile,
    )
