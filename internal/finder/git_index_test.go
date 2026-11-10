// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestGitRenameOwnsIndexAcrossPreparationAndPublication(t *testing.T) {
	for _, move := range []bool{false, true} {
		for _, phase := range []string{"ls-files --stage -z", "update-index -z --index-info", "update-index --assume-unchanged"} {
			t.Run(fmt.Sprintf("move=%t/%s", move, phase), func(t *testing.T) {
				realGit, err := exec.LookPath("git")
				if err != nil {
					t.Fatal(err)
				}
				root, _ := newCommittedTestRepo(t)
				source := filepath.Join(root, "note.txt")
				marker := filepath.Join(t.TempDir(), "stage-result")
				command := exec.Command(realGit, "-C", root, "update-index", "--assume-unchanged", "note.txt")
				command.Env = gitCommandEnvironment()
				if output, err := command.CombinedOutput(); err != nil {
					t.Fatalf("flag fixture: %v %s", err, output)
				}
				// A source modification between the row read and its old snapshot used to
				// be admitted, then overwritten in the destination index by stale rows.
				wrapperDir := t.TempDir()
				target := "renamed.txt"
				if move {
					target = "destination/note.txt"
				}
				injectionRelative, suffix := target, ""
				if phase == "ls-files --stage -z" {
					injectionRelative, suffix = "note.txt", " -- note.txt"
				}
				indexPath := filepath.Join(root, ".git", "index")
				program := fmt.Sprintf("#!/bin/sh\n%q \"$@\"\nresult=$?\nif [ \"$result\" -ne 0 ]; then exit \"$result\"; fi\ncase \"$*\" in *%q*%q)\n printf 'new unique unstaged bytes\\n' > %q\n if GIT_INDEX_FILE=%q %q -C %q update-index --no-assume-unchanged %q 2>/dev/null && GIT_INDEX_FILE=%q %q -C %q add %q 2>/dev/null; then printf admitted > %q; else printf blocked > %q; fi\n ;;\nesac\n", realGit, phase, suffix, filepath.Join(root, injectionRelative), indexPath, realGit, root, injectionRelative, indexPath, realGit, root, injectionRelative, marker, marker)

				if err := os.WriteFile(filepath.Join(wrapperDir, "git"), []byte(program), 0700); err != nil {
					t.Fatal(err)
				}
				t.Setenv("PATH", wrapperDir+string(os.PathListSeparator)+os.Getenv("PATH"))
				service, err := New(root, nil)
				if err != nil {
					t.Fatal(err)
				}
				defer service.Close()
				if move {
					destination := filepath.Join(root, "destination")
					if err := os.Mkdir(destination, 0700); err != nil {
						t.Fatal(err)
					}
					result, failure := service.Move(context.Background(), []string{source}, destination)
					if failure != nil || result["complete"] != true {
						t.Fatal(result, failure)
					}
				} else if _, failure := service.Rename(context.Background(), source, "renamed.txt"); failure != nil {
					t.Fatal(failure)
				}
				result, err := os.ReadFile(marker)
				if err != nil {
					t.Fatal("phase did not execute", err)
				}
				if string(result) != "blocked" {
					t.Fatal("concurrent stage was admitted while rename owned the index")
				}
				command = exec.Command(realGit, "-C", root, "show", ":"+target)
				command.Env = gitCommandEnvironment()
				data, err := command.Output()
				if err != nil || string(data) != "one\n" {
					t.Fatalf("staged content changed: %q %v", data, err)
				}
				data, err = os.ReadFile(filepath.Join(root, target))
				if err != nil || string(data) != "new unique unstaged bytes\n" {
					t.Fatalf("working edit lost: %q %v", data, err)
				}
				if _, err := os.Stat(filepath.Join(root, ".git", "index.lock")); !os.IsNotExist(err) {
					t.Fatal("rename retained index.lock", err)
				}
			})
		}
	}
}

func TestGitRenameRollbackDoesNotOverwriteLaterStagedWork(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	service.publishGitRename = func(ctx context.Context, plan *gitRenamePlan, path string) error {
		if err := plan.publish(ctx, path); err != nil {
			return err
		}
		if err := os.WriteFile(filepath.Join(root, path), []byte("new unique staged bytes\n"), 0600); err != nil {
			return err
		}
		command := exec.Command("git", "-C", root, "add", path)
		command.Env = gitCommandEnvironment()
		if output, err := command.CombinedOutput(); err != nil {
			return fmt.Errorf("external stage: %w: %s", err, output)
		}
		return errors.New("injected failure after external stage")
	}
	_, failure := service.Rename(context.Background(), filepath.Join(root, "note.txt"), "renamed.txt")
	if failure == nil || !strings.Contains(failure.Message, "index rollback") {
		t.Fatal("rollback conflict was hidden", failure)
	}
	if failure.MessageKey != "common.errorDetail" || failure.MessageParams["error"] != failure.Message {
		t.Fatalf("index rollback diagnostic lost to generic localization: %+v", failure)
	}
	command := exec.Command("git", "-C", root, "show", ":renamed.txt")
	command.Env = gitCommandEnvironment()
	data, err := command.Output()
	if err != nil || string(data) != "new unique staged bytes\n" {
		t.Fatalf("rollback overwrote external stage: %q %v", data, err)
	}
	if _, err := os.Stat(filepath.Join(root, ".git", "index.lock")); !os.IsNotExist(err) {
		t.Fatal("rollback retained index.lock", err)
	}
}

