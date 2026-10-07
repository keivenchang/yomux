// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestSessionFilesRepoRefsChooseEquivalentKeysDeterministically(t *testing.T) {
	repo := filepath.Clean(filepath.Join(t.TempDir(), "repo"))
	refs := map[string]map[string]string{
		repo + string(os.PathSeparator) + ".": {"from": "alias", "to": "alias-to"},
		repo:                                  {"from": "exact", "to": "exact-to"},
	}
	from, to := sessionFilesRefsForRepo(repo, "default", "base", refs)
	if from != "exact" || to != "exact-to" {
		t.Fatalf("selected refs = (%q, %q), want the exact repository key", from, to)
	}
}

func TestSessionFilesRepoRefsIgnoreUnmatchedEmptyKey(t *testing.T) {
	from, to := sessionFilesRefsForRepo("/repo", "HEAD", "current", map[string]map[string]string{
		"": {"from": "v1", "to": "v2"},
	})
	if from != "HEAD" || to != "current" {
		t.Fatalf("unmatched empty-key refs = (%q, %q), want request refs unchanged", from, to)
	}
}

func TestAggregateSessionFilesPreservesEffectiveComparisonRefs(t *testing.T) {
	payload := map[string]any{
		"repos": []map[string]any{
			{"repo": "/repo-a", "from_ref": "v1", "to_ref": "v2", "comparison_from_ref": "base-a", "comparison_to_ref": "current"},
			{"repo": "/repo-b", "from_ref": "v3", "to_ref": "v4", "comparison_from_ref": "base-b", "comparison_to_ref": "current"},
		},
		"files": []map[string]any{}, "refs_by_repo": map[string]any{},
		"errors": []any{}, "warnings": []any{},
	}
	aggregate := AggregateSessionFiles([]map[string]any{payload}, 24, "", "")
	rows := aggregate["repos"].([]map[string]any)
	if len(rows) != 2 {
		t.Fatalf("aggregate effective comparison = %#v", rows)
	}
	byRepo := map[string]map[string]any{rows[0]["repo"].(string): rows[0], rows[1]["repo"].(string): rows[1]}
	for repo, want := range map[string][4]string{
		"/repo-a": {"v1", "v2", "base-a", "current"},
		"/repo-b": {"v3", "v4", "base-b", "current"},
	} {
		row := byRepo[repo]
		got := [4]string{
			fmt.Sprint(row["from_ref"]), fmt.Sprint(row["to_ref"]),
			fmt.Sprint(row["comparison_from_ref"]), fmt.Sprint(row["comparison_to_ref"]),
		}
		if got != want {
			t.Errorf("%s comparison refs = %#v, want %#v", repo, got, want)
		}
	}
}

func sessionFileRows(payload map[string]any) map[string]map[string]any {
	rows := make(map[string]map[string]any)
	for _, raw := range payload["files"].([]map[string]any) {
		rows[raw["path"].(string)] = raw
	}
	return rows
}

func TestSessionFilesRepositoryResolverAcceptsFileAndDirectoryTargets(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	for _, target := range []string{root, filepath.Join(root, "note.txt")} {
		repo, finderErr := service.sessionFilesRepositoryForTarget(context.Background(), target, false)
		if finderErr != nil || repo != root {
			t.Fatalf("repository for %q = %q, error=%#v; want %q", target, repo, finderErr, root)
		}
	}
	repo, finderErr := service.sessionFilesRepositoryForTouchedPath(context.Background(), filepath.Join(root, "missing.txt"))
	if finderErr != nil || repo != root {
		t.Fatalf("repository for missing transcript file = %q, error=%#v; want %q", repo, finderErr, root)
	}
	if _, finderErr := service.sessionFilesRepositoryForTarget(context.Background(), filepath.Join(root, "missing-directory"), false); finderErr == nil || finderErr.Code != "path_not_found" {
		t.Fatalf("missing candidate root error = %#v, want path_not_found", finderErr)
	}
}

