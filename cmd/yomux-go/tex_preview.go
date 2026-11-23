// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"archive/tar"
	"bufio"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/keivenchang/yomux/internal/atomicfile"
)

const texCompilerVersion = "0.17.0"
const texSourceLimit = 2 << 20
const texPDFLimit = 32 << 20
const texDownloadLimit = 64 << 20
const texProgressLogLimit = 8 << 10
const texSyncTeXLimit = 16 << 20

type texSyncPage struct {
	Line int `json:"line"`
	Page int `json:"page"`
}

type texProgressState struct {
	Phase         string        `json:"phase"`
	Message       string        `json:"message"`
	Downloaded    int64         `json:"downloaded_bytes,omitempty"`
	Total         int64         `json:"total_bytes,omitempty"`
	ElapsedMillis int64         `json:"elapsed_ms"`
	Log           string        `json:"log,omitempty"`
	SyncPages     []texSyncPage `json:"-"`
	StartedAt     time.Time     `json:"-"`
	UpdatedAt     time.Time     `json:"-"`
}

func (s *server) setTexProgress(jobID, phase, message string, downloaded, total int64, output string) {
	if len(jobID) < 16 || len(jobID) > 64 {
		return
	}
	now := time.Now()
	s.texProgressMu.Lock()
	defer s.texProgressMu.Unlock()
	if s.texProgress == nil {
		s.texProgress = make(map[string]texProgressState)
	}
	for id, state := range s.texProgress {
		retention := 10 * time.Minute
		if state.Phase == "complete" {
			retention = 24 * time.Hour
		}
		if now.Sub(state.UpdatedAt) > retention {
			delete(s.texProgress, id)
		}
	}
	started := now
	if previous, ok := s.texProgress[jobID]; ok {
		started = previous.StartedAt
	}
	if len(output) > texProgressLogLimit {
		output = output[len(output)-texProgressLogLimit:]
	}
	if len(s.texProgress) >= 128 {
		var oldestID string
		var oldest time.Time
		for id, state := range s.texProgress {
			if oldestID == "" || state.UpdatedAt.Before(oldest) {
				oldestID, oldest = id, state.UpdatedAt
			}
		}
		delete(s.texProgress, oldestID)
	}
	s.texProgress[jobID] = texProgressState{Phase: phase, Message: message, Downloaded: downloaded, Total: total, ElapsedMillis: now.Sub(started).Milliseconds(), Log: output, StartedAt: started, UpdatedAt: now}
}

func (s *server) texProgressSnapshot(jobID string) (texProgressState, bool) {
	s.texProgressMu.Lock()
	defer s.texProgressMu.Unlock()
	state, ok := s.texProgress[jobID]
	if !ok {
		return texProgressState{}, false
	}
	state.ElapsedMillis = time.Since(state.StartedAt).Milliseconds()
	state.UpdatedAt = time.Now()
	s.texProgress[jobID] = state
	return state, true
}

func (s *server) setTexSyncPages(jobID string, pages []texSyncPage) {
	s.texProgressMu.Lock()
	defer s.texProgressMu.Unlock()
	state, ok := s.texProgress[jobID]
	if !ok {
		return
	}
	state.SyncPages = pages
	state.UpdatedAt = time.Now()
	s.texProgress[jobID] = state
}

func texSyncPageForLine(pages []texSyncPage, line int) (int, bool) {
	if len(pages) == 0 || line < 1 {
		return 0, false
	}
	index := sort.Search(len(pages), func(index int) bool { return pages[index].Line >= line })
	if index == len(pages) {
		return pages[len(pages)-1].Page, true
	}
	if index == 0 || pages[index].Line == line {
		return pages[index].Page, true
	}
	if line-pages[index-1].Line <= pages[index].Line-line {
		index--
	}
	return pages[index].Page, true
}

// Published release digests pin executable provenance independently of the download.
// https://github.com/tectonic-typesetting/tectonic/releases/tag/tectonic%400.17.0
var texCompilerAssets = map[string]struct{ platform, digest string }{
	"linux/amd64":  {"x86_64-unknown-linux-musl", "8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7"},
	"linux/arm64":  {"aarch64-unknown-linux-musl", "b10954a95404f3ab2328d2fa59a5ebab8e657f893fab096f98be8db7c0c979b8"},
	"darwin/amd64": {"x86_64-apple-darwin", "7c90ef5b6ddb1eb1937e4337add5237b79338e4b9676459fa91187d24d6cdf80"},
	"darwin/arm64": {"aarch64-apple-darwin", "a3f1cac7c5678f01661a92212f58480ae3b0634115d880dbc59e2953ded45667"},
}

