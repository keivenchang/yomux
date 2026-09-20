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
	"testing"
)

func sessionFileRows(payload map[string]any) map[string]map[string]any {
	rows := make(map[string]map[string]any)
	for _, raw := range payload["files"].([]map[string]any) {
		rows[raw["path"].(string)] = raw
	}
	return rows
}

func TestSessionFilesProjectsDefaultAndExplicitGitChanges(t *testing.T) {
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
	if len(explicitRepo) != 1 || explicitRepo[0]["from_ref"] != "HEAD" || explicitRepo[0]["to_ref"] != "current" {
		t.Fatalf("explicit repository = %#v", explicitPayload["repos"])
	}
}

func TestSessionFilesWithAgentsKeepsCleanTranscriptTouchedFilesAndWindowAttribution(t *testing.T) {
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

func TestSessionFilesWithAgentsStreamEmitsEarlierRepositoryBeforeLaterDiscoveryFails(t *testing.T) {
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
	root, _ := newCommittedTestRepo(t)
	deleted := filepath.Join(root, "note.txt")
	if err := os.Remove(deleted); err != nil {
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
	repos := payload["repos"].([]map[string]any)
	if len(repos) != 1 || repos[0]["error"] != "requested refs not found in this repo; showing default" || repos[0]["from_ref"] != "default" || repos[0]["to_ref"] != "base" {
		t.Fatalf("fallback repository = %#v", payload["repos"])
	}
}

func TestSessionFilesHandlesNoHistoryAndAllowsAnyRepositoryPath(t *testing.T) {
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
