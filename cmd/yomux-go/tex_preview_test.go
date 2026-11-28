// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
	"github.com/keivenchang/yomux/internal/testsupport"
)

func TestTeXCompilerArchiveVerification(t *testing.T) {
	t.Setenv("PATH", t.TempDir())
	var archive bytes.Buffer
	gz := gzip.NewWriter(&archive)
	tarball := tar.NewWriter(gz)
	content := []byte("test executable")
	if err := tarball.WriteHeader(&tar.Header{Name: "tectonic", Mode: 0o755, Size: int64(len(content))}); err != nil {
		t.Fatal(err)
	}
	if _, err := tarball.Write(content); err != nil {
		t.Fatal(err)
	}
	if err := tarball.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gz.Close(); err != nil {
		t.Fatal(err)
	}
	s := &server{config: t.TempDir(), port: 7111}
	if err := s.publishTexCompiler(archive.Bytes(), strings.Repeat("0", 64)); err == nil {
		t.Fatal("corrupt download admitted")
	}
	if _, kind := s.texCompiler(); kind != "" {
		t.Fatalf("invalid download published compiler %s", kind)
	}
	if err := s.publishTexCompiler(archive.Bytes(), fmt.Sprintf("%x", sha256.Sum256(archive.Bytes()))); err != nil {
		t.Fatal(err)
	}
	path, kind := s.texCompiler()
	got, err := os.ReadFile(path)
	if err != nil || kind != "tectonic" || !bytes.Equal(got, content) {
		t.Fatalf("compiler publication: %q %q %v", kind, got, err)
	}
	for _, consent := range []string{`{}`, `{"consent":false}`} {
		response := httptest.NewRecorder()
		s.texCompilerInstall(response, httptest.NewRequest(http.MethodPost, "/api/tex/compiler/install", strings.NewReader(consent)))
		if response.Code != http.StatusBadRequest {
			t.Fatalf("installation without consent: %d", response.Code)
		}
	}
}

func TestTeXRoutesRequireAdminAndSameOrigin(t *testing.T) {
	for _, route := range []struct{ method, path string }{{"DELETE", "/api/tex/compiler"}, {"GET", "/api/tex/compiler"}, {"POST", "/api/tex/compiler/install"}, {"POST", "/api/tex/preview"}} {
		for _, role := range []string{"readonly", "admin"} {
			s := &server{}
			reached := false
			handler := s.protected(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { reached = true; w.WriteHeader(204) }))
			request := httptest.NewRequest(route.method, route.path, nil)
			request = request.WithContext(context.WithValue(request.Context(), authContextKey{}, authUser{username: "fixture", role: role}))
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if reached != (role == "admin") {
				t.Fatalf("%s %s role=%s status=%d", route.method, route.path, role, response.Code)
			}
			request.Header.Set("Origin", "https://other.example")
			reached = false
			handler.ServeHTTP(httptest.NewRecorder(), request)
			if reached {
				t.Fatalf("cross-origin %s admitted", route.path)
			}
		}
	}
}

func TestTeXPreviewMissingCompiler(t *testing.T) {
	t.Setenv("PATH", t.TempDir())
	s := &server{config: t.TempDir(), port: 7111}
	response := httptest.NewRecorder()
	s.texCompilerStatus(response, httptest.NewRequest("GET", "/api/tex/compiler", nil))
	if !strings.Contains(response.Body.String(), `"available":false`) || !strings.Contains(response.Body.String(), "instructions") {
		t.Fatal(response.Body.String())
	}
	response = httptest.NewRecorder()
	s.texCompilerInstall(response, httptest.NewRequest("POST", "/api/tex/compiler/install", strings.NewReader(`{"consent":false}`)))
	if response.Code != 400 {
		t.Fatal(response.Code)
	}
	if _, err := os.Stat(s.texToolsDirectory()); !os.IsNotExist(err) {
		t.Fatalf("refused install wrote tool directory: %v", err)
	}
}