func (s *server) texToolsDirectory() string {
	return filepath.Join(s.config, "tex-tools")
}

func (s *server) texCompilerInstallPath() string {
	return filepath.Join(s.texToolsDirectory(), "bin", "tectonic-"+texCompilerVersion)
}

func (s *server) legacyTexCompilerPaths() ([]string, error) {
	entries, err := os.ReadDir(s.texToolsDirectory())
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("list existing TeX tools: %w", err)
	}
	paths := make([]string, 0, len(entries))
	for _, entry := range entries {
		if _, err := strconv.Atoi(entry.Name()); err == nil && entry.IsDir() {
			paths = append(paths, filepath.Join(s.texToolsDirectory(), entry.Name(), "bin", "tectonic-"+texCompilerVersion))
		}
	}
	return paths, nil
}

func (s *server) acquireTexToolsLock(ctx context.Context) (*atomicfile.Lock, error) {
	if err := os.MkdirAll(s.texToolsDirectory(), 0o700); err != nil {
		return nil, fmt.Errorf("prepare shared TeX tools directory: %w", err)
	}
	return atomicfile.AcquireLock(ctx, filepath.Join(s.texToolsDirectory(), ".operation.lock"))
}

func (s *server) texCompiler() (string, string) {
	managed := s.texCompilerInstallPath()
	candidates := []struct{ path, kind string }{{managed, "tectonic"}}
	legacy, err := s.legacyTexCompilerPaths()
	if err != nil {
		slog.Warn("cannot inspect legacy TeX compilers", "error", err)
	} else {
		for _, path := range legacy {
			candidates = append(candidates, struct{ path, kind string }{path, "tectonic"})
		}
	}
	for _, name := range []string{"tectonic", "pdflatex"} {
		if path, err := exec.LookPath(name); err == nil {
			candidates = append(candidates, struct{ path, kind string }{path, name})
		}
	}
	for _, candidate := range candidates {
		path, err := filepath.EvalSymlinks(candidate.path)
		if err != nil {
			continue
		}
		path, err = filepath.Abs(path)
		if err != nil {
			continue
		}
		if info, err := os.Stat(path); err == nil && info.Mode().IsRegular() && info.Mode().Perm()&0o111 != 0 {
			return path, candidate.kind
		}
	}
	return "", ""
}

func texInstallInstructions() string {
	if runtime.GOOS == "darwin" {
		return "brew install tectonic"
	}
	if runtime.GOOS == "linux" {
		return "sudo apt install texlive-latex-base texlive-latex-recommended bubblewrap"
	}
	return "https://tectonic-typesetting.github.io/en-US/install/"
}

func (s *server) texCompilerPayload() map[string]any {
	_, compiler := s.texCompiler()
	_, supported := texCompilerAssets[runtime.GOOS+"/"+runtime.GOARCH]
	return map[string]any{"available": compiler != "", "compiler": compiler, "install_supported": supported, "install_path": s.texCompilerInstallPath(), "instructions": texInstallInstructions()}
}

func (s *server) texCompilerStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodDelete {
		jobID := r.URL.Query().Get("job_id")
		if len(jobID) < 16 || len(jobID) > 64 {
			jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid TeX job identifier"})
			return
		}
		s.texProgressMu.Lock()
		delete(s.texProgress, jobID)
		s.texProgressMu.Unlock()
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	payload := s.texCompilerPayload()
	if jobID := r.URL.Query().Get("job_id"); jobID != "" {
		if state, ok := s.texProgressSnapshot(jobID); ok {
			payload["progress"] = state
			if r.URL.Query().Get("sync") == "1" {
				payload["sync_pages"] = state.SyncPages
			}
			if line, err := strconv.Atoi(r.URL.Query().Get("line")); err == nil {
				if page, ok := texSyncPageForLine(state.SyncPages, line); ok {
					payload["sync_page"] = page
				}
			}
		}
	}
	jsonResponse(w, http.StatusOK, payload)
}

