// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
)

const gitIndexMaxBytes = 64 * 1024 * 1024

var errGitIndexChanged = fmt.Errorf("Git index changed during rename")

type gitIndexRenameRow struct {
	metadata string
	oldPath  string
	newPath  string
}

type gitRenamePlan struct {
	root          string
	oldPath       string
	rows          []gitIndexRenameRow
	indexPath     string
	indexIdentity descriptorIdentity
	indexDigest   [sha256.Size]byte
	flags         map[string][]string
	indexUpdated  bool
	indexOriginal []byte
	indexMode     os.FileMode
	lock          *os.File
}

func gitIndexFailure(format string, args ...any) error {
	return fmt.Errorf("%w: %s", errGitIndexChanged, fmt.Sprintf(format, args...))
}

func gitPath(root, value string) string {
	value = strings.TrimSpace(value)
	if filepath.IsAbs(value) {
		return filepath.Clean(value)
	}
	return filepath.Join(root, value)
}

func readGitIndex(path string) ([]byte, descriptorIdentity, os.FileMode, error) {
	file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, descriptorIdentity{}, 0, err
	}
	defer file.Close()
	before, err := file.Stat()
	if err != nil {
		return nil, descriptorIdentity{}, 0, err
	}
	if !before.Mode().IsRegular() || before.Size() > gitIndexMaxBytes {
		return nil, descriptorIdentity{}, 0, gitIndexFailure("Git index is not a bounded regular file")
	}
	data, err := io.ReadAll(io.LimitReader(file, gitIndexMaxBytes+1))
	if err != nil {
		return nil, descriptorIdentity{}, 0, err
	}
	after, err := file.Stat()
	if err != nil {
		return nil, descriptorIdentity{}, 0, err
	}
	named, err := os.Lstat(path)
	if err != nil {
		return nil, descriptorIdentity{}, 0, err
	}
	if len(data) > gitIndexMaxBytes || !os.SameFile(before, after) || !os.SameFile(after, named) || before.Size() != after.Size() || before.ModTime() != after.ModTime() {
		return nil, descriptorIdentity{}, 0, gitIndexFailure("Git index changed during snapshot")
	}
	return data, identityFor(after), after.Mode(), nil
}

func (plan *gitRenamePlan) acquireIndexLock() error {
	lock, err := os.OpenFile(plan.indexPath+".lock", os.O_RDWR|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return errors.Join(gitIndexFailure("Git index cannot be locked"), err)
	}
	plan.lock = lock
	return nil
}

func gitIndexLockOwned(lock *os.File) (bool, error) {
	opened, err := lock.Stat()
	if err != nil {
		return false, err
	}
	named, err := os.Lstat(lock.Name())
	if err != nil {
		return false, err
	}
	return os.SameFile(opened, named), nil
}

func (plan *gitRenamePlan) close() {
	if plan == nil || plan.lock == nil {
		return
	}
	lock := plan.lock
	plan.lock = nil
	if owned, err := gitIndexLockOwned(lock); err != nil && !os.IsNotExist(err) {
		slog.Error("inspect Git rename lock before release", "error", err)
	} else if owned {
		if err := os.Remove(lock.Name()); err != nil {
			slog.Error("release Git rename lock", "error", err)
		}
	}
	if err := lock.Close(); err != nil {
		slog.Error("close Git rename lock", "error", err)
	}
}

func gitIndexFlags(ctx context.Context, root, oldRelative string) (map[string][]string, error) {
	flags := map[string][]string{}
	for _, spec := range []struct {
		option string
		match  byte
	}{
		{option: "-v", match: 'h'},
		{option: "-t", match: 'S'},
		{option: "-f", match: 'h'},
	} {
		command := exec.CommandContext(ctx, "git", "--literal-pathspecs", "-C", root, "ls-files", spec.option, "-z", "--", oldRelative)
		command.Env = gitCommandEnvironment()
		output, err := command.Output()
		if err != nil {
			return nil, gitIndexFailure("Git index flags are unavailable")
		}
		for _, raw := range bytes.Split(output, []byte{0}) {
			if len(raw) < 3 || raw[1] != ' ' || raw[0] != spec.match {
				continue
			}
			path := string(raw[2:])
			if path == "" {
				return nil, gitIndexFailure("Git index flags are malformed")
			}
			flags[spec.option] = append(flags[spec.option], path)
		}
	}
	return flags, nil
}

func gitRenameRoot(ctx context.Context, directory string) (string, error) {
	command := exec.CommandContext(ctx, "git", "-C", directory, "rev-parse", "--show-toplevel")
	command.Env = gitCommandEnvironment()
	output, err := command.Output()
	if err != nil {
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		if exit, ok := err.(*exec.ExitError); ok && exit.ExitCode() == 128 {
			return "", nil
		}
		return "", err
	}
	return strings.TrimSpace(string(output)), nil
}