func TestTeXPreviewUsesUnsavedBufferAndBoundsWork(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux subprocess contract; Darwin uses Seatbelt instead of bubblewrap")
	}
	root := t.TempDir()
	tools := t.TempDir()
	write := func(name, content string, mode os.FileMode) {
		t.Helper()
		if err := os.WriteFile(name, []byte(content), mode); err != nil {
			t.Fatal(err)
		}
	}
	write(filepath.Join(tools, "bwrap"), "#!/bin/sh\nwhile [ \"$1\" != -- ]; do shift; done\nshift\nexec \"$@\"\n", 0o700)
	write(filepath.Join(tools, "tectonic"), "#!/bin/sh\n[ \"$TECTONIC_UNTRUSTED_MODE\" = 1 ] || exit 11\n[ -z \"${YOMUX_TEST_SECRET:-}\" ] || exit 12\n[ \"$1\" = --untrusted ] || exit 13\nIFS= read -r text || true\nprintf '%%PDF-1.4\\n%s\\n' \"$text\" > \"$3/texput.pdf\"\n", 0o700)
	t.Setenv("PATH", tools)
	t.Setenv("YOMUX_TEST_SECRET", "must not reach the compiler")
	path := filepath.Join(root, "document.tex")
	write(path, "saved source", 0o600)
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	s := &server{config: t.TempDir(), port: 7111, finder: service}
	request := func(content string) *httptest.ResponseRecorder {
		t.Helper()
		body, _ := json.Marshal(map[string]string{"path": path, "content": content})
		response := httptest.NewRecorder()
		s.texPreview(response, httptest.NewRequest("POST", "/api/tex/preview", bytes.NewReader(body)))
		return response
	}
	response := request("unsaved equation")
	if response.Header().Get("Content-Type") != "application/pdf" || !strings.Contains(response.Body.String(), "unsaved equation") {
		t.Fatalf("preview=%d %s", response.Code, response.Body.String())
	}
	disk, err := os.ReadFile(path)
	if err != nil || string(disk) != "saved source" {
		t.Fatalf("preview mutated source: %s %v", disk, err)
	}
	s.texCompileMu.Lock()
	body, _ := json.Marshal(map[string]string{"path": path, "content": "queued preview"})
	queuedContext, stop := context.WithTimeout(context.Background(), 50*time.Millisecond)
	queued := httptest.NewRecorder()
	s.texPreview(queued, httptest.NewRequest("POST", "/api/tex/preview", bytes.NewReader(body)).WithContext(queuedContext))
	stop()
	s.texCompileMu.Unlock()
	if !strings.Contains(queued.Body.String(), "compile_cancelled") {
		t.Fatal(queued.Body.String())
	}
	// Hold a running engine while its successor waits for the shared compiler owner.
	write(filepath.Join(tools, "tectonic"), "#!/bin/sh\nIFS= read -r text || true\nif [ \"$text\" = held ]; then printf started > \"$3/started\"; /bin/sleep 60; fi\nprintf '%%PDF-1.4\\n%s\\n' \"$text\" > \"$3/texput.pdf\"\n", 0o700)
	runningContext, cancelRunning := context.WithCancel(context.Background())
	defer cancelRunning()
	runningDone := make(chan struct{})
	go func() {
		defer close(runningDone)
		body, _ := json.Marshal(map[string]string{"path": path, "content": "held"})
		s.texPreview(httptest.NewRecorder(), httptest.NewRequest("POST", "/api/tex/preview", bytes.NewReader(body)).WithContext(runningContext))
	}()
	deadline := time.After(5 * time.Second)
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
waitForEngine:
	for {
		started, err := filepath.Glob(filepath.Join(s.texToolsDirectory(), "preview-*", "started"))
		if err != nil {
			t.Fatal(err)
		}
		if len(started) == 1 {
			break waitForEngine
		}
		select {
		case <-deadline:
			t.Fatal("engine did not start")
		case <-ticker.C:
		}
	}
	successor := make(chan *httptest.ResponseRecorder, 1)
	go func() { successor <- request("successor") }()
	cancelRunning()
	select {
	case <-runningDone:
	case <-time.After(5 * time.Second):
		t.Fatal("running engine did not stop")
	}
	select {
	case response := <-successor:
		if !strings.Contains(response.Body.String(), "%PDF-1.4") || !strings.Contains(response.Body.String(), "successor") {
			t.Fatal(response.Body.String())
		}
	case <-time.After(5 * time.Second):
		t.Fatal("successor remained blocked")
	}
	write(filepath.Join(tools, "tectonic"), "#!/bin/sh\nprintf 'not a PDF' > \"$3/texput.pdf\"\n", 0o700)
	if response := request("invalid output"); !strings.Contains(response.Body.String(), "compile_failed") {
		t.Fatal(response.Body.String())
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := s.compileTex(ctx, filepath.Join(tools, "tectonic"), "tectonic", path, "cancelled"); err == nil {
		t.Fatal("cancelled compile succeeded")
	}
	jobs, err := filepath.Glob(filepath.Join(s.texToolsDirectory(), "preview-*"))
	if err != nil || len(jobs) != 0 {
		t.Fatalf("compile job leak: %v %v", jobs, err)
	}
	if err := os.Rename(filepath.Join(tools, "tectonic"), filepath.Join(tools, "pdftex")); err != nil {
		t.Fatal(err)
	}
	write(filepath.Join(tools, "pdftex"), "#!/bin/sh\n[ \"$1\" = -fmt=pdflatex ] || exit 21\n[ \"$2\" = -no-shell-escape ] || exit 22\nprintf pass >> passes\nprintf '%%PDF-1.4\\n' > preview.pdf\n/bin/cat preview.tex >> preview.pdf\n", 0o700)
	if err := os.Symlink("pdftex", filepath.Join(tools, "pdflatex")); err != nil {
		t.Fatal(err)
	}
	resolved, kind := s.texCompiler()
	if filepath.Base(resolved) != "pdftex" || kind != "pdflatex" {
		t.Fatalf("symlink compiler: %s %s", resolved, kind)
	}
	if response := request("LaTeX format selected"); !strings.Contains(response.Body.String(), "LaTeX format selected") {
		t.Fatal(response.Body.String())
	}
	var log texPreviewLog
	if n, err := log.Write(make([]byte, 128<<10)); err != nil || n != 128<<10 || len(log) != 64<<10 {
		t.Fatal("unbounded compiler diagnostics")
	}
	command, err := texSandboxCommand(context.Background(), "/compiler", []string{"--untrusted"}, "/job", "/cache", "/project")
	if err != nil || strings.Contains(strings.Join(command.Args, " "), "--ro-bind / /") || !strings.Contains(strings.Join(command.Args, " "), "--ro-bind /project /project") {
		t.Fatalf("sandbox: %v %v", command, err)
	}
}