func (s *server) texCompilerInstall(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Consent bool   `json:"consent"`
		JobID   string `json:"job_id"`
	}
	if err := decodeJSONBody(r.Body, 1024, &body); err != nil || !body.Consent {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"code": "consent_required", "error": "Compiler installation requires explicit consent."})
		return
	}
	if !s.texInstallMu.TryLock() {
		s.setTexProgress(body.JobID, "failed", "A compiler installation is already running.", 0, 0, "")
		jsonResponse(w, http.StatusOK, map[string]any{"available": false, "code": "install_busy", "error": "A compiler installation is already running. Press Retry when it finishes.", "instructions": texInstallInstructions()})
		return
	}
	defer s.texInstallMu.Unlock()
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Minute)
	defer cancel()
	s.setTexProgress(body.JobID, "waiting", "Waiting for another YOmux instance to finish using TeX tools", 0, 0, "")
	sharedLock, err := s.acquireTexToolsLock(ctx)
	if err != nil {
		s.setTexProgress(body.JobID, "failed", err.Error(), 0, 0, "")
		jsonResponse(w, http.StatusOK, map[string]any{"available": false, "code": "install_failed", "error": err.Error(), "instructions": texInstallInstructions(), "install_supported": true})
		return
	}
	defer func() {
		if err := sharedLock.Close(); err != nil {
			slog.Warn("TeX tools lock release failed", "error", err)
		}
	}()
	if err := s.migrateLegacyTexCompiler(ctx); err != nil {
		s.setTexProgress(body.JobID, "failed", err.Error(), 0, 0, "")
		jsonResponse(w, http.StatusOK, map[string]any{"available": false, "code": "install_failed", "error": err.Error(), "instructions": texInstallInstructions(), "install_supported": true})
		return
	}
	if err := s.migrateLegacyTexCache(ctx); err != nil {
		s.setTexProgress(body.JobID, "failed", err.Error(), 0, 0, "")
		jsonResponse(w, http.StatusOK, map[string]any{"available": false, "code": "install_failed", "error": err.Error(), "instructions": texInstallInstructions(), "install_supported": true})
		return
	}
	if _, compiler := s.texCompiler(); compiler == "" {
		s.setTexProgress(body.JobID, "downloading", "Downloading the TeX compiler…", 0, 0, "")
		if err := s.installTexCompiler(ctx, body.JobID); err != nil {
			s.setTexProgress(body.JobID, "failed", err.Error(), 0, 0, "")
			jsonResponse(w, http.StatusOK, map[string]any{"available": false, "code": "install_failed", "error": err.Error(), "instructions": texInstallInstructions(), "install_supported": true})
			return
		}
	}
	s.setTexProgress(body.JobID, "complete", "TeX compiler installed.", 0, 0, "")
	jsonResponse(w, http.StatusOK, s.texCompilerPayload())
}

