// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build darwin

package finder

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

func prototypeGit(t *testing.T, root string, args ...string) string {
	t.Helper()
	command := exec.Command("git", append([]string{"-C", root}, args...)...)
	command.Env = append(historyPrototypeEnvironment(), "GIT_AUTHOR_NAME=History Fixture", "GIT_AUTHOR_EMAIL=fixture@example.invalid", "GIT_COMMITTER_NAME=History Fixture", "GIT_COMMITTER_EMAIL=fixture@example.invalid", "GIT_AUTHOR_DATE=2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE=2026-01-01T00:00:00Z")
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v %s", args, err, output)
	}
	return strings.TrimSpace(string(output))
}

func prototypeRepo(t *testing.T) (*Service, string) {
	t.Helper()
	root := t.TempDir()
	prototypeGit(t, root, "init", "-b", "main")
	s, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Close)
	return s, root
}

func prototypeCompare(t *testing.T, s *Service, root string, request HistoryPrototypeRequest) HistoryPrototypeComparison {
	t.Helper()
	result, err := s.CompareHistoryPrototype(context.Background(), root, os.Args[0], request)
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func TestDarwinHistoryPrototypeNativeParity(t *testing.T) {
	s, root := prototypeRepo(t)
	request := HistoryPrototypeRequest{Limit: 50}
	if result := prototypeCompare(t, s, root, request); !result.Qualified {
		t.Fatalf("unborn: %+v", result)
	}
	for _, subject := range []string{"initial", "fix  repeated spaces", "title\nwrapped subject\n\nbody"} {
		prototypeGit(t, root, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", subject)
	}
	prototypeGit(t, root, "tag", "-a", "v1", "-m", "annotated tag")
	prototypeGit(t, root, "tag", "lightweight")
	prototypeGit(t, root, "update-ref", "refs/remotes/origin/main", "HEAD")
	prototypeGit(t, root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
	for _, packed := range []bool{false, true} {
		if packed {
			prototypeGit(t, root, "pack-refs", "--all")
		}
		for _, r := range []HistoryPrototypeRequest{request, {Limit: 1}, {Limit: 1, Offset: 1}, {Limit: 2, Offset: 2}} {
			result := prototypeCompare(t, s, root, r)
			if !result.Qualified {
				t.Fatalf("packed=%t request=%+v: differences=%v snapshot=%+v descriptor=%+v", packed, r, result.Differences, result.Snapshot, result.Descriptor)
			}
		}
	}
	prototypeGit(t, root, "checkout", "--detach")
	if result := prototypeCompare(t, s, root, request); !result.Qualified {
		t.Fatalf("detached: %+v", result)
	}
}

func TestDarwinHistoryPrototypeLinkedWorktree(t *testing.T) {
	s, root := prototypeRepo(t)
	prototypeGit(t, root, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "initial")
	linked := filepath.Join(root, "linked")
	prototypeGit(t, root, "worktree", "add", "-b", "linked", linked)
	prototypeGit(t, linked, "update-ref", "refs/worktree/local", "HEAD")
	result := prototypeCompare(t, s, linked, HistoryPrototypeRequest{Limit: 50})
	if !result.Qualified {
		t.Fatalf("linked: %+v snapshot=%+v descriptor=%+v", result, result.Snapshot.Page.Commits, result.Descriptor.Page.Commits)
	}
}

func TestDarwinHistoryPrototypeReportsOrderingDifference(t *testing.T) {
	s, root := prototypeRepo(t)
	prototypeGit(t, root, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "base")
	prototypeGit(t, root, "checkout", "-b", "side")
	for _, subject := range []string{"side one", "side two"} {
		prototypeGit(t, root, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", subject)
	}
	prototypeGit(t, root, "checkout", "main")
	for _, subject := range []string{"main one", "main two"} {
		prototypeGit(t, root, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", subject)
	}
	prototypeGit(t, root, "-c", "commit.gpgsign=false", "merge", "--no-ff", "side", "-m", "merge")
	result := prototypeCompare(t, s, root, HistoryPrototypeRequest{Limit: 50})
	if result.Snapshot.Error != nil || result.Descriptor.Error != nil {
		t.Fatalf("merge read failed: %+v", result)
	}
	if reflect.DeepEqual(result.Snapshot.Page.Commits, result.Descriptor.Page.Commits) || result.Qualified {
		t.Fatal("equal-timestamp merge must expose and disqualify the ordering difference")
	}
	// A mismatch is diagnostic evidence, not a reason to change ordering algorithms.
	t.Logf("equal-timestamp merge qualified=%t differences=%v", result.Qualified, result.Differences)
}

func TestDarwinHistoryPrototypeRejectsShallowAndExternalMetadata(t *testing.T) {
	for _, metadata := range []string{"shallow", "objects/info/alternates", "objects/info/http-alternates", "info/grafts"} {
		t.Run(metadata, func(t *testing.T) {
			s, root := prototypeRepo(t)
			prototypeGit(t, root, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "initial")
			scope, e := s.openGitHistoryScopeWithOptions(context.Background(), root, gitHistoryScopeOptions{})
			if e != nil {
				t.Fatal(e)
			}
			defer scope.close()
			path := filepath.Join(root, ".git", metadata)
			if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, []byte(strings.Repeat("a", 40)+"\n"), 0600); err != nil {
				t.Fatal(err)
			}
			_, err := runHistoryPrototypeProcess(context.Background(), os.Args[0], scope.namespace, HistoryPrototypeRequest{Limit: 50})
			if err == nil || !strings.Contains(err.Error(), "unsupported prototype metadata") {
				t.Fatalf("%s: %v", metadata, err)
			}
		})
	}
}

func TestDarwinHistoryPrototypeConfigDoesNotReadIncludes(t *testing.T) {
	s, root := prototypeRepo(t)
	prototypeGit(t, root, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "initial")
	prototypeGit(t, root, "config", "include.path", "/nonexistent/prototype-include")
	if result := prototypeCompare(t, s, root, HistoryPrototypeRequest{Limit: 50}); !result.Qualified {
		t.Fatalf("include: %+v", result)
	}
	prototypeGit(t, root, "config", "extensions.unknownprototype", "true")
	scope, e := s.openGitHistoryScopeWithOptions(context.Background(), root, gitHistoryScopeOptions{})
	if e != nil {
		t.Fatal(e)
	}
	defer scope.close()
	_, err := runHistoryPrototypeProcess(context.Background(), os.Args[0], scope.namespace, HistoryPrototypeRequest{Limit: 50})
	if err == nil {
		t.Fatal("unknown extension accepted")
	}
}

func TestDarwinHistoryPrototypeConcurrentRefsAndRepack(t *testing.T) {
	s, root := prototypeRepo(t)
	for i := 0; i < 8; i++ {
		prototypeGit(t, root, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fixture")
	}
	scope, e := s.openGitHistoryScopeWithOptions(context.Background(), root, gitHistoryScopeOptions{})
	if e != nil {
		t.Fatal(e)
	}
	defer scope.close()
	done := make(chan error, 1)
	go func() {
		for i := 0; i < 4; i++ {
			cmd := exec.Command("git", "-C", root, "repack", "-ad")
			cmd.Env = historyPrototypeEnvironment()
			if err := cmd.Run(); err != nil {
				done <- err
				return
			}
			cmd = exec.Command("git", "-C", root, "update-ref", "refs/heads/moving", "HEAD~1")
			cmd.Env = historyPrototypeEnvironment()
			if err := cmd.Run(); err != nil {
				done <- err
				return
			}
		}
		done <- nil
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	for i := 0; i < 4; i++ {
		page, err := runHistoryPrototypeProcess(ctx, os.Args[0], scope.namespace, HistoryPrototypeRequest{Limit: 50})
		if err == nil && len(page.Commits) != 8 {
			t.Fatalf("partial success: %+v", page)
		}
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if ctx.Err() != nil {
		t.Fatal(ctx.Err())
	}
}

func TestDarwinHistoryPrototypeTraversalSwapAndRestore(t *testing.T) {
	for _, boundary := range []string{"root", "refs", "objects"} {
		t.Run(boundary, func(t *testing.T) {
			s, root := prototypeRepo(t)
			prototypeGit(t, root, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "authorized")
			_, outside := prototypeRepo(t)
			prototypeGit(t, outside, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "OUTSIDE-CONTENT")
			outsideHead := prototypeGit(t, outside, "rev-parse", "HEAD")
			scope, finderErr := s.openGitHistoryScopeWithOptions(context.Background(), root, gitHistoryScopeOptions{})
			if finderErr != nil {
				t.Fatal(finderErr)
			}
			defer scope.close()
			fs := &descriptorGitFS{git: scope.namespace.gitDir, common: scope.namespace.commonDir, objects: scope.namespace.objects}
			path, target := filepath.Join(root, ".git"), filepath.Join(outside, ".git")
			if boundary != "root" {
				path, target = filepath.Join(path, boundary), filepath.Join(target, boundary)
			}
			swapped, restored := false, false
			restore := func() {
				if swapped && !restored {
					if err := os.Remove(path); err != nil {
						t.Fatal(err)
					}
					if err := os.Rename(path+".saved", path); err != nil {
						t.Fatal(err)
					}
					restored = true
				}
			}
			defer restore()
			fs.afterOpen = func(name, component string) {
				trigger := boundary == "root" && name == "config" || boundary == "refs" && name == "refs/heads/main" && component == "refs" || boundary == "objects" && strings.HasPrefix(name, "objects/") && len(component) == 2
				if trigger && !swapped {
					if err := os.Rename(path, path+".saved"); err != nil {
						t.Fatal(err)
					}
					if err := os.Symlink(target, path); err != nil {
						t.Fatal(err)
					}
					swapped = true
				} else if swapped && !restored {
					restore()
				}
			}
			page, err := readDescriptorGitHistory(fs, HistoryPrototypeRequest{Limit: 50})
			if !swapped {
				t.Fatal("history traversal did not reach swap barrier")
			}
			restore()
			if err == nil {
				if len(page.Commits) != 1 || page.Commits[0].Subject != "authorized" {
					t.Fatalf("unauthorized history returned: %+v", page)
				}
				for name, hash := range page.Refs {
					if strings.Contains(name, "OUTSIDE") || hash == outsideHead {
						t.Fatal("outside ref returned")
					}
				}
			}
		})
	}
}

func TestDarwinHistoryPrototypeRealShallowComparison(t *testing.T) {
	s, root := prototypeRepo(t)
	for _, subject := range []string{"base", "tip"} {
		prototypeGit(t, root, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", subject)
	}
	shallow := filepath.Join(root, "shallow-clone")
	prototypeGit(t, root, "clone", "--depth=1", "file://"+root, shallow)
	result := prototypeCompare(t, s, shallow, HistoryPrototypeRequest{Limit: 50})
	if result.Snapshot.Error != nil || result.Snapshot.Page == nil || len(result.Snapshot.Page.Commits) != 1 {
		t.Fatalf("native shallow: %+v", result.Snapshot)
	}
	if result.Descriptor.Error == nil || !strings.Contains(result.Descriptor.Error.Message, "shallow") || result.Qualified {
		t.Fatalf("shallow compatibility was hidden: %+v", result)
	}
}

func TestDarwinHistoryPrototypeUnsupportedFormats(t *testing.T) {
	for _, setting := range []string{"objectFormat=sha256", "refStorage=reftable", "futureFormat=true", "[extensions \"future\"]\nobjectFormat=sha1"} {
		t.Run(setting, func(t *testing.T) {
			s, root := prototypeRepo(t)
			scope, finderErr := s.openGitHistoryScopeWithOptions(context.Background(), root, gitHistoryScopeOptions{})
			if finderErr != nil {
				t.Fatal(finderErr)
			}
			defer scope.close()
			config, err := os.OpenFile(filepath.Join(root, ".git", "config"), os.O_APPEND|os.O_WRONLY, 0600)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := config.WriteString("\n[extensions]\n" + setting + "\n"); err != nil {
				config.Close()
				t.Fatal(err)
			}
			if err := config.Close(); err != nil {
				t.Fatal(err)
			}
			_, err = runHistoryPrototypeProcess(context.Background(), os.Args[0], scope.namespace, HistoryPrototypeRequest{Limit: 50})
			if err == nil || !strings.Contains(err.Error(), "unsupported") {
				t.Fatalf("format %s: %v", setting, err)
			}
		})
	}
}