func TestInteractiveTeXPreviewPreemptsSharedCompile(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux subprocess contract; Darwin uses Seatbelt instead of bubblewrap")
	}
	root := t.TempDir()
	tools := t.TempDir()
	write := func(name, content string, mode os.FileMode) {
		t.Helper()
		if err := os.WriteFile(name, []byte(content), mode); err != nil {
			t.Fatal(err)
		}
	}
	write(filepath.Join(tools, "bwrap"), "#!/bin/sh\nwhile [ \"$1\" != -- ]; do shift; done\nshift\nexec \"$@\"\n", 0o700)
	write(filepath.Join(tools, "tectonic"), "#!/bin/sh\nIFS= read -r text || true\nif [ \"$text\" = shared ]; then printf started > \"$3/started\"; /bin/sleep 60; fi\nprintf '%%PDF-1.4\\n%s\\n' \"$text\" > \"$3/texput.pdf\"\n", 0o700)
	t.Setenv("PATH", tools)
	path := filepath.Join(root, "document.tex")
	write(path, "saved source", 0o600)
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	s := &server{config: t.TempDir(), port: 7111, finder: service}
	parent, stop := context.WithCancel(context.Background())
	defer stop()
	preempted := make(chan bool, 1)
	go func() { preempted <- s.compileSharedTeX(parent, &shareDocument{Path: path}, "shared") }()
	deadline := time.After(5 * time.Second)
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
waitForSharedCompile:
	for {
		started, err := filepath.Glob(filepath.Join(s.texToolsDirectory(), "preview-*", "started"))
		if err != nil {
			t.Fatal(err)
		}
		if len(started) == 1 {
			break waitForSharedCompile
		}
		select {
		case <-deadline:
			t.Fatal("shared compile did not start")
		case <-ticker.C:
		}
	}
	body, err := json.Marshal(map[string]string{"path": path, "content": "interactive"})
	if err != nil {
		t.Fatal(err)
	}
	interactive := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		response := httptest.NewRecorder()
		s.texPreview(response, httptest.NewRequest("POST", "/api/tex/preview", bytes.NewReader(body)))
		interactive <- response
	}()
	select {
	case response := <-interactive:
		if !strings.Contains(response.Body.String(), "%PDF-1.4") || !strings.Contains(response.Body.String(), "interactive") {
			t.Fatal(response.Body.String())
		}
	case <-time.After(10 * time.Second):
		t.Fatal("interactive preview waited behind the shared compile")
	}
	select {
	case retry := <-preempted:
		if !retry {
			t.Fatal("a preempted shared compile did not ask to run again")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("shared compile did not yield")
	}
}

