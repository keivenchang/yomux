// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func isolateSessionFilesTestHome(t *testing.T) {
	t.Helper()
	t.Setenv("HOME", t.TempDir())
}

func TestHistoricalCodexTranscriptCandidatesSelectNewestBoundedSet(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "sessions", "2026", "09", "23")
	if err := os.MkdirAll(archive, 0o700); err != nil {
		t.Fatal(err)
	}
	cutoff := time.Now().Add(-time.Hour)
	base := time.Now().Add(-time.Minute)
	newest := filepath.Join(archive, "rollout-zzz.jsonl")
	for index := 0; index <= maxHistoricalCodexFiles; index++ {
		name := fmt.Sprintf("rollout-%03d.jsonl", index)
		if index == maxHistoricalCodexFiles {
			name = "rollout-zzz.jsonl"
		}
		path := filepath.Join(archive, name)
		if err := os.WriteFile(path, []byte(`{"type":"response_item"}`+"\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		modified := base.Add(time.Duration(index) * time.Second)
		if err := os.Chtimes(path, modified, modified); err != nil {
			t.Fatal(err)
		}
	}

	transcripts, complete := historicalCodexTranscriptCandidates(context.Background(), archive, cutoff)
	if !complete {
		t.Fatal("complete test archive reported an incomplete scan")
	}
	if len(transcripts) != maxHistoricalCodexFiles {
		t.Fatalf("selected historical transcript count = %d, want %d", len(transcripts), maxHistoricalCodexFiles)
	}
	if len(transcripts) == 0 || transcripts[0].path != newest {
		t.Fatalf("newest transcript was excluded from bounded selection: first=%q newest=%q", transcripts[0].path, newest)
	}
}

func TestHistoricalCodexTranscriptCandidateTieBreaksByPath(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "sessions")
	if err := os.MkdirAll(archive, 0o700); err != nil {
		t.Fatal(err)
	}
	modified := time.Now().Add(-time.Minute)
	for index := 0; index <= maxHistoricalCodexFiles; index++ {
		path := filepath.Join(archive, fmt.Sprintf("rollout-%03d.jsonl", index))
		if err := os.WriteFile(path, []byte("{}\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.Chtimes(path, modified, modified); err != nil {
			t.Fatal(err)
		}
	}

	candidates, complete := historicalCodexTranscriptCandidates(context.Background(), archive, time.Time{})
	if !complete || len(candidates) != maxHistoricalCodexFiles {
		t.Fatalf("candidate window = %d entries, complete=%t; want %d complete entries", len(candidates), complete, maxHistoricalCodexFiles)
	}
	if got, want := filepath.Base(candidates[0].path), "rollout-000.jsonl"; got != want {
		t.Fatalf("first tied candidate = %q, want %q", got, want)
	}
	if got, want := filepath.Base(candidates[len(candidates)-1].path), "rollout-255.jsonl"; got != want {
		t.Fatalf("last tied candidate = %q, want %q", got, want)
	}
}

func TestHistoricalCodexTranscriptIsOpenedOnceAcrossSessionCWDs(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "sessions", "2026", "09", "23")
	if err := os.MkdirAll(archive, 0o700); err != nil {
		t.Fatal(err)
	}
	firstCWD := filepath.Join(t.TempDir(), "first")
	secondCWD := filepath.Join(t.TempDir(), "second")
	for _, cwd := range []string{firstCWD, secondCWD} {
		if err := os.MkdirAll(cwd, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	transcript := []byte(`{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":{"cmd":"git add note.txt"}}}` + "\n")
	path := filepath.Join(archive, "rollout-current.jsonl")
	if err := os.WriteFile(path, transcript, 0o600); err != nil {
		t.Fatal(err)
	}
	modified := time.Now()
	if err := os.Chtimes(path, modified, modified); err != nil {
		t.Fatal(err)
	}
	explicitTranscript := []byte(`{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":{"cmd":"git add explicit.txt","workdir":"` + secondCWD + `"}}}` + "\n")
	explicitPath := filepath.Join(archive, "rollout-explicit.jsonl")
	if err := os.WriteFile(explicitPath, explicitTranscript, 0o600); err != nil {
		t.Fatal(err)
	}
	explicitModified := modified.Add(-time.Second)
	if err := os.Chtimes(explicitPath, explicitModified, explicitModified); err != nil {
		t.Fatal(err)
	}
	openCalls := 0
	parsedBytes := 0
	openFile := func(path string) (io.ReadCloser, error) {
		openCalls++
		data, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		parsedBytes += len(data)
		return io.NopCloser(bytes.NewReader(data)), nil
	}
	transcripts := newHistoricalCodexAttributionCache()
	walkCalls := 0
	archiveVisits := 0
	walk := func(root string, visit fs.WalkDirFunc) error {
		walkCalls++
		return filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
			archiveVisits++
			return visit(path, entry, err)
		})
	}
	collect := func(cwds []string) map[string]*sessionFileAttribution {
		return collectHistoricalCodexAttributionsWithWalker(context.Background(), transcripts, archive, cwds, time.Now().Add(-time.Hour), map[string]bool{}, openFile, walk)
	}
	changes := collect([]string{firstCWD, secondCWD})
	wantParsedBytes := len(transcript) + len(explicitTranscript)
	if openCalls != 2 || parsedBytes != wantParsedBytes {
		t.Fatalf("historical archive reads = %d, parsed bytes = %d, want two opens and %d bytes", openCalls, parsedBytes, wantParsedBytes)
	}
	if walkCalls != 1 || archiveVisits != 3 {
		t.Fatalf("historical archive enumeration = %d walks, %d visits; want one walk and three visits for two CWDs", walkCalls, archiveVisits)
	}
	if changes[filepath.Join(firstCWD, "note.txt")] == nil {
		t.Fatalf("historical relative edit was not assigned to the first matching CWD: %#v", changes)
	}
	if changes[filepath.Join(secondCWD, "note.txt")] != nil {
		t.Fatalf("relative edit was incorrectly duplicated into another CWD: %#v", changes)
	}
	if changes[filepath.Join(secondCWD, "explicit.txt")] == nil {
		t.Fatalf("explicit workdir edit did not select its matching CWD: %#v", changes)
	}
	firstPath := filepath.Join(firstCWD, "note.txt")
	changes[firstPath].windows[0]["kind"] = "caller mutation"
	cachedChanges := collect([]string{firstCWD, secondCWD})
	if openCalls != 2 || parsedBytes != wantParsedBytes || cachedChanges[firstPath] == nil {
		t.Fatalf("unchanged historical scan reopened transcripts: opens=%d bytes=%d changes=%#v", openCalls, parsedBytes, cachedChanges)
	}
	if walkCalls != 2 || archiveVisits != 6 {
		t.Fatalf("second historical collection enumeration = %d walks, %d visits; want one walk for this collection", walkCalls, archiveVisits)
	}
	if got := stringValue(cachedChanges[firstPath].windows[0]["kind"]); got != "codex" {
		t.Fatalf("caller mutation changed cached historical attribution: kind=%q", got)
	}
	thirdCWD := filepath.Join(t.TempDir(), "third")
	if err := os.MkdirAll(thirdCWD, 0o700); err != nil {
		t.Fatal(err)
	}
	otherCWDChanges := collect([]string{thirdCWD, firstCWD, secondCWD})
	if openCalls != 2 || parsedBytes != wantParsedBytes || otherCWDChanges[filepath.Join(thirdCWD, "note.txt")] == nil {
		t.Fatalf("new CWD projection reopened or missed cached transcripts: opens=%d bytes=%d changes=%#v", openCalls, parsedBytes, otherCWDChanges)
	}
	updatedTranscript := append(transcript, []byte(`{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":{"cmd":"git add newer.txt"}}}`+"\n")...)
	if err := os.WriteFile(path, updatedTranscript, 0o600); err != nil {
		t.Fatal(err)
	}
	updatedTime := modified.Add(time.Second)
	if err := os.Chtimes(path, updatedTime, updatedTime); err != nil {
		t.Fatal(err)
	}
	updatedChanges := collect([]string{firstCWD, secondCWD})
	if openCalls != 3 || updatedChanges[filepath.Join(firstCWD, "newer.txt")] == nil {
		t.Fatalf("updated transcript was not rescanned: opens=%d changes=%#v", openCalls, updatedChanges)
	}
}

