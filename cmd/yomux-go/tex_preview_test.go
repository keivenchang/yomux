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
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
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
	for _, route := range []struct{ method, path string }{{"GET", "/api/tex/compiler"}, {"POST", "/api/tex/compiler/install"}, {"POST", "/api/tex/preview"}} {
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