func (s *server) installTexCompiler(ctx context.Context, jobID string) error {
	asset, ok := texCompilerAssets[runtime.GOOS+"/"+runtime.GOARCH]
	if !ok {
		return fmt.Errorf("automatic TeX installation is unavailable on %s/%s", runtime.GOOS, runtime.GOARCH)
	}
	url := fmt.Sprintf("https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%%40%s/tectonic-%s-%s.tar.gz", texCompilerVersion, texCompilerVersion, asset.platform)
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	response, err := (&http.Client{Timeout: 2 * time.Minute}).Do(request)
	if err != nil {
		return fmt.Errorf("download Tectonic: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("download Tectonic: HTTP %d", response.StatusCode)
	}
	var archive bytes.Buffer
	var downloaded texDownloadProgressWriter
	downloaded.update = func(n int64) {
		s.setTexProgress(jobID, "downloading", "Downloading TeX compiler", n, response.ContentLength, "")
	}
	if _, err := io.Copy(&archive, io.LimitReader(io.TeeReader(response.Body, &downloaded), texDownloadLimit+1)); err != nil {
		return fmt.Errorf("read compiler download: %w", err)
	}
	if archive.Len() > texDownloadLimit {
		return errors.New("compiler download exceeds 64 MiB")
	}
	s.setTexProgress(jobID, "verifying", "Verifying compiler download", int64(archive.Len()), response.ContentLength, "")
	if err := s.publishTexCompilerWithProgress(archive.Bytes(), asset.digest, func() {
		s.setTexProgress(jobID, "installing", "Installing verified TeX compiler", int64(archive.Len()), response.ContentLength, "")
	}); err != nil {
		return err
	}
	return nil
}

type texDownloadProgressWriter struct {
	count  int64
	update func(int64)
}

func (writer *texDownloadProgressWriter) Write(data []byte) (int, error) {
	writer.count += int64(len(data))
	writer.update(writer.count)
	return len(data), nil
}

func (s *server) publishTexCompiler(archive []byte, digest string) error {
	return s.publishTexCompilerWithProgress(archive, digest, nil)
}

func (s *server) publishTexCompilerWithProgress(archive []byte, digest string, onVerified func()) error {
	if len(archive) > 64<<20 || fmt.Sprintf("%x", sha256.Sum256(archive)) != digest {
		return errors.New("compiler download failed its pinned SHA-256 verification")
	}
	if onVerified != nil {
		onVerified()
	}
	gz, err := gzip.NewReader(bytes.NewReader(archive))
	if err != nil {
		return fmt.Errorf("open compiler archive: %w", err)
	}
	defer gz.Close()
	reader := tar.NewReader(gz)
	for {
		header, err := reader.Next()
		if errors.Is(err, io.EOF) {
			return errors.New("compiler archive contains no tectonic executable")
		}
		if err != nil {
			return fmt.Errorf("read compiler archive: %w", err)
		}
		if header.Name != "tectonic" {
			continue
		}
		if header.Typeflag != tar.TypeReg || header.Size <= 0 || header.Size > 128<<20 {
			return errors.New("invalid compiler executable in archive")
		}
		directory := filepath.Join(s.texToolsDirectory(), "bin")
		if err := os.MkdirAll(directory, 0o700); err != nil {
			return fmt.Errorf("create compiler directory: %w", err)
		}
		file, err := os.CreateTemp(directory, ".tectonic-*")
		if err != nil {
			return err
		}
		defer os.Remove(file.Name())
		_, copyErr := io.CopyN(file, reader, header.Size)
		modeErr := file.Chmod(0o700)
		closeErr := file.Close()
		if err := errors.Join(copyErr, modeErr, closeErr); err != nil {
			return fmt.Errorf("write compiler: %w", err)
		}
		return os.Rename(file.Name(), s.texCompilerInstallPath())
	}
}

func (s *server) migrateLegacyTexCompiler(ctx context.Context) error {
	target := s.texCompilerInstallPath()
	if _, err := os.Lstat(target); err == nil {
		return nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("inspect shared TeX compiler: %w", err)
	}
	var legacy string
	legacyPaths, err := s.legacyTexCompilerPaths()
	if err != nil {
		return err
	}
	for _, candidate := range legacyPaths {
		info, err := os.Lstat(candidate)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return fmt.Errorf("inspect existing TeX compiler %s: %w", candidate, err)
		}
		if info.Mode().IsRegular() && info.Mode().Perm()&0o111 != 0 {
			legacy = candidate
			break
		}
	}
	if legacy == "" {
		return nil
	}
	directory := filepath.Dir(target)
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return fmt.Errorf("create shared TeX compiler directory: %w", err)
	}
	input, err := os.Open(legacy)
	if err != nil {
		return fmt.Errorf("open existing TeX compiler: %w", err)
	}
	output, err := os.CreateTemp(directory, ".tectonic-migrate-*")
	if err != nil {
		return errors.Join(err, input.Close())
	}
	defer os.Remove(output.Name())
	_, copyErr := copyWithContext(ctx, output, input)
	inputCloseErr := input.Close()
	modeErr := output.Chmod(0o700)
	syncErr := output.Sync()
	closeErr := output.Close()
	if err := errors.Join(copyErr, inputCloseErr, modeErr, syncErr, closeErr); err != nil {
		return fmt.Errorf("copy existing TeX compiler to shared tools: %w", err)
	}
	if err := os.Rename(output.Name(), target); err != nil {
		return fmt.Errorf("publish shared TeX compiler: %w", err)
	}
	return nil
}