func TestGitRenamePreparationFailureReleasesOnlyOwnedLock(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	source := filepath.Join(root, "note.txt")
	plan, err := prepareGitRename(context.Background(), source)
	if err != nil {
		t.Fatal(err)
	}
	lockPath := plan.lock.Name()
	if err := os.Remove(lockPath); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(lockPath, []byte("other owner"), 0600); err != nil {
		t.Fatal(err)
	}
	plan.close()
	data, err := os.ReadFile(lockPath)
	if err != nil || string(data) != "other owner" {
		t.Fatalf("cleanup removed another owner's lock: %q %v", data, err)
	}
}

func TestGitRenamePrivateCandidatePreservesSplitIndexAndFailedFlags(t *testing.T) {
	for _, failFlags := range []bool{false, true} {
		t.Run(fmt.Sprint(failFlags), func(t *testing.T) {
			realGit, err := exec.LookPath("git")
			if err != nil {
				t.Fatal(err)
			}
			root, _ := newCommittedTestRepo(t)
			for _, args := range [][]string{{"update-index", "--split-index"}, {"update-index", "--assume-unchanged", "note.txt"}} {
				command := exec.Command(realGit, append([]string{"-C", root}, args...)...)
				command.Env = gitCommandEnvironment()
				if output, err := command.CombinedOutput(); err != nil {
					t.Fatalf("index fixture: %v %s", err, output)
				}
			}
			before, err := os.ReadFile(filepath.Join(root, ".git", "index"))
			if err != nil {
				t.Fatal(err)
			}
			if failFlags {
				wrapperDir := t.TempDir()
				program := fmt.Sprintf("#!/bin/sh\ncase \"$*\" in *'update-index --assume-unchanged'*) exit 1;; esac\nexec %q \"$@\"\n", realGit)
				if err := os.WriteFile(filepath.Join(wrapperDir, "git"), []byte(program), 0700); err != nil {
					t.Fatal(err)
				}
				t.Setenv("PATH", wrapperDir+string(os.PathListSeparator)+os.Getenv("PATH"))
			}
			service, err := New(root, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer service.Close()
			_, failure := service.Rename(context.Background(), filepath.Join(root, "note.txt"), "renamed.txt")
			if failFlags {
				if failure == nil {
					t.Fatal("flag publication failure ignored")
				}
				after, err := os.ReadFile(filepath.Join(root, ".git", "index"))
				if err != nil || string(after) != string(before) {
					t.Fatal("failed candidate changed original split index", err)
				}
				if _, err := os.Stat(filepath.Join(root, "note.txt")); err != nil {
					t.Fatal("filesystem was not restored", err)
				}
			} else {
				if failure != nil {
					t.Fatal(failure)
				}
				command := exec.Command(realGit, "-C", root, "ls-files", "-v", "--", "renamed.txt")
				command.Env = gitCommandEnvironment()
				output, err := command.Output()
				if err != nil || string(output) != "h renamed.txt\n" {
					t.Fatalf("split index flags lost: %s %v", output, err)
				}
			}
			entries, err := os.ReadDir(filepath.Join(root, ".git"))
			if err != nil {
				t.Fatal(err)
			}
			for _, entry := range entries {
				if strings.HasPrefix(entry.Name(), ".yomux-index-") || entry.Name() == "index.lock" {
					t.Fatal("transaction leaked candidate or lock", entry.Name())
				}
			}
		})
	}
}

func TestGitIndexLockReplacementBeforePublication(t *testing.T) {
	path := filepath.Join(t.TempDir(), "index")
	original := []byte(strings.Repeat("original", 1<<20))
	if err := os.WriteFile(path, original, 0600); err != nil {
		t.Fatal(err)
	}
	_, identity, mode, err := readGitIndex(path)
	if err != nil {
		t.Fatal(err)
	}
	plan := &gitRenamePlan{indexPath: path, indexIdentity: identity, indexMode: mode, indexDigest: sha256.Sum256(original)}
	if err := plan.acquireIndexLock(); err != nil {
		t.Fatal(err)
	}
	defer plan.close()
	lockPath := plan.lock.Name()
	done := make(chan error, 1)
	go func() {
		for {
			info, err := os.Stat(lockPath)
			if err != nil {
				done <- err
				return
			}
			if info.Size() > 0 {
				break
			}
			runtime.Gosched()
		}
		if err := os.Rename(lockPath, lockPath+".old"); err != nil {
			done <- err
			return
		}
		done <- os.WriteFile(lockPath, []byte("another writer's candidate"), 0600)
	}()
	err = plan.commitIndex([]byte("intended candidate"))
	if swapErr := <-done; swapErr != nil {
		t.Fatal(swapErr)
	}
	actual, readErr := os.ReadFile(path)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if err == nil || string(actual) == "another writer's candidate" {
		t.Fatalf("published replacement lock: err=%v actual=%q", err, actual)
	}
}
