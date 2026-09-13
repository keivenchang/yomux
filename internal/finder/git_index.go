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
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
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

func snapshotGitIndex(root string) (string, descriptorIdentity, [sha256.Size]byte, error) {
	var digest [sha256.Size]byte
	command := exec.Command("git", "-C", root, "rev-parse", "--git-path", "index")
	command.Env = gitCommandEnvironment()
	output, err := command.Output()
	if err != nil {
		return "", descriptorIdentity{}, digest, gitIndexFailure("Git index path is unavailable")
	}
	indexPath := gitPath(root, string(output))
	info, err := os.Lstat(indexPath)
	if errors.Is(err, os.ErrNotExist) {
		return "", descriptorIdentity{}, digest, nil
	}
	if err != nil || info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() || info.Size() > gitIndexMaxBytes {
		return "", descriptorIdentity{}, digest, gitIndexFailure("Git index is not a bounded regular file")
	}
	file, err := os.Open(indexPath)
	if err != nil {
		return "", descriptorIdentity{}, digest, gitIndexFailure("Git index cannot be opened: %v", err)
	}
	defer file.Close()
	hasher := sha256.New()
	if _, err := io.CopyN(hasher, file, info.Size()); err != nil && !errors.Is(err, io.EOF) {
		return "", descriptorIdentity{}, digest, gitIndexFailure("Git index cannot be read: %v", err)
	}
	copy(digest[:], hasher.Sum(nil))
	return indexPath, identityFor(info), digest, nil
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
		command := exec.CommandContext(ctx, "git", "-C", root, "ls-files", spec.option, "-z", "--", oldRelative)
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

// prepareGitRename records whether the source is tracked before the namespace move. Untracked
// paths deliberately remain outside Git's index, matching the Python mutation owner.
func prepareGitRename(ctx context.Context, oldPath string) (*gitRenamePlan, error) {
	command := exec.CommandContext(ctx, "git", "-C", filepath.Dir(oldPath), "rev-parse", "--show-toplevel")
	command.Env = gitCommandEnvironment()
	output, err := command.Output()
	if err != nil {
		return nil, nil
	}
	root := strings.TrimSpace(string(output))
	if root == "" {
		return nil, nil
	}
	oldRelative, err := filepath.Rel(root, oldPath)
	if err != nil || oldRelative == "." || strings.HasPrefix(oldRelative, ".."+string(filepath.Separator)) {
		return nil, nil
	}
	command = exec.CommandContext(ctx, "git", "-C", root, "ls-files", "--error-unmatch", "--", oldRelative)
	command.Env = gitCommandEnvironment()
	if err := command.Run(); err != nil {
		if exitError, ok := err.(*exec.ExitError); ok && exitError.ExitCode() == 1 {
			return nil, nil
		}
		return nil, fmt.Errorf("git tracked-path check: %w", err)
	}
	command = exec.CommandContext(ctx, "git", "-C", root, "ls-files", "--stage", "-z", "--", oldRelative)
	command.Env = gitCommandEnvironment()
	output, err = command.Output()
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
	indexPath, indexIdentity, indexDigest, snapshotErr := snapshotGitIndex(root)
	if snapshotErr != nil {
		return nil, snapshotErr
	}
	if indexPath == "" {
		return nil, gitIndexFailure("tracked Git index disappeared")
	}
	lockPath := filepath.Join(filepath.Dir(indexPath), "index.lock")
	if _, lockErr := os.Lstat(lockPath); lockErr == nil {
		return nil, gitIndexFailure("Git index is already locked")
	} else if !os.IsNotExist(lockErr) {
		return nil, gitIndexFailure("Git index lock cannot be inspected: %v", lockErr)
	}
	flags, flagsErr := gitIndexFlags(ctx, root, oldRelative)
	if flagsErr != nil {
		return nil, flagsErr
	}
	return &gitRenamePlan{root: root, oldPath: oldRelative, rows: rows, indexPath: indexPath, indexIdentity: indexIdentity, indexDigest: indexDigest, flags: flags}, nil
}

func gitIndexMatchesSnapshot(plan *gitRenamePlan) bool {
	if plan == nil || plan.indexPath == "" {
		return false
	}
	info, err := os.Lstat(plan.indexPath)
	if err != nil || info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() || !sameDescriptorIdentity(identityFor(info), plan.indexIdentity) || info.Size() > gitIndexMaxBytes {
		return false
	}
	file, err := os.Open(plan.indexPath)
	if err != nil {
		return false
	}
	defer file.Close()
	hasher := sha256.New()
	if _, err := io.CopyN(hasher, file, info.Size()); err != nil && !errors.Is(err, io.EOF) {
		return false
	}
	var digest [sha256.Size]byte
	copy(digest[:], hasher.Sum(nil))
	return digest == plan.indexDigest
}

func (plan *gitRenamePlan) publish(ctx context.Context, newPath string) error {
	if plan == nil {
		return nil
	}
	if !gitIndexMatchesSnapshot(plan) {
		return gitIndexFailure("Git index changed before publication")
	}
	indexInput := bytes.NewBuffer(nil)
	for _, row := range plan.rows {
		fields := strings.Fields(row.metadata)
		zeros := strings.Repeat("0", len(fields[1]))
		_, _ = fmt.Fprintf(indexInput, "0 %s 0\t%s%c", zeros, row.oldPath, 0)
		_, _ = fmt.Fprintf(indexInput, "%s\t%s%c", row.metadata, newPath+row.newPath[len(plan.oldPath):], 0)
	}
	command := exec.CommandContext(ctx, "git", "-C", plan.root, "update-index", "-z", "--index-info")
	command.Env = gitCommandEnvironment()
	command.Stdin = indexInput
	if output, err := command.CombinedOutput(); err != nil {
		return gitIndexFailure("Git index rename failed: %s", strings.TrimSpace(string(output)))
	}
	plan.indexUpdated = true
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
		flagCommand.Env = gitCommandEnvironment()
		flagCommand.Stdin = input
		if output, err := flagCommand.CombinedOutput(); err != nil {
			return gitIndexFailure("Git index flag publication failed: %s", strings.TrimSpace(string(output)))
		}
	}
	return nil
}