func (s *server) migrateLegacyTexCache(ctx context.Context) error {
	entries, err := os.ReadDir(s.texToolsDirectory())
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("list existing TeX tools: %w", err)
	}
	shared := filepath.Join(s.texToolsDirectory(), "cache")
	if err := os.MkdirAll(shared, 0o700); err != nil {
		return fmt.Errorf("create shared TeX package cache: %w", err)
	}
	for _, entry := range entries {
		if _, err := strconv.Atoi(entry.Name()); err != nil || !entry.IsDir() {
			continue
		}
		legacy := filepath.Join(s.texToolsDirectory(), entry.Name(), "cache")
		info, err := os.Lstat(legacy)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return fmt.Errorf("inspect existing TeX package cache %s: %w", legacy, err)
		}
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("existing TeX package cache %s is not a regular directory", legacy)
		}
		if err := migrateTexCacheDirectory(ctx, legacy, shared); err != nil {
			return fmt.Errorf("migrate existing TeX package cache %s: %w", legacy, err)
		}
	}
	return nil
}

func migrateTexCacheDirectory(ctx context.Context, legacy, shared string) error {
	return filepath.WalkDir(legacy, func(source string, entry os.DirEntry, walkErr error) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if walkErr != nil {
			return walkErr
		}
		if source == legacy {
			return nil
		}
		relative, err := filepath.Rel(legacy, source)
		if err != nil {
			return err
		}
		target := filepath.Join(shared, relative)
		if entry.Type()&os.ModeSymlink != 0 {
			return nil
		}
		if entry.IsDir() {
			return os.MkdirAll(target, 0o700)
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			return nil
		}
		if _, err := os.Lstat(target); err == nil {
			return nil
		} else if !errors.Is(err, os.ErrNotExist) {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
			return err
		}
		input, err := os.Open(source)
		if err != nil {
			return err
		}
		output, err := os.CreateTemp(filepath.Dir(target), ".tex-cache-migrate-*")
		if err != nil {
			return errors.Join(err, input.Close())
		}
		_, copyErr := copyWithContext(ctx, output, input)
		inputCloseErr := input.Close()
		modeErr := output.Chmod(0o600)
		syncErr := output.Sync()
		closeErr := output.Close()
		if err := errors.Join(copyErr, inputCloseErr, modeErr, syncErr, closeErr); err != nil {
			_ = os.Remove(output.Name())
			return err
		}
		if err := os.Rename(output.Name(), target); err != nil {
			_ = os.Remove(output.Name())
			return err
		}
		return nil
	})
}

func copyWithContext(ctx context.Context, destination io.Writer, source io.Reader) (int64, error) {
	buffer := make([]byte, 64<<10)
	var total int64
	for {
		if err := ctx.Err(); err != nil {
			return total, err
		}
		count, readErr := source.Read(buffer)
		if count > 0 {
			written, writeErr := destination.Write(buffer[:count])
			total += int64(written)
			if writeErr != nil {
				return total, writeErr
			}
			if written != count {
				return total, io.ErrShortWrite
			}
		}
		if errors.Is(readErr, io.EOF) {
			return total, nil
		}
		if readErr != nil {
			return total, readErr
		}
	}
}