// A deleted working-tree destination may still contain unique staged content.
// Never replace that index entry, including descendants of a moved directory.
func checkGitRenameDestination(ctx context.Context, target *mutationParent) *Error {
	root, err := gitRenameRoot(ctx, target.resolvedParent)
	if err != nil {
		return filesystemError(target.requested, err)
	}
	if root == "" {
		return nil
	}
	relative, err := filepath.Rel(root, filepath.Join(target.resolvedParent, target.name))
	if err != nil {
		return filesystemError(target.requested, err)
	}
	command := exec.CommandContext(ctx, "git", "--literal-pathspecs", "-C", root, "ls-files", "--stage", "-z", "--", relative)
	command.Env = gitCommandEnvironment()
	output, err := command.Output()
	if err != nil {
		return gitIndexChanged(target.requested, err)
	}
	if len(output) > 0 {
		return targetExists(target.requested)
	}
	return nil
}

// Tracked moves retain index.lock through namespace publication. Untracked
// paths stay outside the index; readers and writers share this transaction owner.
func prepareGitRename(ctx context.Context, oldPath string) (*gitRenamePlan, error) {
	root, err := gitRenameRoot(ctx, filepath.Dir(oldPath))
	if err != nil || root == "" {
		return nil, err
	}

	oldRelative, err := filepath.Rel(root, oldPath)
	if err != nil || oldRelative == "." || strings.HasPrefix(oldRelative, ".."+string(filepath.Separator)) {
		return nil, nil
	}
	command := exec.CommandContext(ctx, "git", "--literal-pathspecs", "-C", root, "ls-files", "--error-unmatch", "--", oldRelative)
	command.Env = gitCommandEnvironment()
	if err := command.Run(); err != nil {
		if exitError, ok := err.(*exec.ExitError); ok && exitError.ExitCode() == 1 {
			return nil, nil
		}
		return nil, fmt.Errorf("git tracked-path check: %w", err)
	}
	indexCommand := exec.CommandContext(ctx, "git", "-C", root, "rev-parse", "--git-path", "index")
	indexCommand.Env = gitCommandEnvironment()
	indexOutput, err := indexCommand.Output()
	if err != nil {
		return nil, gitIndexFailure("Git index path is unavailable")
	}
	plan := &gitRenamePlan{root: root, oldPath: oldRelative, indexPath: gitPath(root, string(indexOutput))}
	if err := plan.acquireIndexLock(); err != nil {
		return nil, err
	}
	keep := false
	defer func() {
		if !keep {
			plan.close()
		}
	}()
	original, identity, mode, err := readGitIndex(plan.indexPath)
	if err != nil {
		return nil, errors.Join(gitIndexFailure("Git index snapshot failed"), err)
	}
	plan.indexOriginal, plan.indexIdentity, plan.indexMode = original, identity, mode
	plan.indexDigest = sha256.Sum256(original)
	command = exec.CommandContext(ctx, "git", "--literal-pathspecs", "-C", root, "ls-files", "--stage", "-z", "--", oldRelative)
	command.Env = gitCommandEnvironment()
	output, err := command.Output()
	if err != nil {
		return nil, fmt.Errorf("git index rename snapshot: %w", err)
	}
	rows := make([]gitIndexRenameRow, 0)
	oldPrefix := oldRelative + "/"
	for _, rawRow := range bytes.Split(output, []byte{0}) {
		if len(rawRow) == 0 {
			continue
		}
		metadata, rawPath, ok := bytes.Cut(rawRow, []byte{'\t'})
		if !ok {
			return nil, fmt.Errorf("git index rename snapshot has malformed output")
		}
		fields := strings.Fields(string(metadata))
		if len(fields) != 3 || fields[2] != "0" {
			return nil, fmt.Errorf("git index rename snapshot has unsupported staged entry")
		}
		oldPath := string(rawPath)
		suffix := ""
		if oldPath == oldRelative {
			suffix = ""
		} else if strings.HasPrefix(oldPath, oldPrefix) {
			suffix = oldPath[len(oldRelative):]
		} else {
			continue
		}
		rows = append(rows, gitIndexRenameRow{metadata: string(metadata), oldPath: oldPath, newPath: oldRelative + suffix})
	}
	if len(rows) == 0 {
		return nil, nil
	}
	flags, flagsErr := gitIndexFlags(ctx, root, oldRelative)
	if flagsErr != nil {
		return nil, flagsErr
	}
	if !gitIndexMatchesSnapshot(plan) {
		return nil, gitIndexFailure("Git index changed during preparation")
	}
	plan.rows, plan.flags = rows, flags
	keep = true
	return plan, nil
}

func gitIndexMatchesSnapshot(plan *gitRenamePlan) bool {
	data, identity, _, err := readGitIndex(plan.indexPath)
	return err == nil && sameDescriptorIdentity(identity, plan.indexIdentity) && sha256.Sum256(data) == plan.indexDigest
}