func (plan *gitRenamePlan) rollback(ctx context.Context, newPath string) error {
	if plan == nil || !plan.indexUpdated {
		return nil
	}
	rollbackContext, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	indexInput := bytes.NewBuffer(nil)
	for _, row := range plan.rows {
		fields := strings.Fields(row.metadata)
		if len(fields) < 2 {
			return gitIndexFailure("Git index rename snapshot is malformed")
		}
		zeros := strings.Repeat("0", len(fields[1]))
		_, _ = fmt.Fprintf(indexInput, "0 %s 0\t%s%c", zeros, newPath+row.newPath[len(plan.oldPath):], 0)
		_, _ = fmt.Fprintf(indexInput, "%s\t%s%c", row.metadata, row.oldPath, 0)
	}
	command := exec.CommandContext(rollbackContext, "git", "-C", plan.root, "update-index", "-z", "--index-info")
	command.Env = gitCommandEnvironment()
	command.Stdin = indexInput
	if output, err := command.CombinedOutput(); err != nil {
		return gitIndexFailure("Git index rename rollback failed: %s", strings.TrimSpace(string(output)))
	}
	for option, paths := range plan.flags {
		if len(paths) == 0 {
			continue
		}
		updateOption := map[string]string{"-v": "--assume-unchanged", "-t": "--skip-worktree", "-f": "--fsmonitor-valid"}[option]
		if updateOption == "" {
			return gitIndexFailure("Git index flag rollback option is unsupported")
		}
		input := bytes.NewBuffer(nil)
		for _, oldPath := range paths {
			_, _ = input.WriteString(oldPath)
			_ = input.WriteByte(0)
		}
		flagCommand := exec.CommandContext(rollbackContext, "git", "-C", plan.root, "update-index", updateOption, "-z", "--stdin")
		flagCommand.Env = gitCommandEnvironment()
		flagCommand.Stdin = input
		if output, err := flagCommand.CombinedOutput(); err != nil {
			return gitIndexFailure("Git index flag rollback failed: %s", strings.TrimSpace(string(output)))
		}
	}
	plan.indexUpdated = false
	return nil
}