func TestSessionFilesKnownRepositoryReusesMostSpecificScope(t *testing.T) {
	root := t.TempDir()
	nestedRepo := filepath.Join(root, "nested")
	externalRepo := t.TempDir()
	for _, directory := range []string{filepath.Join(root, "src"), nestedRepo, externalRepo} {
		if err := os.MkdirAll(directory, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Symlink(externalRepo, filepath.Join(root, "external")); err != nil {
		t.Fatal(err)
	}
	scopes := map[string]*gitHistoryScope{
		root:       {repo: root},
		nestedRepo: {repo: nestedRepo},
	}
	tests := []struct {
		name string
		path string
		want string
		ok   bool
	}{
		{name: "parent repository", path: filepath.Join(root, "src", "missing.jsonl"), want: root, ok: true},
		{name: "nested repository wins", path: filepath.Join(nestedRepo, "missing.jsonl"), want: nestedRepo, ok: true},
		{name: "symlink escape is not reused", path: filepath.Join(root, "external", "missing.jsonl"), ok: false},
		{name: "unrelated path is not reused", path: filepath.Join(externalRepo, "missing.jsonl"), ok: false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			path := sessionFilesRepositoryScopePath(test.path, true)
			got, ok := sessionFilesKnownRepository(path, scopes)
			if got != test.want || ok != test.ok {
				t.Fatalf("known repository = (%q, %t), want (%q, %t)", got, ok, test.want, test.ok)
			}
		})
	}
}

func TestSessionFilesKnownRepositoryDoesNotCrossUnregisteredNestedRepository(t *testing.T) {
	root := t.TempDir()
	nestedRepo := filepath.Join(root, "nested")
	if err := os.MkdirAll(filepath.Join(nestedRepo, ".git"), 0o700); err != nil {
		t.Fatal(err)
	}
	scopes := map[string]*gitHistoryScope{root: {repo: root}}
	path := sessionFilesRepositoryScopePath(filepath.Join(nestedRepo, "missing.jsonl"), true)
	if got, ok := sessionFilesKnownRepository(path, scopes); ok {
		t.Fatalf("known repository = (%q, %t), want no reusable outer scope across the nested .git boundary", got, ok)
	}
}

func TestSessionFilesProjectsDefaultAndExplicitGitChanges(t *testing.T) {
	isolateSessionFilesTestHome(t)
	root, _ := newCommittedTestRepo(t)
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("two\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "new[one].txt"), []byte("new\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	defaultPayload, finderErr := service.SessionFiles(context.Background(), "agent-1", []string{root, root}, 7.5, "", "", nil)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	rows := sessionFileRows(defaultPayload)
	if len(rows) != 2 || rows["note.txt"]["status"] != "M" || rows["new[one].txt"]["status"] != "?" {
		t.Fatalf("default session files = %#v", defaultPayload)
	}
	if defaultPayload["session"] != "agent-1" || defaultPayload["hours"] != 7.5 || defaultPayload["from_ref"] != "default" || defaultPayload["to_ref"] != "base" {
		t.Fatalf("default envelope = %#v", defaultPayload)
	}
	repo := defaultPayload["repos"].([]map[string]any)
	if len(repo) != 1 || repo[0]["repo"] != root || repo[0]["count"] != 2 || repo[0]["from_ref"] != "default" || repo[0]["to_ref"] != "base" {
		t.Fatalf("default repository = %#v", defaultPayload["repos"])
	}
	if repo[0]["comparison_from_ref"] == "" || repo[0]["comparison_from_ref"] == "default" || repo[0]["comparison_to_ref"] != "current" {
		t.Fatalf("default repository comparison refs = %#v", repo[0])
	}
	if rows["note.txt"]["added"] != 1 || rows["note.txt"]["removed"] != 1 {
		t.Fatalf("numstat row = %#v", rows["note.txt"])
	}

	explicitPayload, finderErr := service.SessionFiles(context.Background(), "agent-1", []string{root}, 24, "HEAD", "current", nil)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if explicitPayload["from_ref"] != "HEAD" || explicitPayload["to_ref"] != "current" {
		t.Fatalf("explicit envelope = %#v", explicitPayload)
	}
	explicitRepo := explicitPayload["repos"].([]map[string]any)
	if len(explicitRepo) != 1 || explicitRepo[0]["from_ref"] != "HEAD" || explicitRepo[0]["to_ref"] != "current" || explicitRepo[0]["comparison_from_ref"] != "HEAD" || explicitRepo[0]["comparison_to_ref"] != "current" {
		t.Fatalf("explicit repository = %#v", explicitPayload["repos"])
	}
}

func TestSessionFilesDefaultBaseListsRefsOnceAndFallsBackToHEAD(t *testing.T) {
	root, _ := newCommittedTestRepo(t)
	for _, args := range [][]string{
		{"-C", root, "checkout", "--orphan", "unrelated"},
		{"-C", root, "rm", "-q", "--cached", "-r", "--", "."},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	unrelatedPath := filepath.Join(root, "unrelated.txt")
	if err := os.WriteFile(unrelatedPath, []byte("unrelated history\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{
		{"-C", root, "add", "--", "unrelated.txt"},
		{"-C", root, "commit", "-qm", "unrelated"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	unrelatedCommit, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(filepath.Join(root, "note.txt")); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "checkout", "master").CombinedOutput(); err != nil {
		t.Fatalf("restore master: %v (%s)", err, output)
	}
	for _, args := range [][]string{
		{"-C", root, "update-ref", "refs/remotes/origin/main", strings.TrimSpace(string(unrelatedCommit))},
		{"-C", root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"},
		{"-C", root, "update-ref", "refs/heads/main", strings.TrimSpace(string(unrelatedCommit))},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	headCommit, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
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
	originalRunner := execCommandContext
	commandCalls := 0
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		commandCalls++
		return originalRunner(ctx, args, extraFiles)
	}
	t.Cleanup(func() { execCommandContext = originalRunner })
	if base := sessionFilesDefaultBase(context.Background(), scope); base != strings.TrimSpace(string(headCommit)) {
		t.Fatalf("default base = %q, want current HEAD after unrelated refs", base)
	}
	if commandCalls != 4 {
		t.Fatalf("default-base Git command count = %d, want one ref listing and three unique ancestry checks", commandCalls)
	}
}

func TestSessionFilesDefaultBasePrefersOriginHEAD(t *testing.T) {
	root, base := newCommittedTestRepo(t)
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("second commit\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "commit", "-qam", "second").CombinedOutput(); err != nil {
		t.Fatalf("create second commit: %v (%s)", err, output)
	}
	for _, args := range [][]string{
		{"-C", root, "update-ref", "refs/remotes/origin/main", base},
		{"-C", root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"},
		{"-C", root, "update-ref", "refs/heads/main", "HEAD"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
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
	originalRunner := execCommandContext
	commandCalls := 0
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		commandCalls++
		return originalRunner(ctx, args, extraFiles)
	}
	t.Cleanup(func() { execCommandContext = originalRunner })
	if selected := sessionFilesDefaultBase(context.Background(), scope); selected != base {
		t.Fatalf("default base = %q, want origin/HEAD merge base %q", selected, base)
	}
	if commandCalls != 2 {
		t.Fatalf("default-base Git command count = %d, want one ref listing and one ancestry check", commandCalls)
	}
}

func TestSessionFilesWithAgentsKeepsCleanTranscriptTouchedFilesAndWindowAttribution(t *testing.T) {
	isolateSessionFilesTestHome(t)
	root, _ := newCommittedTestRepo(t)
	tracked := filepath.Join(root, "note.txt")
	transcript := filepath.Join(t.TempDir(), "claude.jsonl")
	transcriptLine := `{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"` + tracked + `"}}]}}` + "\n"
	if err := os.WriteFile(transcript, []byte(transcriptLine), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	payload, finderErr := service.SessionFilesWithAgents(context.Background(), "s1", []string{root}, []SessionFileAgent{
		{Kind: "claude", CWD: root, Transcript: transcript, PaneTarget: "s1:2.3", Window: "2", WindowIndex: 2, Pane: "3"},
	}, 24, "", "", nil)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	rows := sessionFileRows(payload)
	row := rows["note.txt"]
	if row == nil || row["status"] != "T" || row["source"] != "transcript" {
		t.Fatalf("clean transcript row = %#v", payload)
	}
	if got := row["agents"].([]any); len(got) != 1 || got[0] != "claude" || row["agent"] != "claude" {
		t.Fatalf("transcript attribution = %#v", row)
	}
	windows, ok := row["agent_windows"].([]map[string]any)
	if !ok || len(windows) != 1 || windows[0]["kind"] != "claude" || windows[0]["window"] != "2" || windows[0]["window_index"] != 2 || windows[0]["pane"] != "3" || windows[0]["pane_target"] != "s1:2.3" {
		t.Fatalf("transcript window attribution = %#v", row["agent_windows"])
	}
}

func TestSessionFilesWithAgentsMergesTranscriptAttributionIntoGitRows(t *testing.T) {
	isolateSessionFilesTestHome(t)
	root, _ := newCommittedTestRepo(t)
	tracked := filepath.Join(root, "note.txt")
	if err := os.WriteFile(tracked, []byte("working\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	transcript := filepath.Join(t.TempDir(), "rollout.jsonl")
	line := `{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":{"cmd":"git add note.txt","workdir":"` + root + `"}}}` + "\n"
	if err := os.WriteFile(transcript, []byte(line), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	payload, finderErr := service.SessionFilesWithAgents(context.Background(), "s1", []string{root}, []SessionFileAgent{
		{Kind: "codex", CWD: root, Transcript: transcript, PaneTarget: "s1:0.0", Window: "0", WindowIndex: 0, Pane: "0"},
	}, 24, "", "", nil)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	row := sessionFileRows(payload)["note.txt"]
	if row == nil || row["status"] != "M" || row["source"] != "git" {
		t.Fatalf("git transcript row = %#v", payload)
	}
	if got := row["agents"].([]any); len(got) != 1 || got[0] != "codex" {
		t.Fatalf("git attribution = %#v", row)
	}
}

func TestSessionFilesWithAgentsStreamEmitsOneChunkPerRepository(t *testing.T) {
	isolateSessionFilesTestHome(t)
	firstRoot, _ := newCommittedTestRepo(t)
	secondRoot, _ := newCommittedTestRepo(t)
	for _, root := range []string{firstRoot, secondRoot} {
		if err := os.WriteFile(filepath.Join(root, "changed.txt"), []byte("changed\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	service, err := New(firstRoot, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	chunks := make([]map[string]any, 0, 2)
	streamContext, cancel := context.WithCancel(context.Background())
	defer cancel()
	payload, finderErr := service.SessionFilesWithAgentsStream(streamContext, "s1", []string{firstRoot, secondRoot}, nil, 24, "", "", nil, func(chunk map[string]any) error {
		chunks = append(chunks, chunk)
		if len(chunks) == 2 {
			// Stop after the two initial Git chunks; transcript attribution is a later phase and is
			// intentionally not part of this latency-focused unit test.
			cancel()
		}
		return nil
	})
	if finderErr == nil || finderErr.Code != "cancelled" {
		t.Fatalf("stream error = %#v, want cancellation after the two initial repository chunks", finderErr)
	}
	if len(chunks) != 2 {
		t.Fatalf("stream chunks = %d, want one per repository: %#v", len(chunks), chunks)
	}
	seenRepos := make(map[string]bool)
	for _, chunk := range chunks {
		if chunk["loaded"] != false {
			t.Fatalf("repository chunk loaded = %#v", chunk)
		}
		repos, ok := chunk["repos"].([]map[string]any)
		if !ok || len(repos) != 1 {
			t.Fatalf("repository chunk repos = %#v", chunk["repos"])
		}
		repo, _ := repos[0]["repo"].(string)
		seenRepos[repo] = true
		if len(chunk["files"].([]map[string]any)) != 1 {
			t.Fatalf("repository chunk files = %#v", chunk["files"])
		}
	}
	if !seenRepos[firstRoot] || !seenRepos[secondRoot] {
		t.Fatalf("stream repositories = %#v", seenRepos)
	}
	if payload != nil {
		t.Fatalf("cancelled stream payload = %#v, want nil", payload)
	}
}

func TestSessionFilesWithAgentsStreamEmitsInitialChunkBeforeRefEnrichment(t *testing.T) {
	isolateSessionFilesTestHome(t)
	root, _ := newCommittedTestRepo(t)
	if err := os.WriteFile(filepath.Join(root, "changed.txt"), []byte("changed\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	refLogStarted := make(chan struct{})
	refLogRelease := make(chan struct{})
	var releaseOnce sync.Once
	releaseRefLog := func() { releaseOnce.Do(func() { close(refLogRelease) }) }
	t.Cleanup(releaseRefLog)
	originalExec := execCommandContext
	t.Cleanup(func() { execCommandContext = originalExec })
	var startedOnce sync.Once
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		for _, arg := range args {
			if arg == "--max-count=200" {
				startedOnce.Do(func() { close(refLogStarted) })
				select {
				case <-refLogRelease:
				case <-ctx.Done():
				}
				break
			}
		}
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}

	firstChunk := make(chan map[string]any, 1)
	streamDone := make(chan struct{})
	streamContext, cancelStream := context.WithCancel(context.Background())
	defer cancelStream()
	var payload map[string]any
	var finderErr *Error
	go func() {
		payload, finderErr = service.SessionFilesWithAgentsStream(streamContext, "s1", []string{root}, nil, 24, "", "", nil, func(chunk map[string]any) error {
			select {
			case firstChunk <- chunk:
			default:
			}
			return nil
		})
		close(streamDone)
	}()

	select {
	case <-refLogStarted:
		releaseRefLog()
		cancelStream()
		<-streamDone
		t.Fatalf("Git ref enrichment started before the first repository chunk")
	case chunk := <-firstChunk:
		repos, ok := chunk["repos"].([]map[string]any)
		if !ok || len(repos) != 1 || repos[0]["branch"] != "" {
			releaseRefLog()
			cancelStream()
			<-streamDone
			t.Fatalf("initial repository chunk = %#v, want one repository with deferred Git metadata", chunk)
		}
		files, ok := chunk["files"].([]map[string]any)
		if !ok || len(files) != 1 || files[0]["mtime"] != int64(0) || files[0]["size"] != nil {
			releaseRefLog()
			cancelStream()
			<-streamDone
			t.Fatalf("initial file metadata = %#v, want deferred filesystem fields", chunk["files"])
		}
		releaseRefLog()
		cancelStream()
		<-streamDone
		if finderErr == nil || finderErr.Code != "cancelled" || payload != nil {
			t.Fatalf("cancelled stream result = payload %#v, error %#v", payload, finderErr)
		}
	}
}

func TestSessionFilesStagedEnrichmentAdvancesRepositoriesIndependently(t *testing.T) {
	service := &Service{}
	slowFilesStarted := make(chan struct{})
	releaseSlowFiles := make(chan struct{})
	fastGitStarted := make(chan struct{})
	var slowFilesOnce sync.Once
	var fastGitOnce sync.Once
	views := []sessionFilesRepositoryView{
		{repo: map[string]any{"repo": "/slow"}, files: []map[string]any{{"path": "slow.txt"}}},
		{repo: map[string]any{"repo": "/fast"}, files: []map[string]any{{"path": "fast.txt"}}},
	}
	stages := []sessionFilesViewEnricher{
		func(ctx context.Context, view sessionFilesRepositoryView, _ *gitHistoryScope) sessionFilesRepositoryView {
			if sessionFilesRepositoryPath(view) == "/slow" {
				slowFilesOnce.Do(func() { close(slowFilesStarted) })
				select {
				case <-releaseSlowFiles:
				case <-ctx.Done():
				}
			}
			view.repo["filesystem_ready"] = true
			return view
		},
		func(_ context.Context, view sessionFilesRepositoryView, _ *gitHistoryScope) sessionFilesRepositoryView {
			if sessionFilesRepositoryPath(view) == "/fast" {
				fastGitOnce.Do(func() { close(fastGitStarted) })
			}
			view.repo["git_ready"] = true
			return view
		},
	}
	type event struct {
		repo  string
		stage string
	}
	events := make([]event, 0, 4)
	done := make(chan error, 1)
	go func() {
		done <- service.enrichSessionFilesViewsStagedBounded(context.Background(), views, stages, func(_ int, view sessionFilesRepositoryView) error {
			repo := sessionFilesRepositoryPath(view)
			stage := "filesystem"
			if view.repo["git_ready"] == true {
				stage = "git"
			}
			events = append(events, event{repo: repo, stage: stage})
			return nil
		})
	}()
	select {
	case <-slowFilesStarted:
	case <-time.After(2 * time.Second):
		t.Fatal("slow repository did not enter filesystem enrichment")
	}
	select {
	case <-fastGitStarted:
	case <-time.After(2 * time.Second):
		close(releaseSlowFiles)
		<-done
		t.Fatalf("fast repository did not reach Git enrichment while slow filesystem enrichment was blocked; events=%#v", events)
	}
	close(releaseSlowFiles)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	seen := make(map[string][]string)
	for _, item := range events {
		seen[item.repo] = append(seen[item.repo], item.stage)
	}
	if got := strings.Join(seen["/fast"], ","); got != "filesystem,git" {
		t.Fatalf("fast repository stages = %q, want filesystem,git; events=%#v", got, events)
	}
	if got := strings.Join(seen["/slow"], ","); got != "filesystem,git" {
		t.Fatalf("slow repository stages = %q, want filesystem,git; events=%#v", got, events)
	}
}

func TestSessionFilesStagedEnrichmentStopsEmittingAfterCancellation(t *testing.T) {
	service := &Service{}
	views := []sessionFilesRepositoryView{
		{repo: map[string]any{"repo": "/one"}},
		{repo: map[string]any{"repo": "/two"}},
		{repo: map[string]any{"repo": "/three"}},
	}
	stageDone := make(chan struct{}, len(views))
	stages := []sessionFilesViewEnricher{
		func(_ context.Context, view sessionFilesRepositoryView, _ *gitHistoryScope) sessionFilesRepositoryView {
			stageDone <- struct{}{}
			return view
		},
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	emitted := 0
	err := service.enrichSessionFilesViewsStagedBounded(ctx, views, stages, func(_ int, _ sessionFilesRepositoryView) error {
		emitted++
		for range len(views) {
			<-stageDone
		}
		cancel()
		return nil
	})
	if err != context.Canceled {
		t.Fatalf("enrichment error = %v, want context cancellation", err)
	}
	if emitted != 1 {
		t.Fatalf("emitted %d staged chunks after cancellation, want only the first", emitted)
	}
}

func TestSessionFilesStreamRecomputesAttributionOncePerRepository(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	root, _ := newCommittedTestRepo(t)
	parent := filepath.Dir(root)
	second := filepath.Join(root, "nested", "second.txt")
	nested := filepath.Join(root, "nested", "old.txt")
	agentCWD := filepath.Join(parent, "agent")
	if err := os.MkdirAll(filepath.Dir(nested), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(agentCWD, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(second, []byte("second\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(nested, []byte("nested\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "--", "nested/second.txt", "nested/old.txt"}, {"-C", root, "commit", "-qm", "add nested files"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %s: %v (%s)", strings.Join(args, " "), err, output)
		}
	}
	if err := os.RemoveAll(filepath.Dir(nested)); err != nil {
		t.Fatal(err)
	}
	transcript := filepath.Join(t.TempDir(), "claude.jsonl")
	agent := SessionFileAgent{Kind: "claude", CWD: agentCWD, Transcript: transcript}
	lines := []string{
		`{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"` + nested + `"}}]}}`,
		`{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"` + second + `"}}]}}`,
	}
	if err := os.WriteFile(transcript, []byte(strings.Join(lines, "\n")+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if attributed, ok := scanSessionTranscript(transcript, agent, time.Now().Add(-24*time.Hour)); !ok || len(attributed) != 2 {
		t.Fatalf("transcript fixture attributions = %#v, parsed=%t", attributed, ok)
	}
	if attributed := collectSessionFileAttributions(context.Background(), []SessionFileAgent{agent}, []string{parent}, 24); len(attributed) != 2 {
		t.Fatalf("session attribution scan = %#v, want both transcript paths", attributed)
	}
	service, err := New(parent, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	combinedDiffCalls := 0
	originalExec := execCommandContext
	execCommandContext = func(ctx context.Context, args []string, extraFiles []*os.File) *exec.Cmd {
		isDiff, hasRaw, hasNumstat := false, false, false
		for _, arg := range args {
			isDiff = isDiff || arg == "diff"
			hasRaw = hasRaw || arg == "--raw"
			hasNumstat = hasNumstat || arg == "--numstat"
		}
		if isDiff && hasRaw && hasNumstat {
			combinedDiffCalls++
		}
		command := exec.CommandContext(ctx, "git", args...)
		command.ExtraFiles = extraFiles
		command.Env = gitCommandEnvironment()
		return command
	}
	t.Cleanup(func() { execCommandContext = originalExec })

	chunks := make([]map[string]any, 0, 1)
	payload, finderErr := service.SessionFilesWithAgentsStream(context.Background(), "s1", []string{parent}, []SessionFileAgent{agent}, 24, "", "", nil, func(chunk map[string]any) error {
		chunks = append(chunks, chunk)
		return nil
	})
	if finderErr != nil {
		t.Fatalf("session-files stream: %#v", finderErr)
	}
	if combinedDiffCalls != 1 || len(chunks) != 3 || chunks[0]["replace_repo"] != false || chunks[1]["replace_repo"] != true || chunks[2]["replace_repo"] != true {
		t.Fatalf("combined repository diff commands/stages = %d/%#v, want one initial plus two staged repository views; files=%#v", combinedDiffCalls, chunks, payload["files"])
	}
	for index, chunk := range chunks {
		if _, present := chunk["repo_index"]; present {
			t.Fatalf("chunk %d exposes unused repo_index metadata: %#v", index, chunk)
		}
		if _, present := chunk["repo_total"]; present {
			t.Fatalf("chunk %d exposes unused repo_total metadata: %#v", index, chunk)
		}
	}
	rows := sessionFileRows(payload)
	deleted := rows["nested/old.txt"]
	if len(rows) != 2 || deleted["status"] != "D" || deleted["agent"] != "claude" || rows["nested/second.txt"]["agent"] != "claude" {
		t.Fatalf("attributed repository rows = %#v", payload["files"])
	}
}

func TestSessionFilesStreamReplacesEachAttributedRepositoryOnce(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	root, _ := newCommittedTestRepo(t)
	second := filepath.Join(root, "second.txt")
	if err := os.WriteFile(second, []byte("two\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "second.txt"}, {"-C", root, "commit", "-qm", "second"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte("changed\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(second, []byte("changed too\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	transcript := filepath.Join(t.TempDir(), "rollout.jsonl")
	transcriptData := fmt.Sprintf(`{"msg":%q}\n`, "*** Begin Patch\n*** Update File: "+filepath.Join(root, "note.txt")+"\n*** Update File: "+second+"\n")
	if err := os.WriteFile(transcript, []byte(transcriptData), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	chunks := make([]map[string]any, 0, 4)
	_, finderErr := service.SessionFilesWithAgentsStream(context.Background(), "s1", []string{root}, []SessionFileAgent{{
		Kind: "codex", CWD: root, Transcript: transcript,
	}}, 24, "", "", nil, func(chunk map[string]any) error {
		chunks = append(chunks, chunk)
		return nil
	})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	if len(chunks) != 4 {
		t.Fatalf("stream chunks = %d, want initial plus metadata, Git-ref, and attribution replacements: %#v", len(chunks), chunks)
	}
	for index := 1; index < len(chunks); index++ {
		if chunks[index]["replace_repo"] != true {
			t.Fatalf("replacement chunk %d = %#v, want replace_repo=true", index, chunks[index])
		}
	}
	for index, chunk := range chunks {
		if _, present := chunk["repo_index"]; present {
			t.Fatalf("chunk %d exposes unused repo_index progress metadata: %#v", index, chunk)
		}
		if _, present := chunk["repo_total"]; present {
			t.Fatalf("chunk %d exposes unused repo_total progress metadata: %#v", index, chunk)
		}
	}
}

func TestSessionFilesWithAgentsStreamEmitsEarlierRepositoryBeforeLaterDiscoveryFails(t *testing.T) {
	isolateSessionFilesTestHome(t)
	firstRoot, _ := newCommittedTestRepo(t)
	missingRoot := filepath.Join(firstRoot, "does-not-exist")
	service, err := New(firstRoot, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	chunks := make([]map[string]any, 0, 1)
	payload, finderErr := service.SessionFilesWithAgentsStream(context.Background(), "s1", []string{firstRoot, missingRoot}, nil, 24, "", "", nil, func(chunk map[string]any) error {
		chunks = append(chunks, chunk)
		return nil
	})
	if finderErr == nil || finderErr.Code != "path_not_found" {
		t.Fatalf("stream error = %#v, want path_not_found after the first repository", finderErr)
	}
	if len(chunks) != 1 {
		t.Fatalf("stream chunks = %d, want the first repository before the later failure: %#v", len(chunks), chunks)
	}
	if payload != nil {
		t.Fatalf("failed stream payload = %#v, want nil", payload)
	}
	repos, ok := chunks[0]["repos"].([]map[string]any)
	if !ok || len(repos) != 1 || repos[0]["repo"] != firstRoot {
		t.Fatalf("first stream repository = %#v, want %q", chunks[0]["repos"], firstRoot)
	}
}

func TestSessionFilesWithAgentsExcludesDefaultIgnoredTranscriptPaths(t *testing.T) {
	isolateSessionFilesTestHome(t)
	root, _ := newCommittedTestRepo(t)
	visible := filepath.Join(root, "visible.txt")
	ignored := filepath.Join(root, ".cache", "hidden.txt")
	if err := os.MkdirAll(filepath.Dir(ignored), 0o700); err != nil {
		t.Fatal(err)
	}
	for path := range map[string]bool{visible: true, ignored: true} {
		if err := os.WriteFile(path, []byte("changed\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	transcript := filepath.Join(t.TempDir(), "claude.jsonl")
	line := `{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"` + visible + `"}},{"type":"tool_use","name":"Edit","input":{"file_path":"` + ignored + `"}}]}}` + "\n"
	if err := os.WriteFile(transcript, []byte(line), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	payload, finderErr := service.SessionFilesWithAgents(context.Background(), "s1", []string{root}, []SessionFileAgent{{Kind: "claude", CWD: root, Transcript: transcript}}, 24, "", "", nil)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	rows := sessionFileRows(payload)
	if rows["visible.txt"] == nil || rows[".cache/hidden.txt"] != nil {
		t.Fatalf("ignored transcript rows = %#v", payload)
	}
}

func TestSessionFilesPreservesDeletedRowsAndRefFallback(t *testing.T) {
	isolateSessionFilesTestHome(t)
	root, _ := newCommittedTestRepo(t)
	if err := os.WriteFile(filepath.Join(root, "stable.txt"), []byte("unchanged\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "--", "stable.txt"}, {"-C", root, "commit", "-qm", "add stable file"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	deleted := filepath.Join(root, "note.txt")
	if err := os.Remove(deleted); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "new.txt"), []byte("untracked\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()

	payload, finderErr := service.SessionFiles(context.Background(), "agent-2", []string{root}, 24, "missing-ref", "current", nil)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	rows := sessionFileRows(payload)
	row := rows["note.txt"]
	if row == nil || row["status"] != "D" || row["missing"] != true || row["diff_tracked"] != true {
		t.Fatalf("deleted row = %#v", payload)
	}
	if rows["new.txt"] == nil || rows["new.txt"]["status"] != "?" {
		t.Fatalf("invalid-ref fallback did not include untracked files: %#v", payload["files"])
	}
	if rows["stable.txt"] != nil {
		t.Fatalf("invalid-ref fallback reported unchanged tracked file: %#v", rows["stable.txt"])
	}
	repos := payload["repos"].([]map[string]any)
	if len(repos) != 1 || repos[0]["error"] != "requested refs not found in this repo; showing default" || repos[0]["from_ref"] != "default" || repos[0]["to_ref"] != "base" {
		t.Fatalf("fallback repository = %#v", payload["repos"])
	}
	if repos[0]["comparison_from_ref"] == "" || repos[0]["comparison_from_ref"] == "default" || repos[0]["comparison_to_ref"] != "current" {
		t.Fatalf("fallback comparison refs = %#v", repos[0])
	}
}

func TestSessionFilesHandlesNoHistoryAndAllowsAnyRepositoryPath(t *testing.T) {
	isolateSessionFilesTestHome(t)
	root := t.TempDir()
	if output, err := exec.Command("git", "init", "-q", root).CombinedOutput(); err != nil {
		t.Fatalf("git init: %v (%s)", err, output)
	}
	if err := os.WriteFile(filepath.Join(root, "first.txt"), []byte("first\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	payload, finderErr := service.SessionFiles(context.Background(), "new", []string{root}, 24, "", "", nil)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	rows := sessionFileRows(payload)
	if rows["first.txt"]["status"] != "?" || payload["repos"].([]map[string]any)[0]["count"] != 1 {
		t.Fatalf("no-history payload = %#v", payload)
	}
	outside := filepath.Join(t.TempDir(), "outside")
	if output, err := exec.Command("git", "init", "-q", outside).CombinedOutput(); err != nil {
		t.Fatalf("outside git init: %v (%s)", err, output)
	}
	if err := os.WriteFile(filepath.Join(outside, "outside.txt"), []byte("outside\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	outsidePayload, finderErr := service.SessionFiles(context.Background(), "new", []string{outside}, 24, "", "", nil)
	if finderErr != nil {
		t.Fatalf("outside repository error = %#v", finderErr)
	}
	outsideRows := sessionFileRows(outsidePayload)
	if outsideRows["outside.txt"]["status"] != "?" {
		t.Fatalf("outside repository payload = %#v", outsidePayload)
	}
}

func TestSessionFilesCountsUntrackedLinesAndLeavesBinaryNumstatNil(t *testing.T) {
	isolateSessionFilesTestHome(t)
	root, _ := newCommittedTestRepo(t)
	if err := os.WriteFile(filepath.Join(root, "untracked.txt"), []byte("one\ntwo\nthree"), 0o600); err != nil {
		t.Fatal(err)
	}
	binaryPath := filepath.Join(root, "binary.dat")
	if err := os.WriteFile(binaryPath, []byte("before\x00"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{{"-C", root, "add", "binary.dat"}, {"-C", root, "commit", "-qm", "binary"}} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	if err := os.WriteFile(binaryPath, []byte("after\x00"), 0o600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	payload, finderErr := service.SessionFiles(context.Background(), "s1", []string{root}, 24, "HEAD", "current", nil)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	rows := sessionFileRows(payload)
	if rows["untracked.txt"]["status"] != "?" || rows["untracked.txt"]["added"] != 3 || rows["untracked.txt"]["removed"] != 0 || rows["untracked.txt"]["diff_tracked"] != false {
		t.Fatalf("untracked row = %#v", rows["untracked.txt"])
	}
	if rows["binary.dat"]["status"] != "M" || rows["binary.dat"]["added"] != nil || rows["binary.dat"]["removed"] != nil {
		t.Fatalf("binary row = %#v", rows["binary.dat"])
	}
}

func TestSessionFilesAppliesCanonicalPerRepositoryRefsAndAheadBehind(t *testing.T) {
	isolateSessionFilesTestHome(t)
	root, first := newCommittedTestRepo(t)
	tracked := filepath.Join(root, "note.txt")
	if err := os.WriteFile(tracked, []byte("two\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("git", "-C", root, "commit", "-qam", "second").CombinedOutput(); err != nil {
		t.Fatalf("second commit: %v (%s)", err, output)
	}
	secondBytes, err := exec.Command("git", "-C", root, "rev-parse", "HEAD").Output()
	if err != nil {
		t.Fatal(err)
	}
	second := strings.TrimSpace(string(secondBytes))
	alias := filepath.Join(t.TempDir(), "repo-alias")
	if err := os.Symlink(root, alias); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	payload, finderErr := service.SessionFiles(context.Background(), "s1", []string{root}, 24, "", "", map[string]map[string]string{
		alias: {"from": first, "to": second},
	})
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	repos := payload["repos"].([]map[string]any)
	if len(repos) != 1 || repos[0]["from_ref"] != first || repos[0]["to_ref"] != second || repos[0]["ahead"] != 1 || repos[0]["behind"] != 0 {
		t.Fatalf("canonical refs repository = %#v", repos)
	}
	if len(payload["files"].([]map[string]any)) != 1 {
		t.Fatalf("explicit ref files = %#v", payload["files"])
	}
}

func TestSessionFilesNormalizesLookbackBounds(t *testing.T) {
	isolateSessionFilesTestHome(t)
	root, _ := newCommittedTestRepo(t)
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	for _, test := range []struct {
		input, want float64
	}{
		{0, 0.25},
		{0.01, 0.25},
		{24 * 365, 24 * 14},
	} {
		payload, finderErr := service.SessionFiles(context.Background(), "s1", []string{root}, test.input, "", "", nil)
		if finderErr != nil {
			t.Fatal(finderErr)
		}
		if payload["hours"] != test.want {
			t.Fatalf("hours %v = %#v, want %v", test.input, payload["hours"], test.want)
		}
	}
}

func TestSessionFilesExposesRecentRefsAndAliases(t *testing.T) {
	isolateSessionFilesTestHome(t)
	root, _ := newCommittedTestRepo(t)
	for index := 0; index < 24; index++ {
		if err := os.WriteFile(filepath.Join(root, "note.txt"), []byte(fmt.Sprintf("revision %d\n", index)), 0o600); err != nil {
			t.Fatal(err)
		}
		if output, err := exec.Command("git", "-C", root, "commit", "-qam", "revision").CombinedOutput(); err != nil {
			t.Fatalf("revision commit: %v (%s)", err, output)
		}
	}
	for _, args := range [][]string{
		{"-C", root, "branch", "-M", "main"},
		{"-C", root, "branch", "same-head"},
		{"-C", root, "update-ref", "refs/remotes/origin/main", "HEAD"},
		{"-C", root, "update-ref", "refs/remotes/origin/topic", "HEAD"},
	} {
		if output, err := exec.Command("git", args...).CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v (%s)", args, err, output)
		}
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	payload, finderErr := service.SessionFiles(context.Background(), "s1", []string{root}, 24, "", "", nil)
	if finderErr != nil {
		t.Fatal(finderErr)
	}
	refs := payload["refs_by_repo"].(map[string]any)[root].([]map[string]any)
	if len(refs) < 27 || refs[0]["ref"] != "HEAD" || refs[1]["ref"] != "current" {
		t.Fatalf("recent refs length/order = %#v", refs)
	}
	aliases, ok := refs[0]["aliases"].([]string)
	if !ok || len(aliases) == 0 || aliases[0] != "HEAD" {
		t.Fatalf("head aliases = %#v", refs[0])
	}
	for _, alias := range []string{"origin/main", "origin/topic", "main", "same-head"} {
		found := false
		for _, candidate := range aliases {
			if candidate == alias {
				found = true
				break
			}
		}
		if !found {
			t.Fatalf("head aliases missing %q: %#v", alias, aliases)
		}
	}
}