func TestHistoricalCodexRecordedCWDWinsOverFallbackOrder(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "sessions")
	if err := os.MkdirAll(archive, 0o700); err != nil {
		t.Fatal(err)
	}
	firstCWD := filepath.Join(t.TempDir(), "first")
	recordedCWD := filepath.Join(t.TempDir(), "recorded")
	for _, cwd := range []string{firstCWD, recordedCWD} {
		if err := os.MkdirAll(cwd, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	path := filepath.Join(archive, "rollout-recorded.jsonl")
	transcript := strings.Join([]string{
		`{"type":"session_meta","payload":{"cwd":"` + recordedCWD + `"}}`,
		`{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":{"cmd":"git add note.txt"}}}`,
	}, "\n") + "\n"
	if err := os.WriteFile(path, []byte(transcript), 0o600); err != nil {
		t.Fatal(err)
	}

	changes := collectHistoricalCodexAttributionsWithCache(
		context.Background(), newHistoricalCodexAttributionCache(), archive,
		[]string{firstCWD, recordedCWD}, time.Time{}, map[string]bool{},
		func(path string) (io.ReadCloser, error) { return os.Open(path) },
	)
	if changes[filepath.Join(recordedCWD, "note.txt")] == nil {
		t.Fatalf("recorded CWD attribution missing: %#v", changes)
	}
	if changes[filepath.Join(firstCWD, "note.txt")] != nil {
		t.Fatalf("fallback CWD won over recorded transcript CWD: %#v", changes)
	}
}

func TestHistoricalCodexRecordedCWDOutsideSessionRootsIsIgnored(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "sessions")
	if err := os.MkdirAll(archive, 0o700); err != nil {
		t.Fatal(err)
	}
	sessionCWD := filepath.Join(t.TempDir(), "session")
	otherCWD := filepath.Join(t.TempDir(), "other")
	for _, cwd := range []string{sessionCWD, otherCWD} {
		if err := os.MkdirAll(cwd, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	path := filepath.Join(archive, "rollout-other-session.jsonl")
	transcript := strings.Join([]string{
		`{"type":"session_meta","payload":{"cwd":"` + otherCWD + `"}}`,
		`{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":{"cmd":"git add note.txt"}}}`,
	}, "\n") + "\n"
	if err := os.WriteFile(path, []byte(transcript), 0o600); err != nil {
		t.Fatal(err)
	}

	changes := collectHistoricalCodexAttributionsWithCache(
		context.Background(), newHistoricalCodexAttributionCache(), archive,
		[]string{sessionCWD}, time.Time{}, map[string]bool{},
		func(path string) (io.ReadCloser, error) { return os.Open(path) },
	)
	if len(changes) != 0 {
		t.Fatalf("unrelated recorded CWD leaked into session attribution: %#v", changes)
	}
}

func TestHistoricalCodexExplicitWorkdirSurvivesUnrelatedRecordedCWD(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "sessions")
	if err := os.MkdirAll(archive, 0o700); err != nil {
		t.Fatal(err)
	}
	sessionCWD := filepath.Join(t.TempDir(), "session")
	otherCWD := filepath.Join(t.TempDir(), "other")
	for _, cwd := range []string{sessionCWD, otherCWD} {
		if err := os.MkdirAll(cwd, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	path := filepath.Join(archive, "rollout-explicit.jsonl")
	transcript := strings.Join([]string{
		`{"type":"session_meta","payload":{"cwd":"` + otherCWD + `"}}`,
		`{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":{"cmd":"git add note.txt","workdir":"` + sessionCWD + `"}}}`,
	}, "\n") + "\n"
	if err := os.WriteFile(path, []byte(transcript), 0o600); err != nil {
		t.Fatal(err)
	}

	changes := collectHistoricalCodexAttributionsWithCache(
		context.Background(), newHistoricalCodexAttributionCache(), archive,
		[]string{sessionCWD}, time.Time{}, map[string]bool{},
		func(path string) (io.ReadCloser, error) { return os.Open(path) },
	)
	if changes[filepath.Join(sessionCWD, "note.txt")] == nil {
		t.Fatalf("explicit workdir attribution missing: %#v", changes)
	}
}

func TestHistoricalCodexFailedOpenDoesNotConsumeCandidate(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "sessions")
	if err := os.MkdirAll(archive, 0o700); err != nil {
		t.Fatal(err)
	}
	cwd := t.TempDir()
	path := filepath.Join(archive, "rollout-retry.jsonl")
	if err := os.WriteFile(path, []byte(`{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":{"cmd":"git add note.txt"}}}`+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	cache := newHistoricalCodexAttributionCache()
	failed := collectHistoricalCodexAttributionsWithCache(context.Background(), cache, archive, []string{cwd}, time.Time{}, seen, func(string) (io.ReadCloser, error) {
		return nil, os.ErrPermission
	})
	if len(failed) != 0 || seen[path] {
		t.Fatalf("failed candidate was consumed: changes=%#v seen=%#v", failed, seen)
	}
	retried := collectHistoricalCodexAttributionsWithCache(context.Background(), cache, archive, []string{cwd}, time.Time{}, seen, func(path string) (io.ReadCloser, error) {
		return os.Open(path)
	})
	if retried[filepath.Join(cwd, "note.txt")] == nil || !seen[path] {
		t.Fatalf("retry did not consume the completed candidate: changes=%#v seen=%#v", retried, seen)
	}
}

func TestHistoricalCodexAttributionCacheReusesFullCandidateWindow(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "sessions")
	if err := os.MkdirAll(archive, 0o700); err != nil {
		t.Fatal(err)
	}
	cwd := t.TempDir()
	openCalls := 0
	openFile := func(path string) (io.ReadCloser, error) {
		openCalls++
		return os.Open(path)
	}
	transcripts := newHistoricalCodexAttributionCache()
	for index := 0; index < maxHistoricalCodexFiles; index++ {
		path := filepath.Join(archive, fmt.Sprintf("rollout-%02d.jsonl", index))
		transcript := []byte(fmt.Sprintf(`{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":{"cmd":"git add note-%02d.txt"}}}`+"\n", index))
		if err := os.WriteFile(path, transcript, 0o600); err != nil {
			t.Fatal(err)
		}
		modified := time.Unix(1_700_000_000+int64(index), 0)
		if err := os.Chtimes(path, modified, modified); err != nil {
			t.Fatal(err)
		}
	}
	collect := func() {
		t.Helper()
		collectHistoricalCodexAttributionsWithCache(context.Background(), transcripts, archive, []string{cwd}, time.Unix(1_600_000_000, 0), map[string]bool{}, openFile)
	}
	collect()
	collect()
	if openCalls != maxHistoricalCodexFiles {
		t.Fatalf("repeated scan reopened cached candidate window: opens=%d, want %d", openCalls, maxHistoricalCodexFiles)
	}
}

func TestHistoricalCodexAttributionCacheKeepsEmptyTranscriptReads(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "sessions")
	if err := os.MkdirAll(archive, 0o700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(archive, "rollout-empty.jsonl")
	if err := os.WriteFile(path, []byte(`{"type":"message","payload":{"text":"no file operation"}}`+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	cwd := t.TempDir()
	openCalls := 0
	openFile := func(path string) (io.ReadCloser, error) {
		openCalls++
		return os.Open(path)
	}
	transcripts := newHistoricalCodexAttributionCache()
	collect := func() {
		t.Helper()
		collectHistoricalCodexAttributionsWithCache(context.Background(), transcripts, archive, []string{cwd}, time.Time{}, map[string]bool{}, openFile)
	}
	collect()
	collect()
	if openCalls != 1 {
		t.Fatalf("unchanged no-operation transcript opened %d times, want one", openCalls)
	}
}

func TestHistoricalCodexAttributionCacheKeepsLargeLowWeightTranscript(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "sessions")
	if err := os.MkdirAll(archive, 0o700); err != nil {
		t.Fatal(err)
	}
	cwd := t.TempDir()
	path := filepath.Join(archive, "rollout-large.jsonl")
	largeLine := append([]byte("{\"type\":\"message\",\"payload\":{\"text\":\""), []byte(strings.Repeat("x", 2*1024*1024))...)
	largeLine = append(largeLine, []byte("\"}}\n")...)
	operationLine := []byte("{\"type\":\"response_item\",\"payload\":{\"type\":\"function_call\",\"name\":\"exec_command\",\"arguments\":{\"cmd\":\"git add note.txt\",\"workdir\":\"" + cwd + "\"}}}\n")
	transcript := append(largeLine, operationLine...)
	if err := os.WriteFile(path, transcript, 0o600); err != nil {
		t.Fatal(err)
	}
	openCalls := 0
	openFile := func(path string) (io.ReadCloser, error) {
		openCalls++
		return os.Open(path)
	}
	transcripts := newHistoricalCodexAttributionCache()
	collect := func() map[string]*sessionFileAttribution {
		t.Helper()
		return collectHistoricalCodexAttributionsWithCache(context.Background(), transcripts, archive, []string{cwd}, time.Time{}, map[string]bool{}, openFile)
	}
	for iteration := 0; iteration < 2; iteration++ {
		changes := collect()
		if changes[filepath.Join(cwd, "note.txt")] == nil {
			t.Fatalf("large transcript attribution is missing on collection %d", iteration+1)
		}
	}
	if openCalls != 1 || transcripts.records.Len() != 1 || transcripts.bytes == 0 {
		t.Fatalf("large low-weight transcript cache = opens:%d records:%d bytes:%d; want one open and one bounded entry", openCalls, transcripts.records.Len(), transcripts.bytes)
	}
}

func TestHistoricalCodexAttributionCacheBoundsRetainedBytes(t *testing.T) {
	transcripts := newHistoricalCodexAttributionCache()
	halfBudget := int64(maxHistoricalCodexAttributionCacheBytes / 2)
	cacheInfo := func(name string, modified time.Time) os.FileInfo {
		t.Helper()
		path := filepath.Join(t.TempDir(), name)
		if err := os.WriteFile(path, []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.Chtimes(path, modified, modified); err != nil {
			t.Fatal(err)
		}
		info, err := os.Stat(path)
		if err != nil {
			t.Fatal(err)
		}
		return info
	}
	base := time.Unix(1_700_000_000, 0)
	transcripts.set("first", historicalCodexAttributionRecord{info: cacheInfo("first", base), weight: halfBudget})
	transcripts.set("second", historicalCodexAttributionRecord{info: cacheInfo("second", base.Add(time.Second)), weight: halfBudget})
	transcripts.records.Get("first")
	transcripts.set("third", historicalCodexAttributionRecord{info: cacheInfo("third", base.Add(2*time.Second)), weight: halfBudget})
	if transcripts.bytes != maxHistoricalCodexAttributionCacheBytes || transcripts.records.Len() != 2 {
		t.Fatalf("cache retained bytes/records = %d/%d, want %d/2", transcripts.bytes, transcripts.records.Len(), maxHistoricalCodexAttributionCacheBytes)
	}
	if _, ok := transcripts.records.Peek("first"); !ok {
		t.Fatal("cache evicted a recently read transcript")
	}
	if _, ok := transcripts.records.Peek("second"); ok {
		t.Fatal("cache retained the least recently used transcript")
	}
	transcripts.set("oversized", historicalCodexAttributionRecord{weight: maxHistoricalCodexAttributionCacheBytes + 1})
	if transcripts.bytes != maxHistoricalCodexAttributionCacheBytes || transcripts.records.Len() != 2 {
		t.Fatalf("oversized record changed cache bounds: bytes=%d records=%d", transcripts.bytes, transcripts.records.Len())
	}
}

type interruptedTranscriptReader struct {
	data []byte
	done bool
}

func (r *interruptedTranscriptReader) Read(buffer []byte) (int, error) {
	if !r.done {
		r.done = true
		return copy(buffer, r.data), nil
	}
	return 0, io.ErrUnexpectedEOF
}

func TestHistoricalCodexTranscriptReadErrorIsNotCached(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "sessions")
	if err := os.MkdirAll(archive, 0o700); err != nil {
		t.Fatal(err)
	}
	cwd := t.TempDir()
	transcript := []byte(`{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":{"cmd":"git add note.txt"}}}` + "\n")
	path := filepath.Join(archive, "rollout-current.jsonl")
	if err := os.WriteFile(path, transcript, 0o600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	candidate := historicalCodexTranscriptCandidate{path: path, mtime: info.ModTime(), info: info}
	cache := newHistoricalCodexAttributionCache()
	openCalls := 0
	openFile := func(path string) (io.ReadCloser, error) {
		openCalls++
		if openCalls == 1 {
			return io.NopCloser(&interruptedTranscriptReader{data: transcript}), nil
		}
		return io.NopCloser(bytes.NewReader(transcript)), nil
	}
	if got, complete := cache.scan(context.Background(), candidate, openFile); complete || len(got.operations) != 0 {
		t.Fatalf("partial transcript operations = %#v, want empty", got)
	}
	if got, complete := cache.scan(context.Background(), candidate, openFile); !complete || projectHistoricalCodexOperations(context.Background(), got.operations, cwd, info.ModTime().Unix())[filepath.Join(cwd, "note.txt")] == nil {
		t.Fatalf("complete retry did not produce the historical file attribution: %#v", got)
	}
	if openCalls != 2 {
		t.Fatalf("transcript open calls = %d, want failed read and uncached retry", openCalls)
	}
}

func TestHistoricalCodexScanStopsWhenContextCancelsDuringLargeLine(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	reader := &cancelAfterChunkReader{cancel: cancel, data: []byte(strings.Repeat("x", 256*1024))}
	_, _, complete := scanHistoricalCodexOperations(ctx, reader)
	if complete {
		t.Fatal("large transcript scan completed after cancellation")
	}
	if ctx.Err() != context.Canceled {
		t.Fatalf("scan context error = %v, want cancellation", ctx.Err())
	}
}

func TestActiveSessionTranscriptScanStopsOnCancellationAndDiscardsPartialResults(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cwd := t.TempDir()
	line := []byte(`{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Write","input":{"file_path":"partial.txt"}}]}}` + "\n")
	reader := cancelAfterChunkReader{cancel: cancel, data: append(line, []byte(strings.Repeat("x", 256*1024))...)}
	changes, complete := scanSessionTranscriptReader(ctx, &reader, SessionFileAgent{Kind: "claude", CWD: cwd}, time.Now().Unix())
	if complete || changes != nil {
		t.Fatalf("cancelled active transcript scan = (%#v, complete=%t), want no partial result", changes, complete)
	}
	if ctx.Err() != context.Canceled {
		t.Fatalf("scan context error = %v, want cancellation", ctx.Err())
	}
}

func TestActiveSessionTranscriptScanDiscardsPartialResultsOnReadError(t *testing.T) {
	cwd := t.TempDir()
	line := []byte(`{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Write","input":{"file_path":"partial.txt"}}]}}` + "\n")
	reader := &interruptedTranscriptReader{data: line}
	changes, complete := scanSessionTranscriptReader(context.Background(), reader, SessionFileAgent{Kind: "claude", CWD: cwd}, time.Now().Unix())
	if complete || changes != nil {
		t.Fatalf("failed active transcript scan = (%#v, complete=%t), want no partial result", changes, complete)
	}
}

type cancelAfterChunkReader struct {
	cancel context.CancelFunc
	data   []byte
	done   bool
}

func (reader *cancelAfterChunkReader) Read(buffer []byte) (int, error) {
	if reader.done {
		return 0, io.EOF
	}
	reader.done = true
	count := copy(buffer, reader.data)
	reader.cancel()
	return count, nil
}