// Git writers share index.lock. Build every row/flag update privately before the
// sole rename, so a failed flag update cannot publish a partially rewritten index.
func (plan *gitRenamePlan) commitIndex(data []byte) error {
	if plan.lock == nil {
		return gitIndexFailure("Git index lock is not owned")
	}
	if !gitIndexMatchesSnapshot(plan) {
		return gitIndexFailure("Git index changed before publication")
	}
	opened, err := plan.lock.Stat()
	if err != nil {
		return err
	}
	if owned, err := gitIndexLockOwned(plan.lock); err != nil || !owned {
		return errors.Join(gitIndexFailure("Git index lock identity changed"), err)
	}
	if err := plan.lock.Chmod(plan.indexMode.Perm()); err != nil {
		return err
	}
	if _, err := plan.lock.Write(data); err != nil {
		return err
	}
	if err := plan.lock.Sync(); err != nil {
		return err
	}
	if !gitIndexMatchesSnapshot(plan) {
		return gitIndexFailure("Git index changed before publication")
	}
	// Hashing the existing index and syncing the candidate may yield to another
	// process. Recheck the named lock after that work, at the namespace boundary.
	if owned, err := gitIndexLockOwned(plan.lock); err != nil || !owned {
		return errors.Join(gitIndexFailure("Git index lock identity changed"), err)
	}
	if err := os.Rename(plan.lock.Name(), plan.indexPath); err != nil {
		return err
	}
	plan.indexIdentity = identityFor(opened)
	plan.indexDigest = sha256.Sum256(data)
	plan.indexUpdated = true
	lock := plan.lock
	plan.lock = nil
	return lock.Close()
}

func (plan *gitRenamePlan) publish(ctx context.Context, newPath string) error {
	if plan == nil {
		return nil
	}
	if !gitIndexMatchesSnapshot(plan) {
		return gitIndexFailure("Git index changed before publication")
	}
	candidate, err := os.CreateTemp(filepath.Dir(plan.indexPath), ".yomux-index-")
	if err != nil {
		return err
	}
	candidatePath := candidate.Name()
	defer func() {
		for _, path := range []string{candidatePath, candidatePath + ".lock"} {
			if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
				slog.Error("remove private Git rename candidate", "error", err)
			}
		}
	}()
	_, writeErr := candidate.Write(plan.indexOriginal)
	if err := errors.Join(writeErr, candidate.Close()); err != nil {
		return err
	}
	environment := append(gitCommandEnvironment(), "GIT_INDEX_FILE="+candidatePath)
	indexInput := bytes.NewBuffer(nil)
	for _, row := range plan.rows {
		fields := strings.Fields(row.metadata)
		zeros := strings.Repeat("0", len(fields[1]))
		_, _ = fmt.Fprintf(indexInput, "0 %s 0\t%s%c", zeros, row.oldPath, 0)
		_, _ = fmt.Fprintf(indexInput, "%s\t%s%c", row.metadata, newPath+row.newPath[len(plan.oldPath):], 0)
	}
	command := exec.CommandContext(ctx, "git", "-C", plan.root, "update-index", "-z", "--index-info")
	command.Env = environment
	command.Stdin = indexInput
	if output, err := command.CombinedOutput(); err != nil {
		return errors.Join(gitIndexFailure("Git index rename failed: %s", strings.TrimSpace(string(output))), err)
	}
	for option, paths := range plan.flags {
		if len(paths) == 0 {
			continue
		}
		updateOption := map[string]string{"-v": "--assume-unchanged", "-t": "--skip-worktree", "-f": "--fsmonitor-valid"}[option]
		if updateOption == "" {
			return gitIndexFailure("Git index flag option is unsupported")
		}
		input := bytes.NewBuffer(nil)
		for _, oldPath := range paths {
			for _, row := range plan.rows {
				if row.oldPath == oldPath {
					_, _ = input.WriteString(newPath + row.newPath[len(plan.oldPath):])
					_ = input.WriteByte(0)
					break
				}
			}
		}
		flagCommand := exec.CommandContext(ctx, "git", "-C", plan.root, "update-index", updateOption, "-z", "--stdin")
		flagCommand.Env = environment
		flagCommand.Stdin = input
		if output, err := flagCommand.CombinedOutput(); err != nil {
			return errors.Join(gitIndexFailure("Git index flag publication failed: %s", strings.TrimSpace(string(output))), err)
		}
	}
	data, _, _, err := readGitIndex(candidatePath)
	if err != nil {
		return err
	}
	if err := plan.commitIndex(data); err != nil {
		return err
	}
	plan.indexUpdated = true
	return nil
}

func (plan *gitRenamePlan) rollback(ctx context.Context, _ string) error {
	if plan == nil || !plan.indexUpdated {
		return nil
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if err := plan.acquireIndexLock(); err != nil {
		return err
	}
	defer plan.close()
	// Refuse to replace a later external writer's index, even when filesystem
	// rollback is required after the completed publication.
	if err := plan.commitIndex(plan.indexOriginal); err != nil {
		return err
	}
	plan.indexUpdated = false
	return nil
}
