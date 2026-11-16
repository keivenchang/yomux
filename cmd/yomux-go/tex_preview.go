// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

const texCompilerVersion = "0.17.0"
const texSourceLimit = 2 << 20
const texPDFLimit = 32 << 20

// Published release digests pin executable provenance independently of the download.
// https://github.com/tectonic-typesetting/tectonic/releases/tag/tectonic%400.17.0
var texCompilerAssets = map[string]struct{ platform, digest string }{
	"linux/amd64":  {"x86_64-unknown-linux-musl", "8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7"},
	"linux/arm64":  {"aarch64-unknown-linux-musl", "b10954a95404f3ab2328d2fa59a5ebab8e657f893fab096f98be8db7c0c979b8"},
	"darwin/amd64": {"x86_64-apple-darwin", "7c90ef5b6ddb1eb1937e4337add5237b79338e4b9676459fa91187d24d6cdf80"},
	"darwin/arm64": {"aarch64-apple-darwin", "a3f1cac7c5678f01661a92212f58480ae3b0634115d880dbc59e2953ded45667"},
}

func (s *server) texToolsDirectory() string {
	return filepath.Join(s.config, "tex-tools", strconv.Itoa(s.port))
}

func (s *server) texCompiler() (string, string) {
	managed := filepath.Join(s.texToolsDirectory(), "bin", "tectonic-"+texCompilerVersion)
	candidates := []struct{ path, kind string }{{managed, "tectonic"}}
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
		return "On the YOmux server Mac, run: brew install tectonic\nThen press Retry. If Homebrew is unavailable, see https://tectonic-typesetting.github.io/en-US/install/"
	}
	return "Install Tectonic on the YOmux server: https://tectonic-typesetting.github.io/en-US/install/\nAlternatively, on Debian/Ubuntu run: sudo apt install texlive-latex-base texlive-latex-recommended\nThen press Retry. Linux previews also require bubblewrap (sudo apt install bubblewrap)."
}

func (s *server) texCompilerPayload() map[string]any {
	_, compiler := s.texCompiler()
	_, supported := texCompilerAssets[runtime.GOOS+"/"+runtime.GOARCH]
	return map[string]any{"available": compiler != "", "compiler": compiler, "install_supported": supported, "instructions": texInstallInstructions()}
}

func (s *server) texCompilerStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	jsonResponse(w, http.StatusOK, s.texCompilerPayload())
}

func (s *server) texCompilerInstall(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Consent bool `json:"consent"`
	}
	if err := decodeJSONBody(r.Body, 1024, &body); err != nil || !body.Consent {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"code": "consent_required", "error": "Compiler installation requires explicit consent."})
		return
	}
	if !s.texInstallMu.TryLock() {
		jsonResponse(w, http.StatusOK, map[string]any{"available": false, "code": "install_busy", "error": "A compiler installation is already running. Press Retry when it finishes.", "instructions": texInstallInstructions()})
		return
	}
	defer s.texInstallMu.Unlock()
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Minute)
	defer cancel()
	if _, compiler := s.texCompiler(); compiler == "" {
		if err := s.installTexCompiler(ctx); err != nil {
			jsonResponse(w, http.StatusOK, map[string]any{"available": false, "code": "install_failed", "error": err.Error(), "instructions": texInstallInstructions(), "install_supported": true})
			return
		}
	}
	jsonResponse(w, http.StatusOK, s.texCompilerPayload())
}

func (s *server) installTexCompiler(ctx context.Context) error {
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
	archive, err := io.ReadAll(io.LimitReader(response.Body, (64<<20)+1))
	if err != nil {
		return fmt.Errorf("read compiler download: %w", err)
	}
	return s.publishTexCompiler(archive, asset.digest)
}

func (s *server) publishTexCompiler(archive []byte, digest string) error {
	if len(archive) > 64<<20 || fmt.Sprintf("%x", sha256.Sum256(archive)) != digest {
		return errors.New("compiler download failed its pinned SHA-256 verification")
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
		return os.Rename(file.Name(), filepath.Join(directory, "tectonic-"+texCompilerVersion))
	}
}

func (s *server) texPreview(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct{ Path, Content string }
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
		payload := s.texCompilerPayload()
		payload["code"] = "compiler_missing"
		jsonResponse(w, http.StatusOK, payload)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 150*time.Second)
	defer cancel()
	// Replaced browser requests cancel while queued too; their successor must not lose
	// its turn merely because the retiring engine has not released the shared cache yet.
	if err := s.texCompileMu.LockContext(ctx, nil); err != nil {
		jsonResponse(w, http.StatusOK, map[string]any{"code": "compile_cancelled", "error": err.Error()})
		return
	}
	defer s.texCompileMu.Unlock()
	pdf, err := s.compileTex(ctx, compiler, kind, body.Path, body.Content)
	if err != nil {
		jsonResponse(w, http.StatusOK, map[string]any{"code": "compile_failed", "error": err.Error(), "instructions": texInstallInstructions()})
		return
	}
	w.Header().Set("Content-Type", "application/pdf")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Disposition", `inline; filename="preview.pdf"`)
	w.Header().Set("Content-Length", strconv.Itoa(len(pdf)))
	_, _ = w.Write(pdf)
}

func (s *server) compileTex(ctx context.Context, compiler, kind, path, content string) ([]byte, error) {
	tools := s.texToolsDirectory()
	cache := filepath.Join(tools, "cache")
	if err := os.MkdirAll(cache, 0o700); err != nil {
		return nil, err
	}
	cache, err := filepath.EvalSymlinks(cache)
	if err != nil {
		return nil, err
	}
	job, err := os.MkdirTemp(tools, "preview-*")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(job)
	job, err = filepath.EvalSymlinks(job)
	if err != nil {
		return nil, err
	}
	args := []string{"--untrusted", "--outdir", job, "-"}
	name := "texput.pdf"
	project, err := filepath.EvalSymlinks(filepath.Dir(path))
	if err != nil {
		return nil, err
	}
	workingDirectory := project
	if kind == "pdflatex" {
		if err := os.WriteFile(filepath.Join(job, "preview.tex"), []byte(content), 0o600); err != nil {
			return nil, err
		}
		args = []string{"-fmt=pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "-output-directory=" + job, "preview.tex"}
		name = "preview.pdf"
		workingDirectory = job
	}
	var log texPreviewLog
	passes := 1
	if kind == "pdflatex" {
		passes = 2
	}
	for pass := 0; pass < passes; pass++ {
		command, err := texSandboxCommand(ctx, compiler, args, job, cache, project)
		if err != nil {
			return nil, err
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
			return nil, fmt.Errorf("TeX compilation failed: %w\n%s", errors.Join(ctx.Err(), err), string(log))
		}
	}
	file, err := os.Open(filepath.Join(job, name))
	if err != nil {
		return nil, fmt.Errorf("compiler produced no PDF: %w\n%s", err, string(log))
	}
	defer file.Close()
	pdf, err := io.ReadAll(io.LimitReader(file, texPDFLimit+1))
	if err != nil {
		return nil, err
	}
	if len(pdf) > texPDFLimit || !bytes.HasPrefix(pdf, []byte("%PDF-")) {
		return nil, errors.New("compiler output is not a PDF up to 32 MiB")
	}
	return pdf, nil
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