// A shared compile that registers after an interactive preview started waiting, but before
// the preview owns the compiler, must yield instead of taking the compiler first.
func TestSharedTeXYieldsToAWaitingInteractivePreview(t *testing.T) {
	tools := t.TempDir()
	if err := testsupport.WriteExecutable(filepath.Join(tools, "pdflatex"), []byte("#!/bin/sh\nexit 1\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", tools)
	s := &server{config: t.TempDir()}
	s.beginInteractiveTeX()
	retry := s.compileSharedTeX(context.Background(), &shareDocument{Path: filepath.Join(tools, "document.tex")}, "shared")
	if !retry {
		t.Fatal("the shared compile did not yield to the waiting preview")
	}
	if !s.texCompileMu.TryLock() {
		t.Fatal("the yielding shared compile kept the compiler")
	}
	s.texCompileMu.Unlock()
	s.endInteractiveTeX()
	s.texBackgroundMu.Lock()
	waiting, registered := s.texInteractive, len(s.texBackground)
	s.texBackgroundMu.Unlock()
	if waiting != 0 || registered != 0 {
		t.Fatalf("interactive waiters=%d registered compiles=%d after both finished", waiting, registered)
	}
}

// texCacheFixture is a document directory and a fake Tectonic that records each run, reads the
// document's files the way \input, \includegraphics, and \bibliography would, and reports them
// with --makefile-rules. Source "fail" fails, "norules" reports nothing, and "standalone" reports
// that it read no document files.
func texCacheFixture(t *testing.T) (*server, string, func() int) {
	t.Helper()
	if runtime.GOOS != "linux" {
		t.Skip("Linux subprocess contract; Darwin uses Seatbelt instead of bubblewrap")
	}
	root, tools := t.TempDir(), t.TempDir()
	runs := filepath.Join(tools, "runs")
	files := map[string]string{
		filepath.Join(tools, "bwrap"): "#!/bin/sh\nwhile [ \"$1\" != -- ]; do shift; done\nshift\nexec \"$@\"\n",
		filepath.Join(tools, "tectonic"): "#!/bin/sh\nprintf x >> " + shellQuote(runs) + "\nIFS= read -r text || true\n[ \"$text\" = fail ] && exit 3\n" +
			"{ printf '%%PDF-1.4\\n%s\\n' \"$text\"; /bin/cat part.tex sub/chapter.tex refs.bib figure.png 2>/dev/null; } > \"$3/texput.pdf\"\n" +
			"[ \"$text\" = standalone ] && { printf '%s :\\n' \"$3/texput.pdf\" > \"$7\"; exit 0; }\n" +
			"[ \"$text\" = norules ] || printf '%s : \\\\\\n  %s \\\\\\n  %s \\\\\\n  %s \\\\\\n  %s\\n' \"$3/texput.pdf\" \"$3/part.tex\" \"$3/sub/chapter.tex\" \"$3/figure.png\" \"$3/refs.bib\" > \"$7\"\n",
		filepath.Join(root, "part.tex"):           "first part\n",
		filepath.Join(root, "sub", "chapter.tex"): "chapter\n",
		filepath.Join(root, "refs.bib"):           "@misc{a, title={A}}\n",
		filepath.Join(root, "figure.png"):         "PNGA",
		filepath.Join(root, "document.tex"):       "saved\n",
	}
	for path, content := range files {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := testsupport.WriteExecutable(path, []byte(content), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("PATH", tools)
	// The fixture's files were just written; judge them as long settled.
	fileSettleClock = func() time.Time { return time.Now().Add(time.Hour) }
	t.Cleanup(func() { fileSettleClock = time.Now })
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(service.Close)
	count := func() int {
		data, err := os.ReadFile(runs)
		if errors.Is(err, os.ErrNotExist) {
			return 0
		}
		if err != nil {
			t.Fatal(err)
		}
		return len(data)
	}
	return &server{config: t.TempDir(), port: 7111, finder: service}, root, count
}

func texPreviewRequest(t *testing.T, s *server, path, content, job string) *httptest.ResponseRecorder {
	t.Helper()
	body, err := json.Marshal(map[string]string{"path": path, "content": content, "job_id": job})
	if err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	s.texPreview(response, httptest.NewRequest("POST", "/api/tex/preview", bytes.NewReader(body)))
	return response
}

func TestTeXPreviewCacheReusesOnlyUnchangedCompiles(t *testing.T) {
	s, root, runs := texCacheFixture(t)
	document := filepath.Join(root, "document.tex")
	preview := func(content string, wantRuns int, wantCache string) string {
		t.Helper()
		response := texPreviewRequest(t, s, document, content, "job-cache-0000001")
		if response.Header().Get("Content-Type") != "application/pdf" || response.Header().Get("X-YOmux-Preview-Cache") != wantCache || runs() != wantRuns {
			t.Fatalf("%q: cache=%q runs=%d (want %s, %d) %s", content, response.Header().Get("X-YOmux-Preview-Cache"), runs(), wantCache, wantRuns, response.Body.String())
		}
		if state, ok := s.texProgressSnapshot("job-cache-0000001"); !ok || state.Phase != "complete" {
			t.Fatalf("%q: progress %+v %v", content, state, ok)
		}
		return response.Body.String()
	}
	first := preview("main", 1, "miss")
	if preview("main", 1, "hit") != first {
		t.Fatal("a cached preview differs from its compile")
	}
	preview("edited main", 2, "miss")
	edit := func(name, content string, restoreTime bool) {
		t.Helper()
		path := filepath.Join(root, name)
		before, err := os.Stat(path)
		if err != nil {
			t.Fatal(err)
		}
		advanceChangeTimeTick(t)
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
		if restoreTime {
			if err := os.Chtimes(path, before.ModTime(), before.ModTime()); err != nil {
				t.Fatal(err)
			}
		}
	}
	runsSoFar := 2
	for _, change := range []struct{ name, content string }{
		{"part.tex", "second part\n"},
		{"sub/chapter.tex", "revised chapter\n"},
		{"refs.bib", "@misc{b, title={B}}\n"},
	} {
		edit(change.name, change.content, false)
		runsSoFar++
		if body := preview("main", runsSoFar, "miss"); !strings.Contains(body, strings.TrimSpace(change.content)) {
			t.Fatalf("%s edit compiled stale input: %s", change.name, body)
		}
		preview("main", runsSoFar, "hit")
	}
	// A same-size image edit that restores the modification time still forces a compile.
	edit("figure.png", "PNGB", true)
	runsSoFar++
	if body := preview("main", runsSoFar, "miss"); !strings.Contains(body, "PNGB") {
		t.Fatalf("image edit compiled the stale image: %s", body)
	}
	// Touching an input without changing its bytes keeps the cached PDF.
	advanceChangeTimeTick(t)
	later := time.Now().Add(time.Minute)
	if err := os.Chtimes(filepath.Join(root, "refs.bib"), later, later); err != nil {
		t.Fatal(err)
	}
	preview("main", runsSoFar, "hit")
	// A new file in the document directory may be one the document probed for.
	advanceChangeTimeTick(t)
	if err := os.WriteFile(filepath.Join(root, "local.cfg"), []byte("cfg\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	runsSoFar++
	preview("main", runsSoFar, "miss")
	// The same holds for the directory of an input outside the document directory's top level.
	advanceChangeTimeTick(t)
	if err := os.WriteFile(filepath.Join(root, "sub", "local.cfg"), []byte("cfg\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	runsSoFar++
	preview("main", runsSoFar, "miss")

	for _, content := range []string{"fail", "norules"} {
		for range 2 {
			runsSoFar++
			response := texPreviewRequest(t, s, document, content, "job-cache-0000002")
			if runs() != runsSoFar || response.Header().Get("X-YOmux-Preview-Cache") == "hit" {
				t.Fatalf("%q was served from the cache: runs=%d %v", content, runs(), response.Header())
			}
		}
	}
	snapshot := s.memoryCache.snapshot()["kinds"].(map[string]memoryCacheStats)[memoryCacheTeX]
	if snapshot.Hits != 5 || snapshot.Misses != 12 || snapshot.Uncacheable != 2 || snapshot.Entries == 0 || snapshot.Bytes <= 0 {
		t.Fatalf("TeX counters %+v", snapshot)
	}
}

// A shared document's background compile reuses an earlier compile of the same source.
func TestSharedTeXCompileReusesTheCache(t *testing.T) {
	s, _, runs := texCacheFixture(t)
	compiler, kind := s.texCompiler()
	for range 2 {
		pdf, err := s.compileBackgroundTeX(context.Background(), compiler, kind, "standalone")
		if err != nil || !strings.Contains(string(pdf), "standalone") {
			t.Fatalf("shared compile %q %v", pdf, err)
		}
	}
	if runs() != 1 {
		t.Fatalf("the same shared source compiled %d times", runs())
	}
	// Source that reads document files has no directory to check them against, so it is not cached.
	for range 2 {
		if _, err := s.compileBackgroundTeX(context.Background(), compiler, kind, "main"); err != nil {
			t.Fatal(err)
		}
	}
	if runs() != 3 {
		t.Fatalf("shared source with unverifiable inputs ran %d times", runs())
	}
}

// Identical requests that wait for the compiler together compile once.
func TestTeXPreviewCoalescesWaitingIdenticalRequests(t *testing.T) {
	s, root, runs := texCacheFixture(t)
	document := filepath.Join(root, "document.tex")
	s.texCompileMu.Lock()
	responses := make(chan *httptest.ResponseRecorder, 2)
	jobs := []string{"job-coalesce-000001", "job-coalesce-000002"}
	for _, job := range jobs {
		go func() { responses <- texPreviewRequest(t, s, document, "main", job) }()
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		queued := 0
		for _, job := range jobs {
			if state, ok := s.texProgressSnapshot(job); ok && state.Phase == "queued" {
				queued++
			}
		}
		if queued == len(jobs) {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("requests did not queue for the compiler")
		}
		runtime.Gosched()
	}
	s.texCompileMu.Unlock()
	caches := []string{}
	for range jobs {
		select {
		case response := <-responses:
			if response.Header().Get("Content-Type") != "application/pdf" {
				t.Fatal(response.Body.String())
			}
			caches = append(caches, response.Header().Get("X-YOmux-Preview-Cache"))
		case <-time.After(10 * time.Second):
			t.Fatal("request did not finish")
		}
	}
	slices.Sort(caches)
	if runs() != 1 || !slices.Equal(caches, []string{"hit", "miss"}) {
		t.Fatalf("runs=%d caches=%v; want one compile shared by both", runs(), caches)
	}
}

func TestTeXInputListsFromCompilers(t *testing.T) {
	directory := t.TempDir()
	rules := filepath.Join(directory, "inputs.mk")
	job, project := "/tex/job", "/home/user/paper"
	if err := os.WriteFile(rules, []byte("/tex/job/texput.pdf /tex/job/texput.aux :  \\\n  /tex/job/part.tex \\\n  /tex/job/my\\ figure.png \\\n  /tex/job/sub/a$$b.tex\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	inputs, err := texMakefileInputs(rules, job, project)
	if err != nil || !slices.Equal(inputs, []string{"/home/user/paper/part.tex", "/home/user/paper/my figure.png", "/home/user/paper/sub/a$b.tex"}) {
		t.Fatalf("makefile inputs %q %v", inputs, err)
	}
	if err := os.WriteFile(rules, []byte("/tex/job/texput.pdf : /tex/job/../escape.tex\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := texMakefileInputs(rules, job, project); err == nil {
		t.Fatal("an input outside the document directory was accepted")
	}
	if _, err := texMakefileInputs(filepath.Join(directory, "missing.mk"), job, project); err == nil {
		t.Fatal("a missing dependency report was accepted")
	}
	recorder := filepath.Join(directory, "preview.fls")
	if err := os.WriteFile(recorder, []byte("PWD /tex/job\nINPUT /usr/share/texlive/article.cls\nINPUT preview.tex\nINPUT /home/user/paper/part.tex\nINPUT /home/user/paper/part.tex\nOUTPUT preview.log\nINPUT /home/user/paper/figure.png\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	inputs, err = texRecorderInputs(recorder, job, project)
	if err != nil || !slices.Equal(inputs, []string{"/home/user/paper/part.tex", "/home/user/paper/figure.png"}) {
		t.Fatalf("recorder inputs %q %v", inputs, err)
	}
}

func TestTeXCleanupAuthorizationPreservesJob(t *testing.T) {
	for _, test := range []struct {
		role, origin string
		status       int
		removed      bool
	}{
		{"readonly", "http://example.com", 403, false},
		{"admin", "http://example.com", 204, true},
		{"admin", "https://other.example", 403, false},
	} {
		t.Run(test.role+test.origin, func(t *testing.T) {
			const job = "fixture-job-123456"
			s := &server{httpMode: true, texProgress: map[string]texProgressState{job: {Phase: "done"}}}
			request := httptest.NewRequest("DELETE", "http://example.com/api/tex/compiler?job_id="+job, nil)
			request.Header.Set("Origin", test.origin)
			request = request.WithContext(context.WithValue(request.Context(), authContextKey{}, authUser{username: "fixture", role: test.role}))
			response := httptest.NewRecorder()
			s.protected(http.HandlerFunc(s.texCompilerStatus)).ServeHTTP(response, request)
			_, exists := s.texProgress[job]
			if response.Code != test.status || exists == test.removed {
				t.Fatalf("status=%d job exists=%t", response.Code, exists)
			}
		})
	}
}

func TestTeXSourceMapIgnoresEmptyReferenceBoxes(t *testing.T) {
	const records = "SyncTeX Version:1\nInput:1:texput\nContent:\n" +
		"{1\n(1,9:1200,1300:400,500,0\n}\n" +
		"{2\ng1,33:1200,1300\n}\n" +
		"{3\n(1,8:14108797,12902310:0,623900,267389\n(1,58:1200,1300:400,500,0\n}\n"
	var compressed bytes.Buffer
	writer := gzip.NewWriter(&compressed)
	if _, err := writer.Write([]byte(records)); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "texput.synctex.gz")
	if err := os.WriteFile(path, compressed.Bytes(), 0600); err != nil {
		t.Fatal(err)
	}
	pages, err := readTexSyncPages(path)
	if err != nil {
		t.Fatal(err)
	}
	const job = "fixture-source-map"
	s := &server{config: t.TempDir(), texProgress: map[string]texProgressState{job: {SyncPages: pages}}}
	response := httptest.NewRecorder()
	s.texCompilerStatus(response, httptest.NewRequest(http.MethodGet, "/api/tex/compiler?job_id="+job+"&sync=1", nil))
	var status struct {
		Pages []texSyncPage `json:"sync_pages"`
	}
	if err := json.NewDecoder(response.Body).Decode(&status); err != nil {
		t.Fatal(err)
	}
	want := []texSyncPage{{Line: 9, Page: 1}, {Line: 33, Page: 2}, {Line: 58, Page: 3}}
	if response.Code != http.StatusOK || !slices.Equal(status.Pages, want) {
		t.Fatalf("status=%d source map=%v want=%v", response.Code, status.Pages, want)
	}
}

func TestTeXDependencyDigestRefreshesStat(t *testing.T) {
	s, root, _ := texCacheFixture(t)
	document := filepath.Join(root, "document.tex")
	texPreviewRequest(t, s, document, "main", "job-stat-refresh-1")
	dependency := filepath.Join(root, "refs.bib")
	advanceChangeTimeTick(t)
	later := time.Now().Add(time.Minute)
	if err := os.Chtimes(dependency, later, later); err != nil {
		t.Fatal(err)
	}
	texPreviewRequest(t, s, document, "main", "job-stat-refresh-1")
	current, err := os.Stat(dependency)
	if err != nil {
		t.Fatal(err)
	}
	s.memoryCache.mu.Lock()
	defer s.memoryCache.mu.Unlock()
	found := false
	for _, entry := range s.memoryCache.entries {
		meta, ok := entry.meta.(texPreviewEntry)
		if !ok {
			continue
		}
		for _, input := range meta.inputs {
			if input.path == dependency {
				found = true
				if !sameFileVersion(input.info, current) {
					t.Fatal("digest matched but dependency metadata stayed stale")
				}
			}
		}
	}
	if !found {
		t.Fatal("fixture did not cache dependency")
	}
}
