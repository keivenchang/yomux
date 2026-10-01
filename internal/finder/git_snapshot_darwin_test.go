// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build darwin

package finder

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestDarwinGitSnapshotSharesCloneConcurrencyAcrossNestedDirectories(t *testing.T) {
	root := t.TempDir()
	sourcePath := filepath.Join(root, "source")
	destinationPath := filepath.Join(root, "destination")
	if err := os.Mkdir(sourcePath, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(destinationPath, 0o700); err != nil {
		t.Fatal(err)
	}
	directory := sourcePath
	for depth := 0; depth < 5; depth++ {
		for index := 0; index < darwinGitSnapshotCloneWorkers-1; index++ {
			name := filepath.Join(directory, fmt.Sprintf("a-%02d", index))
			if err := os.WriteFile(name, []byte("snapshot data"), 0o600); err != nil {
				t.Fatal(err)
			}
		}
		if depth < 4 {
			child := filepath.Join(directory, "z-child")
			if err := os.Mkdir(child, 0o700); err != nil {
				t.Fatal(err)
			}
			directory = child
		}
	}
	source, err := os.Open(sourcePath)
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close()
	destination, err := os.Open(destinationPath)
	if err != nil {
		t.Fatal(err)
	}
	defer destination.Close()
	budget := newDarwinSnapshotBudget()
	budget.beforeClone = func() { time.Sleep(5 * time.Millisecond) }
	if err := cloneDarwinTree(context.Background(), source, destination, budget, 0, darwinSnapshotTreeOptions{}); err != nil {
		t.Fatalf("clone nested snapshot: %v", err)
	}
	if budget.maxLive < 2 || budget.maxLive > darwinGitSnapshotCloneWorkers {
		t.Fatalf("maximum simultaneous file clones = %d, want 2..%d", budget.maxLive, darwinGitSnapshotCloneWorkers)
	}
}

func TestDarwinGitSnapshotBudgetCountsAPFSCloneLogicalSize(t *testing.T) {
	root := t.TempDir()
	sourcePath := filepath.Join(root, "source")
	destinationPath := filepath.Join(root, "destination")
	if err := os.Mkdir(sourcePath, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(destinationPath, 0o700); err != nil {
		t.Fatal(err)
	}
	largeFile := filepath.Join(sourcePath, "large-sparse-file")
	file, err := os.Create(largeFile)
	if err != nil {
		t.Fatalf("create sparse budget fixture: %v", err)
	}
	if err := file.Truncate(darwinGitSnapshotMaxBytes + 1); err != nil {
		_ = file.Close()
		t.Fatalf("create sparse budget fixture: %v", err)
	}
	if err := file.Close(); err != nil {
		t.Fatalf("close sparse budget fixture: %v", err)
	}
	source, err := os.Open(sourcePath)
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close()
	destination, err := os.Open(destinationPath)
	if err != nil {
		t.Fatal(err)
	}
	defer destination.Close()
	budget := newDarwinSnapshotBudget()
	err = cloneDarwinTree(context.Background(), source, destination, budget, 0, darwinSnapshotTreeOptions{})
	if err == nil {
		if _, statErr := os.Stat(filepath.Join(destinationPath, "large-sparse-file")); statErr != nil {
			t.Fatalf("cloned large destination stat error = %v", statErr)
		}
		if budget.bytes != 0 {
			t.Fatalf("APFS clone charged %d fallback bytes, want 0", budget.bytes)
		}
	} else if !strings.Contains(err.Error(), "byte limit exceeded") {
		t.Fatalf("snapshot error = %v, want APFS clone or bounded fallback rejection", err)
	} else if _, statErr := os.Stat(filepath.Join(destinationPath, "large-sparse-file")); !errors.Is(statErr, os.ErrNotExist) {
		t.Fatalf("rejected fallback destination stat error = %v, want file absent", statErr)
	}
}

type cancelAfterContextChecks struct {
	context.Context
	cancel   context.CancelFunc
	cancelAt int
	checks   int
}

func (ctx *cancelAfterContextChecks) Err() error {
	ctx.checks++
	if ctx.checks == ctx.cancelAt {
		ctx.cancel()
	}
	return ctx.Context.Err()
}

func TestDarwinGitMetadataSnapshotSkipsWorktreeFiles(t *testing.T) {
	root, head := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	scope, finderErr := service.openGitHistoryScopeWithOptions(context.Background(), root, gitHistoryScopeOptions{})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	defer scope.close()
	if err := scope.namespace.prepareSnapshot(context.Background(), scope.directory); err != nil {
		t.Fatalf("prepare metadata snapshot: %v", err)
	}
	entries, err := os.ReadDir(scope.namespace.snapshotWork)
	if err != nil {
		t.Fatalf("read metadata snapshot worktree: %v", err)
	}
	if len(entries) != 0 {
		t.Fatalf("metadata snapshot copied %d worktree entries, want none", len(entries))
	}
	for _, name := range []string{"hooks", "logs"} {
		if _, err := os.Lstat(filepath.Join(scope.namespace.snapshotCommon, name)); !errors.Is(err, os.ErrNotExist) {
			t.Fatalf("metadata snapshot entry %q stat error = %v, want excluded", name, err)
		}
	}
	if info, err := os.Stat(filepath.Join(scope.namespace.snapshotCommon, "objects")); err != nil || !info.IsDir() {
		t.Fatalf("metadata snapshot object copy = %v, error=%v, want pinned object directory", info, err)
	}
	output, _, err := runGitMetadataBounded(context.Background(), scope.directory, nil, []string{"log", "-1", "--format=%H"}, 4096, scope.namespace)
	if err != nil || strings.TrimSpace(string(output)) != head {
		t.Fatalf("metadata snapshot Git history = %q, error=%v, want %q", strings.TrimSpace(string(output)), err, head)
	}
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("worktree-change\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	status, _, err := runGitBounded(context.Background(), scope.directory, nil, []string{"status", "--porcelain=v1", "-z", "--untracked-files=all"}, 4096, scope.namespace)
	if err != nil || !strings.Contains(string(status), "note.txt") {
		t.Fatalf("Git status from pinned live worktree = %q, error=%v, want modified note.txt", status, err)
	}
	entries, err = os.ReadDir(scope.namespace.snapshotWork)
	if err != nil || len(entries) != 0 {
		t.Fatalf("Git status snapshot entries = %v, error=%v; status must leave the worktree uncopied", entries, err)
	}
}

func TestDarwinCurrentBinaryDiffUsesSnapshotWorktree(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	path := filepath.Join(root, "binary.dat")
	if err := os.WriteFile(path, []byte("before\x00"), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "add", "binary.dat").CombinedOutput(); err != nil {
		t.Fatalf("git add: %v (%s)", err, output)
	}
	if output, err := exec.Command("git", "-C", root, "commit", "-qm", "binary baseline").CombinedOutput(); err != nil {
		t.Fatalf("git commit: %v (%s)", err, output)
	}
	if err := os.WriteFile(path, []byte("after\x00"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	diff, finderErr := service.Diff(context.Background(), path, "HEAD", "current")
	if finderErr != nil || !strings.Contains(diff["diff"].(string), "Binary files a/binary.dat and b/binary.dat differ") {
		t.Fatalf("current binary diff = %#v, error = %v", diff, finderErr)
	}
}

func TestDarwinGitUpstreamConfigQuotesValues(t *testing.T) {
	var config strings.Builder
	appendDarwinGitUpstreamConfig(&config,
		map[string]map[string][]string{"topic": {"remote": {"origin#backup"}, "merge": {"refs/heads/topic#1"}}},
		map[string][]string{"origin#backup": {"+refs/heads/*:refs/remotes/origin/*"}},
	)
	path := filepath.Join(t.TempDir(), "config")
	if err := os.WriteFile(path, []byte(config.String()), 0o600); err != nil {
		t.Fatal(err)
	}
	for key, want := range map[string]string{
		"branch.topic.remote":        "origin#backup",
		"branch.topic.merge":         "refs/heads/topic#1",
		"remote.origin#backup.fetch": "+refs/heads/*:refs/remotes/origin/*",
	} {
		output, err := exec.Command("git", "config", "--file", path, "--get", key).Output()
		if err != nil || strings.TrimSpace(string(output)) != want {
			t.Errorf("git config --get %s = %q, error = %v; want %q", key, output, err, want)
		}
	}
}

func TestDarwinLinkedGitSnapshotPreservesHEADAndWorktreeIndex(t *testing.T) {
	root, head := newCommittedTestRepo(t)
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("second commit\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "note.txt"}, {"-C", root, "commit", "-qm", "second"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("create second commit: git %v: %v (%s)", args, err, output)
		}
	}
	headOutput, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	head = strings.TrimSpace(string(headOutput))
	linked := filepath.Join(t.TempDir(), "linked")
	if output, err := exec.Command("git", "-C", root, "worktree", "add", "-q", "-b", "linked-snapshot", linked).CombinedOutput(); err != nil {
		t.Fatalf("create linked worktree: %v (%s)", err, output)
	}
	if err := os.WriteFile(filepath.Join(linked, "note.txt"), []byte("changed\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(linked, "new.txt"), []byte("untracked\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(linked, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	scope, finderErr := service.openGitHistoryScopeWithOptions(context.Background(), linked, gitHistoryScopeOptions{})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	defer scope.close()
	if err := scope.namespace.prepareSnapshot(context.Background(), scope.directory); err != nil {
		t.Fatalf("prepare linked-worktree metadata snapshot: %v", err)
	}
	for _, name := range []string{"worktrees", "index"} {
		if _, err := os.Lstat(filepath.Join(scope.namespace.snapshotCommon, name)); !errors.Is(err, os.ErrNotExist) {
			t.Fatalf("shared snapshot entry %q stat error = %v, want excluded", name, err)
		}
	}
	if _, err := os.Stat(filepath.Join(scope.namespace.snapshotGit, "index")); err != nil {
		t.Fatalf("active worktree index missing from metadata snapshot: %v", err)
	}
	metadata, finderErr := service.GitHistoryMetadata(context.Background(), linked, "1", "", -1)
	if finderErr != nil {
		t.Fatalf("read linked-worktree history: %v", finderErr)
	}
	if metadata["head"] != head {
		t.Fatalf("linked-worktree history HEAD = %v, want %s", metadata["head"], head)
	}
	cursor, _ := metadata["snapshot_cursor"].(string)
	if cursor == "" {
		t.Fatal("linked-worktree history did not return a snapshot cursor")
	}
	page, finderErr := service.GitHistoryPageAtOffset(context.Background(), linked, "1", cursor, 1)
	if finderErr != nil {
		t.Fatalf("read linked-worktree history page: %v", finderErr)
	}
	pageCommits, _ := page["commits"].([]map[string]any)
	if len(pageCommits) != 1 {
		t.Fatalf("linked-worktree history page commits = %d, want 1", len(pageCommits))
	}
	commitSHA, _ := pageCommits[0]["sha"].(string)
	if _, finderErr := service.GitCommit(context.Background(), linked, commitSHA, head); finderErr != nil {
		t.Fatalf("read linked-worktree commit detail: %v", finderErr)
	}
	statsEmitted := false
	if finderErr := service.GitHistoryStatsStream(context.Background(), linked, cursor, 2, 1, func(map[string]any) error {
		statsEmitted = true
		return nil
	}); finderErr != nil {
		t.Fatalf("stream linked-worktree Git history stats: %v", finderErr)
	}
	if !statsEmitted {
		t.Fatal("linked-worktree Git history stats stream emitted no batches")
	}
	statusPayload, finderErr := service.GitHistoryWorktree(context.Background(), linked)
	if finderErr != nil {
		t.Fatalf("read linked-worktree status: %v", finderErr)
	}
	status, ok := statusPayload["worktree_status"].(map[string]any)
	if !ok || status["available"] != true || status["dirty"] != true {
		t.Fatalf("linked-worktree status = %#v, want available and dirty", statusPayload["worktree_status"])
	}
	entries, ok := status["entries"].([]map[string]any)
	if !ok || len(entries) != 2 {
		t.Fatalf("linked-worktree status entries = %#v, want modified and untracked files", status["entries"])
	}
	statuses := map[string]string{}
	for _, entry := range entries {
		path, _ := entry["path"].(string)
		statusCode, _ := entry["status"].(string)
		statuses[path] = statusCode
	}
	if statuses["note.txt"] != "M" || statuses["new.txt"] != "??" {
		t.Fatalf("linked-worktree status paths = %#v, want note.txt modified and new.txt untracked", statuses)
	}
	diff, finderErr := service.Diff(context.Background(), filepath.Join(linked, "note.txt"), "HEAD", "current")
	if finderErr != nil {
		t.Fatalf("read linked-worktree file diff: %v", finderErr)
	}
	if diff["working"] != "changed\n" || !strings.Contains(diff["diff"].(string), "+changed") {
		t.Fatalf("linked-worktree file diff = %#v, want live working contents", diff)
	}
	blame, finderErr := service.Blame(context.Background(), filepath.Join(linked, "note.txt"), "")
	if finderErr != nil {
		t.Fatalf("read linked-worktree file blame: %v", finderErr)
	}
	if blame["in_repo"] != true || len(blame["lines"].(map[string]map[string]any)) != 1 {
		t.Fatalf("linked-worktree file blame = %#v, want one attributed line", blame)
	}
}

func TestDarwinGitNamespaceHonorsCancelledContext(t *testing.T) {
	root := t.TempDir()
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	directory, err := os.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, finderErr := service.openGitNamespaceContext(ctx, directory, root, root); finderErr == nil || finderErr.Code != "cancelled" {
		t.Fatalf("cancelled Git namespace discovery error = %v, want cancelled", finderErr)
	}
	if err := service.gitObjectLayouts.validateContext(ctx, root); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled Git object validation error = %v, want context canceled", err)
	}

	walkContext, cancelWalk := context.WithCancel(context.Background())
	defer cancelWalk()
	walk := &cancelAfterContextChecks{Context: walkContext, cancel: cancelWalk, cancelAt: 3}
	if _, finderErr := service.openGitNamespaceContext(walk, directory, root, root); finderErr == nil || finderErr.Code != "cancelled" {
		t.Fatalf("mid-walk Git namespace discovery error = %v, want cancelled", finderErr)
	}

	objects := filepath.Join(root, "objects")
	if err := os.Mkdir(objects, 0o700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"one", "two"} {
		if err := os.WriteFile(filepath.Join(objects, name), nil, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	validationContext, cancelValidation := context.WithCancel(context.Background())
	defer cancelValidation()
	validation := &cancelAfterContextChecks{Context: validationContext, cancel: cancelValidation, cancelAt: 3}
	if err := (&gitObjectLayoutCache{}).validateContext(validation, objects); !errors.Is(err, context.Canceled) {
		t.Fatalf("mid-scan Git object validation error = %v, want context canceled", err)
	}
}

func TestDarwinGitReadsPinnedSnapshotWhenLooseRefIsReplacedAndRestored(t *testing.T) {
	root := t.TempDir()
	for _, args := range [][]string{
		{"init", "-q", "--initial-branch=master", root},
		{"-C", root, "config", "user.email", "test@example.invalid"},
		{"-C", root, "config", "user.name", "Finder Test"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	note := filepath.Join(root, "note.txt")
	if err := os.WriteFile(note, []byte("master\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "note.txt"}, {"-C", root, "commit", "-qm", "master"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	masterSHA, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "checkout", "-qb", "replacement").CombinedOutput(); err != nil {
		t.Fatalf("create replacement branch: %v (%s)", err, output)
	}
	if err := os.WriteFile(note, []byte("replacement\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "note.txt"}, {"-C", root, "commit", "-qm", "replacement"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	replacementSHA, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "checkout", "-q", "master").CombinedOutput(); err != nil {
		t.Fatalf("restore master branch: %v (%s)", err, output)
	}
	realGit, err := exec.LookPath("git")
	if err != nil {
		t.Fatal(err)
	}
	ref := filepath.Join(root, ".git", "refs", "heads", "master")
	backup := ref + ".snapshot-test-backup"
	bin := t.TempDir()
	wrapper := filepath.Join(bin, "git")
	script := "#!/bin/sh\nset -u\nmv \"$YOLO_REF\" \"$YOLO_BACKUP\"\nprintf '%s\\n' \"$YOLO_REPLACEMENT\" > \"$YOLO_REF\"\nset +e\n\"$YOLO_REAL_GIT\" \"$@\"\nstatus=$?\nrm -f \"$YOLO_REF\"\nmv \"$YOLO_BACKUP\" \"$YOLO_REF\"\nexit \"$status\"\n"
	if err := os.WriteFile(wrapper, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("YOLO_REF", ref)
	t.Setenv("YOLO_BACKUP", backup)
	t.Setenv("YOLO_REPLACEMENT", strings.TrimSpace(string(replacementSHA)))
	t.Setenv("YOLO_REAL_GIT", realGit)

	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	directory, err := os.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	namespace, finderErr := service.openGitNamespace(directory, root, root)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	defer namespace.close()
	output, _, err := runGitBounded(context.Background(), directory, nil, []string{"rev-parse", "HEAD"}, 4096, namespace)
	if err != nil || strings.TrimSpace(string(output)) != strings.TrimSpace(string(masterSHA)) {
		t.Fatalf("pinned Git HEAD = %q, error=%v, want %q", strings.TrimSpace(string(output)), err, strings.TrimSpace(string(masterSHA)))
	}
	refData, err := os.ReadFile(ref)
	if err != nil || strings.TrimSpace(string(refData)) != strings.TrimSpace(string(masterSHA)) {
		t.Fatalf("source ref after swap-and-restore = %q, error=%v", strings.TrimSpace(string(refData)), err)
	}
}

func TestDarwinGitDiffDoesNotFollowSwappedWorktreePath(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	tracked := filepath.Join(root, "note.txt")
	if err := os.WriteFile(tracked, []byte("allowed-change\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	outside := t.TempDir()
	if err := os.WriteFile(filepath.Join(outside, "note.txt"), []byte("outside-only-secret\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	backup := root + ".snapshot-test-backup"
	realGit, err := exec.LookPath("git")
	if err != nil {
		t.Fatal(err)
	}
	bin := t.TempDir()
	wrapper := filepath.Join(bin, "git")
	script := "#!/bin/sh\nset -u\ncase \" $* \" in *\" diff \"*|*\" status \"*) ;; *) exec \"$YOLO_REAL_GIT\" \"$@\" ;; esac\nmv \"$YOLO_TREE\" \"$YOLO_TREE_BACKUP\" || exit 71\nln -s \"$YOLO_OUTSIDE\" \"$YOLO_TREE\" || { mv \"$YOLO_TREE_BACKUP\" \"$YOLO_TREE\"; exit 72; }\nln -s \"$YOLO_TREE_BACKUP/.git\" \"$YOLO_OUTSIDE/.git\" || exit 73\n\"$YOLO_REAL_GIT\" \"$@\"\nrm \"$YOLO_TREE\"\nmv \"$YOLO_TREE_BACKUP\" \"$YOLO_TREE\"\nexit 0\n"
	if err := os.WriteFile(wrapper, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("YOLO_TREE", root)
	t.Setenv("YOLO_TREE_BACKUP", backup)
	t.Setenv("YOLO_OUTSIDE", outside)
	t.Setenv("YOLO_REAL_GIT", realGit)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	directory, err := os.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	namespace, finderErr := service.openGitNamespace(directory, root, root)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	defer namespace.close()
	output, stderr, err := runGitBounded(context.Background(), directory, nil, []string{"diff", "--no-ext-diff", "--", "note.txt"}, 4096, namespace)
	if err != nil {
		t.Fatalf("Git diff failed during worktree path swap: %v (%s)", err, stderr)
	}
	if strings.Contains(string(output), "outside-only-secret") || !strings.Contains(string(output), "+allowed-change") {
		t.Fatalf("Git diff = %q, stderr=%q; it must read the pinned worktree snapshot and exclude the swapped outside tree", output, stderr)
	}
	if err := os.WriteFile(filepath.Join(outside, "outside-only-untracked.txt"), []byte("outside tree\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	status, stderr, err := runGitBounded(context.Background(), directory, nil, []string{"status", "--porcelain=v1", "-z", "--untracked-files=all"}, 4096, namespace)
	if err != nil {
		t.Fatalf("Git status failed during worktree path swap: %v (%s)", err, stderr)
	}
	if strings.Contains(string(status), "outside-only-untracked.txt") || !strings.Contains(string(status), "note.txt") {
		t.Fatalf("Git status = %q; it must inspect the pinned worktree and exclude the swapped outside tree", status)
	}
}

func TestDarwinGitSnapshotIgnoresExternalIncludesAndHelpers(t *testing.T) {
	root := t.TempDir()
	for _, args := range [][]string{
		{"init", "-q", "--initial-branch=master", root},
		{"-C", root, "config", "user.email", "test@example.invalid"},
		{"-C", root, "config", "user.name", "Finder Test"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("one\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "note.txt"}, {"-C", root, "commit", "-qm", "initial"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	external := t.TempDir()
	helperMarker := filepath.Join(external, "helper-ran")
	helper := filepath.Join(external, "fsmonitor")
	if err := os.WriteFile(helper, []byte("#!/bin/sh\ntouch \"$YOLO_HELPER_MARKER\"\nprintf '0 0\\n'\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(external, "included-config"), []byte("[core]\nfsmonitor = "+helper+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	config := filepath.Join(root, ".git", "config")
	file, err := os.OpenFile(config, os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := io.WriteString(file, "\n[include]\npath = "+filepath.Join(external, "included-config")+"\n"); err != nil {
		_ = file.Close()
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	t.Setenv("YOLO_HELPER_MARKER", helperMarker)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	directory, err := os.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	namespace, finderErr := service.openGitNamespace(directory, root, root)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	defer namespace.close()
	if _, _, err := runGitBounded(context.Background(), directory, nil, []string{"status", "--porcelain", "--untracked-files=no"}, 4096, namespace); err != nil {
		t.Fatalf("Git status with external include: %v", err)
	}
	if _, err := os.Lstat(helperMarker); !os.IsNotExist(err) {
		t.Fatalf("external fsmonitor helper marker = %v, want not-exist", err)
	}
}

func TestDarwinGitSnapshotPreservesCoreFileModeSetting(t *testing.T) {
	root := t.TempDir()
	for _, args := range [][]string{
		{"init", "-q", "--initial-branch=master", root},
		{"-C", root, "config", "user.email", "test@example.invalid"},
		{"-C", root, "config", "user.name", "Finder Test"},
		{"-C", root, "config", "core.filemode", "false"},
		{"-C", root, "config", "core.ignorecase", "yes"},
		{"-C", root, "config", "extensions.preciousObjects", "on"},
		{"-C", root, "config", "extensions.worktreeConfig", "yes"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	note := filepath.Join(root, "note.txt")
	if err := os.WriteFile(note, []byte("committed\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "note.txt"}, {"-C", root, "commit", "-qm", "initial"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.Chmod(note, 0o700); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	directory, err := os.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	namespace, finderErr := service.openGitNamespace(directory, root, root)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	defer namespace.close()
	output, _, err := runGitBounded(context.Background(), directory, nil, []string{"status", "--porcelain", "--untracked-files=no"}, 4096, namespace)
	if err != nil {
		t.Fatalf("Git status with core.filemode=false: %v", err)
	}
	if len(output) != 0 {
		t.Fatalf("Git status after executable-bit-only change = %q, want clean status", output)
	}
}

func TestNormalizeDarwinGitBoolean(t *testing.T) {
	tests := []struct {
		name     string
		value    string
		hasValue bool
		want     string
		valid    bool
	}{
		{name: "bare variable", hasValue: false, want: "true", valid: true},
		{name: "empty value", hasValue: true, want: "false", valid: true},
		{name: "true", value: "true", hasValue: true, want: "true", valid: true},
		{name: "yes case insensitive", value: "YES", hasValue: true, want: "true", valid: true},
		{name: "on", value: "on", hasValue: true, want: "true", valid: true},
		{name: "one", value: "1", hasValue: true, want: "true", valid: true},
		{name: "nonzero integer", value: "2", hasValue: true, want: "true", valid: true},
		{name: "negative integer", value: "-1", hasValue: true, want: "true", valid: true},
		{name: "false", value: "false", hasValue: true, want: "false", valid: true},
		{name: "no", value: "no", hasValue: true, want: "false", valid: true},
		{name: "off", value: "off", hasValue: true, want: "false", valid: true},
		{name: "zero", value: "0", hasValue: true, want: "false", valid: true},
		{name: "signed zero", value: "+0", hasValue: true, want: "false", valid: true},
		{name: "invalid", value: "sometimes", hasValue: true, valid: false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, valid := normalizeDarwinGitBoolean(tc.value, tc.hasValue)
			if got != tc.want || valid != tc.valid {
				t.Fatalf("normalizeDarwinGitBoolean(%q, %t) = %q, %t, want %q, %t", tc.value, tc.hasValue, got, valid, tc.want, tc.valid)
			}
		})
	}
}

func TestDarwinGitSnapshotPreservesWorktreeCoreFileMode(t *testing.T) {
	root := t.TempDir()
	for _, args := range [][]string{
		{"init", "-q", "--initial-branch=main", root},
		{"-C", root, "config", "user.email", "test@example.invalid"},
		{"-C", root, "config", "user.name", "Finder Test"},
		{"-C", root, "config", "extensions.worktreeConfig", "true"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	note := filepath.Join(root, "note.txt")
	if err := os.WriteFile(note, []byte("committed\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "note.txt"}, {"-C", root, "commit", "-qm", "initial"}, {"-C", root, "config", "--worktree", "core.filemode", "false"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.Chmod(note, 0o700); err != nil {
		t.Fatal(err)
	}
	native, err := exec.Command("git", "-C", root, "status", "--porcelain", "--untracked-files=no").Output()
	if err != nil || len(native) != 0 {
		t.Fatalf("native Git status = %q, error=%v, want clean", native, err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	status, finderErr := service.GitHistoryWorktree(context.Background(), root)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	worktreeStatus, ok := status["worktree_status"].(map[string]any)
	if !ok || worktreeStatus["dirty"] != false {
		t.Fatalf("YOmux Git status = %#v, want clean like native Git", status["worktree_status"])
	}
}

func TestDarwinGitSnapshotPreservesDefaultUpstreamSettings(t *testing.T) {
	root := t.TempDir()
	for _, args := range [][]string{
		{"init", "-q", "--initial-branch=main", root},
		{"-C", root, "config", "user.email", "test@example.invalid"},
		{"-C", root, "config", "user.name", "Finder Test"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("base\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "note.txt"}, {"-C", root, "commit", "-qm", "base"}, {"-C", root, "update-ref", "refs/remotes/origin/main", "HEAD"}, {"-C", root, "config", "remote.origin.url", "https://example.invalid/repo.git"}, {"-C", root, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"}, {"-C", root, "checkout", "-qb", "topic+fix/東京"}, {"-C", root, "config", "branch.topic+fix/東京.remote", "origin"}, {"-C", root, "config", "branch.topic+fix/東京.merge", "refs/heads/main"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "topic.txt"), []byte("topic\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "topic.txt"}, {"-C", root, "commit", "-qm", "topic"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	native, err := exec.Command("git", "-C", root, "rev-list", "--left-right", "--count", "@{u}...HEAD").Output()
	if err != nil || strings.Fields(string(native))[0] != "0" || strings.Fields(string(native))[1] != "1" {
		t.Fatalf("native upstream counts = %q, error=%v, want 0 behind and 1 ahead", native, err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	scope, finderErr := service.openGitHistoryScopeWithOptions(context.Background(), root, gitHistoryScopeOptions{})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	defer scope.close()
	counts := gitAheadBehind(context.Background(), scope, "", "", false)
	if counts["behind"] != 0 || counts["ahead"] != 1 {
		t.Fatalf("YOmux upstream counts = %#v, want 0 behind and 1 ahead", counts)
	}
}

func TestDarwinGitObjectReadDoesNotFollowSwappedObjectPath(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	outside, _ := newCommittedTestRepo(t)
	secret := []byte("outside object store content\n")
	if err := os.WriteFile(filepath.Join(outside, "outside.txt"), secret, 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", outside, "add", "outside.txt").CombinedOutput(); err != nil {
		t.Fatalf("stage external object: %v (%s)", err, output)
	}
	output, err := exec.Command("git", "-C", outside, "hash-object", "-w", "outside.txt").CombinedOutput()
	if err != nil {
		t.Fatalf("write external object: %v (%s)", err, output)
	}
	externalOID := strings.TrimSpace(string(output))
	objectsPath := filepath.Join(root, ".git", "objects")
	backupPath := objectsPath + ".snapshot-test-backup"
	realGit, err := exec.LookPath("git")
	if err != nil {
		t.Fatal(err)
	}
	bin := t.TempDir()
	wrapper := filepath.Join(bin, "git")
	markerPath := filepath.Join(t.TempDir(), "swap-occurred")
	script := "#!/bin/sh\nset -u\ncase \" $* \" in *\" cat-file -p $YOLO_EXTERNAL_OID \"*) /bin/mv \"$YOLO_OBJECTS\" \"$YOLO_OBJECTS_BACKUP\"; /bin/ln -s \"$YOLO_EXTERNAL_OBJECTS\" \"$YOLO_OBJECTS\"; : > \"$YOLO_SWAP_MARKER\";; esac\nset +e\n\"$YOLO_REAL_GIT\" \"$@\"\nstatus=$?\nif [ -L \"$YOLO_OBJECTS\" ]; then /bin/rm \"$YOLO_OBJECTS\"; /bin/mv \"$YOLO_OBJECTS_BACKUP\" \"$YOLO_OBJECTS\"; fi\nexit \"$status\"\n"
	if err := os.WriteFile(wrapper, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("YOLO_EXTERNAL_OID", externalOID)
	t.Setenv("YOLO_OBJECTS", objectsPath)
	t.Setenv("YOLO_OBJECTS_BACKUP", backupPath)
	t.Setenv("YOLO_EXTERNAL_OBJECTS", filepath.Join(outside, ".git", "objects"))
	t.Setenv("YOLO_SWAP_MARKER", markerPath)
	t.Setenv("YOLO_REAL_GIT", realGit)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	directory, err := os.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	defer directory.Close()
	namespace, finderErr := service.openGitNamespace(directory, root, root)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	defer namespace.close()
	output, _, err = runGitBounded(context.Background(), directory, nil, []string{"cat-file", "-p", externalOID}, 4096, namespace)
	if _, err := os.Stat(markerPath); err != nil {
		t.Fatalf("object-path swap wrapper did not run: %v", err)
	}
	if err == nil || strings.Contains(string(output), string(secret)) {
		t.Fatalf("read outside object = %q, error=%v; only pinned repository objects may be read", output, err)
	}
	if _, err := os.Stat(objectsPath); err != nil {
		t.Fatalf("original object directory was not restored: %v", err)
	}
}

func TestDarwinGitSnapshotPreservesGlobalIgnoreAndAttributeData(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	ignorePath := filepath.Join(root, ".git", "global-ignore")
	attributePath := filepath.Join(t.TempDir(), "global-attributes")
	if err := os.WriteFile(ignorePath, []byte("ignored.tmp\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(attributePath, []byte("*.txt binary\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "config", "core.excludesFile", ".git/global-ignore"}, {"-C", root, "config", "core.attributesFile", attributePath}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("modified\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "ignored.tmp"), []byte("ignored\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	nativeStatus, err := exec.Command("git", "-C", root, "status", "--porcelain", "--untracked-files=all").Output()
	if err != nil || strings.Contains(string(nativeStatus), "ignored.tmp") {
		t.Fatalf("native status = %q, error=%v, want external ignore applied", nativeStatus, err)
	}
	nativeNumstat, err := exec.Command("git", "-C", root, "diff", "--numstat", "HEAD").Output()
	if err != nil || !strings.Contains(string(nativeNumstat), "-\t-\tnote.txt") {
		t.Fatalf("native numstat = %q, error=%v, want external binary attribute applied", nativeNumstat, err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	status, finderErr := service.GitHistoryWorktree(context.Background(), root)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	worktreeStatus := status["worktree_status"].(map[string]any)
	entries := worktreeStatus["entries"].([]map[string]any)
	if len(entries) != 1 || entries[0]["path"] != "note.txt" || entries[0]["binary"] != true || entries[0]["counts_available"] != true {
		t.Fatalf("YOmux worktree status = %#v, want only binary note.txt", worktreeStatus)
	}
}

func TestDarwinGitSnapshotIgnoresMissingGlobalConfigData(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	for _, args := range [][]string{{"-C", root, "config", "core.excludesFile", ".git/missing-global-ignore"}, {"-C", root, "config", "core.attributesFile", ".git/missing-global-attributes"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("modified\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	native, err := exec.Command("git", "-C", root, "status", "--porcelain", "--untracked-files=all").Output()
	if err != nil || !strings.Contains(string(native), "note.txt") {
		t.Fatalf("native status = %q, error=%v, want status to succeed", native, err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	status, finderErr := service.GitHistoryWorktree(context.Background(), root)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	worktreeStatus := status["worktree_status"].(map[string]any)
	if worktreeStatus["available"] != true || worktreeStatus["dirty"] != true {
		t.Fatalf("YOmux status with missing external config files = %#v, want available and dirty", worktreeStatus)
	}
}

func TestDarwinGitSnapshotPreservesWorktreeUpstreamSettings(t *testing.T) {
	root := t.TempDir()
	for _, args := range [][]string{
		{"init", "-q", "--initial-branch=main", root},
		{"-C", root, "config", "user.email", "test@example.invalid"},
		{"-C", root, "config", "user.name", "Finder Test"},
		{"-C", root, "config", "extensions.worktreeConfig", "true"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "base.txt"), []byte("base\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "base.txt"}, {"-C", root, "commit", "-qm", "base"}, {"-C", root, "update-ref", "refs/remotes/origin/main", "HEAD"}, {"-C", root, "checkout", "-qb", "topic"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	for _, args := range [][]string{{"-C", root, "config", "--worktree", "branch.topic.remote", "origin"}, {"-C", root, "config", "--worktree", "branch.topic.merge", "refs/heads/main"}, {"-C", root, "config", "--worktree", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "topic.txt"), []byte("topic\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "topic.txt"}, {"-C", root, "commit", "-qm", "topic"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	native, err := exec.Command("git", "-C", root, "rev-list", "--left-right", "--count", "@{u}...HEAD").Output()
	if err != nil || strings.Fields(string(native))[0] != "0" || strings.Fields(string(native))[1] != "1" {
		t.Fatalf("native worktree upstream counts = %q, error=%v, want 0 behind and 1 ahead", native, err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	scope, finderErr := service.openGitHistoryScopeWithOptions(context.Background(), root, gitHistoryScopeOptions{})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	defer scope.close()
	counts := gitAheadBehind(context.Background(), scope, "", "", false)
	if counts["behind"] != 0 || counts["ahead"] != 1 {
		t.Fatalf("YOmux worktree upstream counts = %#v, want 0 behind and 1 ahead", counts)
	}
}