func (s *server) texPreview(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Path    string `json:"path"`
		Content string `json:"content"`
		JobID   string `json:"job_id"`
		Publish bool   `json:"publish"`
	}
	if err := decodeJSONBody(r.Body, 2*texSourceLimit, &body); err != nil || len(body.Content) > texSourceLimit || (strings.ToLower(filepath.Ext(body.Path)) != ".tex" && strings.ToLower(filepath.Ext(body.Path)) != ".ltx") {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"code": "invalid_tex", "error": "Preview requires a .tex or .ltx document up to 2 MiB."})
		return
	}
	// Use Finder's descriptor-bound read owner even though compilation consumes the browser's
	// unsaved buffer. This preserves the same authorization and regular-file checks as Open.
	if _, _, _, err := s.finder.Raw(r.Context(), body.Path, texSourceLimit); err != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, err)
		return
	}
	compiler, kind := s.texCompiler()
	if compiler == "" {
		s.setTexProgress(body.JobID, "failed", "TeX compiler is not installed.", 0, 0, "")
		payload := s.texCompilerPayload()
		payload["code"] = "compiler_missing"
		jsonResponse(w, http.StatusOK, payload)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 150*time.Second)
	defer cancel()
	// Replaced browser requests cancel while queued too; their successor must not lose
	// its turn merely because the retiring engine has not released the shared cache yet.
	s.setTexProgress(body.JobID, "queued", "Waiting for the TeX compiler", 0, 0, "")
	if err := s.texCompileMu.LockContext(ctx, nil); err != nil {
		s.setTexProgress(body.JobID, "failed", err.Error(), 0, 0, "")
		jsonResponse(w, http.StatusOK, map[string]any{"code": "compile_cancelled", "error": err.Error()})
		return
	}
	defer s.texCompileMu.Unlock()
	message := "Compiling TeX document"
	if kind == "tectonic" {
		message += " (the first compile may download TeX packages)"
	}
	s.setTexProgress(body.JobID, "compiling", message, 0, 0, "")
	pdf, syncPages, err := s.compileTexWithProgress(ctx, compiler, kind, body.Path, body.Content, func(output string) {
		s.setTexProgress(body.JobID, "compiling", message, 0, 0, output)
	})
	if err != nil {
		s.setTexProgress(body.JobID, "failed", err.Error(), 0, 0, "")
		jsonResponse(w, http.StatusOK, map[string]any{"code": "compile_failed", "error": err.Error(), "instructions": texInstallInstructions()})
		return
	}
	s.setTexProgress(body.JobID, "complete", "TeX preview ready", 0, 0, "")
	s.setTexSyncPages(body.JobID, syncPages)
	if body.Publish {
		if err := s.sharePublishPDF(body.Path, body.Content, pdf); err != nil {
			shareFailure(w, 409, err.Error())
			return
		}
	}
	w.Header().Set("Content-Type", "application/pdf")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Disposition", `inline; filename="preview.pdf"`)
	w.Header().Set("Content-Length", strconv.Itoa(len(pdf)))
	_, _ = w.Write(pdf)
}

func (s *server) compileTex(ctx context.Context, compiler, kind, path, content string) ([]byte, error) {
	pdf, _, err := s.compileTexWithProgress(ctx, compiler, kind, path, content, nil)
	return pdf, err
}

