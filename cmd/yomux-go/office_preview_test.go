// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"archive/zip"
	"bytes"
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
)

func officeFixture(t *testing.T, extension string) []byte {
	t.Helper()
	var data bytes.Buffer
	archive := zip.NewWriter(&data)
	name := "word/document.xml"
	if extension == ".pptx" {
		name = "ppt/presentation.xml"
	}
	file, err := archive.Create(name)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.Write([]byte("<document/>")); err != nil {
		t.Fatal(err)
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	return data.Bytes()
}

func TestOfficeInputValidation(t *testing.T) {
	for _, ext := range []string{".docx", ".pptx"} {
		if err := validateOfficeInput(officeFixture(t, ext), ext); err != nil {
			t.Fatal(err)
		}
	}
	for _, data := range [][]byte{[]byte("not a zip"), {0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1}, officeFixture(t, ".pptx")} {
		if err := validateOfficeInput(data, ".docx"); err == nil {
			t.Fatal("invalid or encrypted input accepted")
		}
	}
}

func TestOfficePreviewAuthorizationAndLimits(t *testing.T) {
	for _, role := range []string{"readonly", "admin"} {
		s := &server{}
		reached := false
		handler := s.protected(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { reached = true }))
		request := httptest.NewRequest("POST", "/api/fs/office-preview", nil)
		request = request.WithContext(context.WithValue(request.Context(), authContextKey{}, authUser{username: "fixture", role: role}))
		handler.ServeHTTP(httptest.NewRecorder(), request)
		if reached != (role == "admin") {
			t.Fatalf("role %s reached converter: %v", role, reached)
		}
		reached = false
		request.Header.Set("Origin", "https://foreign.example")
		handler.ServeHTTP(httptest.NewRecorder(), request)
		if reached {
			t.Fatal("cross-origin conversion admitted")
		}
	}
	root := t.TempDir()
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	s := &server{finder: service}
	for _, test := range []struct {
		body   string
		status int
		code   string
	}{
		{`{}`, 415, "unsupported_format"},
		{`{broken`, 400, "invalid_request"},
		{`{"path":"` + filepath.Join(root, "missing.docx") + `"}`, 404, "not_found"},
	} {
		response := httptest.NewRecorder()
		s.officePreview(response, httptest.NewRequest("POST", "/api/fs/office-preview", strings.NewReader(test.body)))
		if response.Code != test.status {
			t.Fatalf("%s: %d %s", test.body, response.Code, response.Body.String())
		}
		if response.Header().Get("Cache-Control") != "no-store" {
			t.Fatal("missing no-store")
		}
	}
	path := filepath.Join(root, "large.docx")
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := file.Truncate(officeInputLimit + 1); err != nil {
		t.Fatal(err)
	}
	file.Close()
	response := httptest.NewRecorder()
	s.officePreview(response, httptest.NewRequest("POST", "/api/fs/office-preview", strings.NewReader(`{"path":"`+path+`"}`)))
	if response.Code != 413 {
		t.Fatalf("oversized input: %d %s", response.Code, response.Body.String())
	}
	s.officePreviewActive = 2
	response = httptest.NewRecorder()
	s.officePreview(response, httptest.NewRequest("POST", "/api/fs/office-preview", strings.NewReader(`{"path":"`+path+`"}`)))
	if response.Code != 429 || s.officePreviewActive != 2 {
		t.Fatal("concurrency admission failed")
	}
	s.officePreviewActive = 0
	path = filepath.Join(root, "sample.docx")
	if err := os.WriteFile(path, officeFixture(t, ".docx"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", t.TempDir())
	response = httptest.NewRecorder()
	s.officePreview(response, httptest.NewRequest("POST", "/api/fs/office-preview", strings.NewReader(`{"path":"`+path+`"}`)))
	if response.Code != 503 || !strings.Contains(response.Body.String(), "converter_missing") || s.officePreviewActive != 0 {
		t.Fatalf("missing dependency: %d %s", response.Code, response.Body.String())
	}
}

func TestOfficeOutputConfinement(t *testing.T) {
	outside := t.TempDir()
	if err := os.WriteFile(filepath.Join(outside, "document.pdf"), []byte("%PDF-secret"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"symlink", "parent", "fifo", "large", "valid", "canceled"} {
		t.Run(kind, func(t *testing.T) {
			job := t.TempDir()
			out := filepath.Join(job, "out")
			if kind == "parent" {
				if err := os.Symlink(outside, out); err != nil {
					t.Fatal(err)
				}
			} else {
				if err := os.Mkdir(out, 0700); err != nil {
					t.Fatal(err)
				}
				target := filepath.Join(out, "document.pdf")
				switch kind {
				case "symlink":
					if err := os.Symlink(filepath.Join(outside, "document.pdf"), target); err != nil {
						t.Fatal(err)
					}
				case "fifo":
					if err := syscall.Mkfifo(target, 0600); err != nil {
						t.Fatal(err)
					}
				default:
					if err := os.WriteFile(target, []byte("%PDF-1.4\n%%EOF"), 0600); err != nil {
						t.Fatal(err)
					}
					if kind == "large" {
						file, err := os.OpenFile(target, os.O_WRONLY, 0)
						if err != nil {
							t.Fatal(err)
						}
						if err := file.Truncate(officePDFLimit + 1); err != nil {
							t.Fatal(err)
						}
						file.Close()
					}
				}
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if kind == "canceled" {
				cancel()
			}
			result, err := readOfficePDF(ctx, job)
			if kind == "valid" {
				if err != nil || !bytes.HasPrefix(result, []byte("%PDF-")) {
					t.Fatal(err)
				}
			} else if err == nil {
				t.Fatalf("%s output accepted: %s", kind, result)
			}
		})
	}
}

func TestOfficeRealSandbox(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux containment qualification")
	}
	_, err := exec.LookPath("libreoffice")
	if err != nil {
		t.Skip("operator LibreOffice unavailable")
	}
	if _, err := exec.LookPath("bwrap"); err != nil {
		t.Skip("bubblewrap unavailable")
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	control, err := net.DialTimeout("tcp", listener.Addr().String(), time.Second)
	if err != nil {
		t.Fatalf("host network positive control: %v", err)
	}
	control.Close()
	port := listener.Addr().(*net.TCPAddr).Port
	job := t.TempDir()
	outside := filepath.Join(t.TempDir(), "secret.pdf")
	if err := os.WriteFile(outside, []byte("private"), 0600); err != nil {
		t.Fatal(err)
	}
	// A real interpreter inside the exact converter namespace checks denial, including loopback.
	command, err := officeSandboxCommand(context.Background(), "/usr/bin/python3", job, []string{"-c", `import os,socket,sys
assert not os.path.exists(sys.argv[1])
assert not os.path.exists('/etc/passwd')
assert not os.path.exists('/home/keivenc/.ssh')
s=socket.socket()
try: s.connect(('1.1.1.1',443))
except OSError: pass
else: raise AssertionError('external network allowed')
s=socket.socket()
try: s.connect(('127.0.0.1',int(sys.argv[2])))
except OSError: pass
else: raise AssertionError('host loopback allowed')
`, outside, strconv.Itoa(port)})
	if err != nil {
		t.Fatal(err)
	}
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("containment: %v %s", err, output)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	command, err = officeSandboxCommand(ctx, "/usr/bin/python3", job, []string{"-c", "import time; time.sleep(60)"})
	if err != nil {
		t.Fatal(err)
	}
	if err := command.Run(); err == nil || !errors.Is(ctx.Err(), context.DeadlineExceeded) {
		t.Fatal("deadline did not stop job")
	}
	cancel()
	scratch := t.TempDir()
	t.Setenv("TMPDIR", scratch)
	canceled, stop := context.WithCancel(context.Background())
	stop()
	if _, err := convertOfficePreview(canceled, "/usr/bin/libreoffice", ".docx", officeFixture(t, ".docx")); !errors.Is(err, context.Canceled) {
		t.Fatalf("abandoned conversion: %v", err)
	}
	entries, err := os.ReadDir(scratch)
	if err != nil || len(entries) != 0 {
		t.Fatalf("abandoned conversion leaked jobs: %v %v", entries, err)
	}
}

func TestOfficeConcurrentRequestsAndAbandonedJobs(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux subprocess admission contract")
	}
	if _, err := exec.LookPath("prlimit"); err != nil {
		t.Skip("prlimit unavailable")
	}
	root, tools, jobs := t.TempDir(), t.TempDir(), t.TempDir()
	t.Setenv("TMPDIR", jobs)
	started, release := filepath.Join(root, "started"), filepath.Join(root, "release")
	for _, path := range []string{started, release} {
		if err := syscall.Mkfifo(path, 0600); err != nil {
			t.Fatal(err)
		}
	}
	events, err := os.OpenFile(started, os.O_RDWR, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer events.Close()
	if err := events.SetReadDeadline(time.Now().Add(5 * time.Second)); err != nil {
		t.Fatal(err)
	}
	script := "#!/bin/sh\nprintf x > " + shellQuote(started) + "\nIFS= read -r release < " + shellQuote(release) + "\n"
	if err := os.WriteFile(filepath.Join(tools, "bwrap"), []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("/usr/bin/true", filepath.Join(tools, "libreoffice")); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", tools+string(os.PathListSeparator)+os.Getenv("PATH"))
	service, err := finder.New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	s := &server{finder: service}
	path := filepath.Join(root, "document.docx")
	if err := os.WriteFile(path, officeFixture(t, ".docx"), 0600); err != nil {
		t.Fatal(err)
	}
	body := `{"path":"` + path + `"}`
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	finished := make(chan int, 2)
	for i := 0; i < 2; i++ {
		go func() {
			response := httptest.NewRecorder()
			s.officePreview(response, httptest.NewRequest("POST", "/api/fs/office-preview", strings.NewReader(body)).WithContext(ctx))
			finished <- response.Code
		}()
	}
	remaining := 2
	defer func() {
		cancel()
		for i := 0; i < remaining; i++ {
			select {
			case <-finished:
			case <-time.After(5 * time.Second):
				t.Error("abandoned conversion did not retire")
			}
		}
	}()
	if _, err := io.ReadFull(events, make([]byte, 2)); err != nil {
		t.Fatal(err)
	}
	third := httptest.NewRecorder()
	s.officePreview(third, httptest.NewRequest("POST", "/api/fs/office-preview", strings.NewReader(body)))
	if third.Code != 429 {
		t.Fatalf("third overlapping request: %d", third.Code)
	}
	cancel()
	for i := 0; i < 2; i++ {
		select {
		case code := <-finished:
			if code != 408 {
				t.Errorf("canceled request: %d", code)
			}
			remaining--
		case <-time.After(5 * time.Second):
			t.Fatal("canceled conversion stuck")
		}
	}
	// The deferred drain owns completion; inspect state only after both request returns.
	s.officePreviewMu.Lock()
	active := s.officePreviewActive
	s.officePreviewMu.Unlock()
	if active != 0 {
		t.Fatalf("conversion slots leaked: %d", active)
	}
	entries, err := os.ReadDir(jobs)
	if err != nil || len(entries) != 0 {
		t.Fatalf("conversion directories leaked: %v %v", entries, err)
	}
}