func (s *server) compileTexWithProgress(ctx context.Context, compiler, kind, path, content string, onOutput func(string)) ([]byte, []texSyncPage, error) {
	tools := s.texToolsDirectory()
	if err := os.MkdirAll(tools, 0o700); err != nil {
		return nil, nil, err
	}
	if kind == "tectonic" {
		sharedLock, err := s.acquireTexToolsLock(ctx)
		if err != nil {
			return nil, nil, fmt.Errorf("wait for shared TeX tools: %w", err)
		}
		defer func() {
			if err := sharedLock.Close(); err != nil {
				slog.Warn("TeX tools lock release failed", "error", err)
			}
		}()
		if err := s.migrateLegacyTexCompiler(ctx); err != nil {
			return nil, nil, err
		}
		if sharedCompiler, sharedKind := s.texCompiler(); sharedKind == "tectonic" && filepath.Clean(sharedCompiler) == filepath.Clean(s.texCompilerInstallPath()) {
			compiler = sharedCompiler
		}
		if err := s.migrateLegacyTexCache(ctx); err != nil {
			return nil, nil, err
		}
	}
	cache := filepath.Join(tools, "cache")
	if err := os.MkdirAll(cache, 0o700); err != nil {
		return nil, nil, err
	}
	cache, err := filepath.EvalSymlinks(cache)
	if err != nil {
		return nil, nil, err
	}
	job, err := os.MkdirTemp(tools, "preview-*")
	if err != nil {
		return nil, nil, err
	}
	defer os.RemoveAll(job)
	job, err = filepath.EvalSymlinks(job)
	if err != nil {
		return nil, nil, err
	}
	args := []string{"--untrusted", "--outdir", job, "--synctex", "--keep-intermediates", "-"}
	name := "texput.pdf"
	project, err := filepath.EvalSymlinks(filepath.Dir(path))
	if err != nil {
		return nil, nil, err
	}
	workingDirectory := project
	if kind == "pdflatex" {
		if err := os.WriteFile(filepath.Join(job, "preview.tex"), []byte(content), 0o600); err != nil {
			return nil, nil, err
		}
		args = []string{"-fmt=pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "-synctex=1", "-output-directory=" + job, "preview.tex"}
		name = "preview.pdf"
		workingDirectory = job
	}
	var log texProgressLog
	log.onOutput = onOutput
	passes := 1
	if kind == "pdflatex" {
		passes = 2
	}
	for pass := 0; pass < passes; pass++ {
		command, err := texSandboxCommand(ctx, compiler, args, job, cache, project)
		if err != nil {
			return nil, nil, err
		}
		command.Dir = workingDirectory
		command.Env = []string{"PATH=" + filepath.Dir(compiler) + ":/usr/bin:/bin", "HOME=" + job, "TMPDIR=" + job, "XDG_CACHE_HOME=" + cache, "TECTONIC_UNTRUSTED_MODE=1", "openout_any=p", "openin_any=p", "LANG=C.UTF-8"}
		if kind == "pdflatex" {
			command.Env = append(command.Env, "TEXINPUTS="+job+string(os.PathListSeparator)+project+string(os.PathListSeparator))
		}
		command.Stdin = strings.NewReader(content)
		command.SysProcAttr = nativeAgentProcessAttributes()
		command.Cancel = func() error { return stopNativeAgentProcess(command) }
		command.WaitDelay = 2 * time.Second
		command.Stdout, command.Stderr = &log, &log
		if err := command.Run(); err != nil {
			return nil, nil, fmt.Errorf("TeX compilation failed: %w\n%s", errors.Join(ctx.Err(), err), log.String())
		}
	}
	file, err := os.Open(filepath.Join(job, name))
	if err != nil {
		return nil, nil, fmt.Errorf("compiler produced no PDF: %w\n%s", err, log.String())
	}
	defer file.Close()
	pdf, err := io.ReadAll(io.LimitReader(file, texPDFLimit+1))
	if err != nil {
		return nil, nil, err
	}
	if len(pdf) > texPDFLimit || !bytes.HasPrefix(pdf, []byte("%PDF-")) {
		return nil, nil, errors.New("compiler output is not a PDF up to 32 MiB")
	}
	synctexPath := filepath.Join(job, strings.TrimSuffix(name, ".pdf")+".synctex.gz")
	syncPages, syncErr := readTexSyncPages(synctexPath)
	if syncErr != nil {
		slog.Debug("TeX preview has no usable SyncTeX data", "error", syncErr)
	}
	return pdf, syncPages, nil
}

func readTexSyncPages(path string) ([]texSyncPage, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	compressed, err := gzip.NewReader(file)
	if err != nil {
		return nil, err
	}
	defer compressed.Close()
	data, err := io.ReadAll(io.LimitReader(compressed, texSyncTeXLimit+1))
	if err != nil {
		return nil, err
	}
	if len(data) > texSyncTeXLimit {
		return nil, errors.New("SyncTeX data exceeds 16 MiB")
	}
	var mainInputTag int
	var currentPage int
	pageByLine := make(map[int]int)
	scanner := bufio.NewScanner(bytes.NewReader(data))
	scanner.Buffer(make([]byte, 64<<10), texSyncTeXLimit)
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "Input:") && mainInputTag == 0 {
			parts := strings.SplitN(strings.TrimPrefix(line, "Input:"), ":", 2)
			if len(parts) == 2 {
				mainInputTag, _ = strconv.Atoi(parts[0])
			}
			continue
		}
		if line == "Content:" {
			continue
		}
		if strings.HasPrefix(line, "{") {
			currentPage, _ = strconv.Atoi(strings.TrimPrefix(line, "{"))
			continue
		}
		if currentPage == 0 || mainInputTag == 0 {
			continue
		}
		tag, sourceLine, ok := texSyncRecordSource(line)
		if ok && tag == mainInputTag && sourceLine > 0 {
			if _, exists := pageByLine[sourceLine]; !exists {
				pageByLine[sourceLine] = currentPage
			}
		}
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	pages := make([]texSyncPage, 0, len(pageByLine))
	for sourceLine, page := range pageByLine {
		pages = append(pages, texSyncPage{Line: sourceLine, Page: page})
	}
	sort.Slice(pages, func(i, j int) bool { return pages[i].Line < pages[j].Line })
	if len(pages) == 0 {
		return nil, errors.New("SyncTeX data contains no source locations")
	}
	return pages, nil
}

func texSyncRecordSource(record string) (int, int, bool) {
	if len(record) < 2 || !strings.ContainsRune("[(hvxkg$", rune(record[0])) {
		return 0, 0, false
	}
	fields := strings.TrimSpace(record[1:])
	if record[0] == '(' || record[0] == '[' {
		// Empty layout boxes can refer back to the preamble on later pages.
		if dimensions := strings.SplitN(fields, ":", 3); len(dimensions) == 3 && strings.HasPrefix(dimensions[2], "0,") {
			return 0, 0, false
		}
	}
	comma := strings.IndexByte(fields, ',')
	if comma <= 0 {
		return 0, 0, false
	}
	end := strings.IndexAny(fields[comma+1:], ":,")
	if end < 0 {
		return 0, 0, false
	}
	tag, tagErr := strconv.Atoi(fields[:comma])
	line, lineErr := strconv.Atoi(fields[comma+1 : comma+1+end])
	return tag, line, tagErr == nil && lineErr == nil
}

type texPreviewLog []byte

func (log *texPreviewLog) Write(data []byte) (int, error) {
	n := len(data)
	if available := (64 << 10) - len(*log); available > 0 {
		if len(data) > available {
			data = data[:available]
		}
		*log = append(*log, data...)
	}
	return n, nil
}

type texProgressLog struct {
	mu       sync.Mutex
	output   []byte
	onOutput func(string)
}

func (log *texProgressLog) Write(data []byte) (int, error) {
	n := len(data)
	log.mu.Lock()
	log.output = append(log.output, data...)
	if len(log.output) > texProgressLogLimit {
		log.output = append([]byte(nil), log.output[len(log.output)-texProgressLogLimit:]...)
	}
	output := string(log.output)
	onOutput := log.onOutput
	log.mu.Unlock()
	if onOutput != nil {
		onOutput(output)
	}
	return n, nil
}

func (log *texProgressLog) String() string {
	log.mu.Lock()
	defer log.mu.Unlock()
	return string(log.output)
}

func texSandboxCommand(ctx context.Context, compiler string, args []string, job, cache, project string) (*exec.Cmd, error) {
	switch runtime.GOOS {
	case "linux":
		wrapper, err := exec.LookPath("bwrap")
		if err != nil {
			return nil, errors.New("TeX preview requires bubblewrap: sudo apt install bubblewrap")
		}
		wrapped := []string{"--die-with-parent", "--unshare-pid", "--unshare-ipc", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"}
		// TeX documents can perform file reads even with shell escape disabled. Expose only
		// the selected project plus the engine, public runtime assets and bundle TLS/DNS files.
		for _, path := range []string{"/usr", "/bin", "/lib", "/lib64", "/etc/texmf", "/var/lib/texmf", "/etc/fonts", "/etc/ssl", "/etc/pki", "/etc/hosts", "/etc/resolv.conf", "/etc/nsswitch.conf", "/var/cache/fontconfig"} {
			if _, err := os.Stat(path); err == nil {
				wrapped = append(wrapped, "--ro-bind", path, path)
			}
		}
		wrapped = append(wrapped, "--ro-bind", project, project, "--ro-bind", compiler, compiler, "--bind", job, job, "--bind", cache, cache, "--", compiler)
		return exec.CommandContext(ctx, wrapper, append(wrapped, args...)...), nil
	case "darwin":
		profile := "(version 1)(allow default)(deny file-read* file-write*)(allow file-read-metadata)(allow file-read-data"
		for _, path := range []string{project, job, cache, "/System/Library", "/Library/Fonts", "/Library/Keychains", "/usr/lib", "/usr/share", "/usr/local/lib", "/usr/local/share", "/usr/local/Cellar", "/usr/local/texlive", "/opt/homebrew/Cellar", "/opt/homebrew/lib", "/opt/homebrew/share", "/private/etc/fonts", "/private/etc/ssl"} {
			profile += " (subpath " + strconv.Quote(path) + ")"
		}
		for _, path := range []string{compiler, "/private/etc/resolv.conf", "/private/etc/hosts", "/dev/null", "/dev/random", "/dev/urandom"} {
			profile += " (literal " + strconv.Quote(path) + ")"
		}
		profile += fmt.Sprintf(")(allow file-write* (subpath %s) (subpath %s))(deny process-exec)(allow process-exec (literal %s))", strconv.Quote(job), strconv.Quote(cache), strconv.Quote(compiler))
		return exec.CommandContext(ctx, "/usr/bin/sandbox-exec", append([]string{"-p", profile, compiler}, args...)...), nil
	default:
		return nil, fmt.Errorf("TeX preview is not supported on %s", runtime.GOOS)
	}
}
