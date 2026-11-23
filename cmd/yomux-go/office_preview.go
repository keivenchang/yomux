// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"archive/zip"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"time"
)

const officeInputLimit = 25 << 20
const officePDFLimit = 50 << 20
const officePreviewTimeout = 30 * time.Second

func officeInstallInstructions() string {
	if runtime.GOOS == "darwin" {
		return "On the YOmux server Mac: brew install --cask libreoffice. Native Office preview isolation has not yet been qualified on macOS; download the original to read it locally."
	}
	return "On the YOmux server, install LibreOffice and bubblewrap (Debian/Ubuntu: sudo apt install libreoffice bubblewrap util-linux). Bubblewrap must be permitted to create user and network namespaces. Then press Retry."
}

func officePreviewError(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Cache-Control", "no-store")
	jsonResponse(w, status, map[string]any{"code": code, "error": message, "instructions": officeInstallInstructions()})
}

func validateOfficeInput(data []byte, extension string) error {
	if bytes.HasPrefix(data, []byte{0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1}) {
		return errors.New("encrypted or legacy Office containers cannot be previewed")
	}
	archive, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return fmt.Errorf("malformed Office document: %w", err)
	}
	required := "word/document.xml"
	if extension == ".pptx" {
		required = "ppt/presentation.xml"
	}
	found := false
	for _, file := range archive.File {
		if file.Flags&1 != 0 {
			return errors.New("encrypted Office documents cannot be previewed")
		}
		if strings.HasSuffix(strings.ToLower(file.Name), "vbaproject.bin") {
			return errors.New("macro-enabled documents cannot be previewed")
		}
		if file.Name == required {
			found = true
		}
	}
	if !found {
		return errors.New("unsupported Office document contents")
	}
	return nil
}

func (s *server) officePreview(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		officePreviewError(w, 405, "method_not_allowed", "Office preview requires POST.")
		return
	}
	var body struct {
		Path string `json:"path"`
	}
	if err := decodeJSONBody(r.Body, 16<<10, &body); err != nil {
		officePreviewError(w, 400, "invalid_request", "Preview requires a file path.")
		return
	}
	ext := strings.ToLower(filepath.Ext(body.Path))
	if ext != ".docx" && ext != ".pptx" {
		officePreviewError(w, 415, "unsupported_format", "Preview supports DOCX and PPTX files.")
		return
	}
	// Admission precedes the read so queued requests cannot retain unbounded document buffers.
	s.officePreviewMu.Lock()
	if s.officePreviewActive >= 2 {
		s.officePreviewMu.Unlock()
		officePreviewError(w, 429, "preview_busy", "Two Office conversions are already running. Press Retry when one finishes.")
		return
	}
	s.officePreviewActive++
	s.officePreviewMu.Unlock()
	defer func() { s.officePreviewMu.Lock(); s.officePreviewActive--; s.officePreviewMu.Unlock() }()
	ctx, cancel := context.WithTimeout(r.Context(), officePreviewTimeout)
	defer cancel()
	data, _, _, readErr := s.finder.Raw(ctx, body.Path, officeInputLimit)
	if readErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, readErr)
		return
	}
	if err := validateOfficeInput(data, ext); err != nil {
		officePreviewError(w, 422, "unsupported_document", err.Error())
		return
	}
	compiler, err := exec.LookPath("libreoffice")
	if err != nil && runtime.GOOS == "darwin" {
		compiler = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
		_, err = os.Stat(compiler)
	}
	if err != nil {
		officePreviewError(w, 503, "converter_missing", "LibreOffice is not installed on the server.")
		return
	}
	pdf, err := convertOfficePreview(ctx, compiler, ext, data)
	if err != nil {
		code, status := "conversion_failed", 422
		if errors.Is(err, context.DeadlineExceeded) {
			code, status = "conversion_timeout", 504
		} else if errors.Is(err, context.Canceled) {
			code, status = "conversion_canceled", 408
		} else if errors.Is(err, errOfficeSandbox) {
			code, status = "sandbox_unavailable", 503
		} else if errors.Is(err, errOfficePDFLimit) {
			code, status = "pdf_too_large", 413
		}
		officePreviewError(w, status, code, err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/pdf")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(http.StatusOK)
	if _, err := w.Write(pdf); err != nil {
		slog.Warn("Office preview response failed", "error", err)
	}
}

var errOfficeSandbox = errors.New("Office preview sandbox unavailable")
var errOfficePDFLimit = errors.New("converted PDF exceeds 50 MiB")

func officeSandboxCommand(ctx context.Context, compiler, job string, args []string) (*exec.Cmd, error) {
	// macOS stays fail-closed until real LibreOffice containment and Chromium qualification.
	if runtime.GOOS != "linux" {
		return nil, fmt.Errorf("%w on %s", errOfficeSandbox, runtime.GOOS)
	}
	wrapper, err := exec.LookPath("bwrap")
	if err != nil {
		return nil, fmt.Errorf("%w: bubblewrap is required", errOfficeSandbox)
	}
	limiter, err := exec.LookPath("prlimit")
	if err != nil {
		return nil, fmt.Errorf("%w: prlimit is required", errOfficeSandbox)
	}
	compiler, err = filepath.EvalSymlinks(compiler)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", errOfficeSandbox, err)
	}
	// Only the distribution runtime and fonts are readable. No project, operator home,
	// host sockets, credentials, or DNS configuration enter the private network namespace.
	wrapped := []string{"--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL", "--clearenv", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"}
	for _, path := range []string{"/usr/bin", "/usr/lib", "/usr/lib64", "/usr/share", "/bin", "/lib", "/lib64", "/etc/fonts", "/etc/libreoffice", "/var/lib/libreoffice", "/var/cache/fontconfig"} {
		if _, err := os.Stat(path); err == nil {
			wrapped = append(wrapped, "--ro-bind", path, path)
		}
	}
	if !strings.HasPrefix(compiler, "/usr/") {
		return nil, fmt.Errorf("%w: install the distribution LibreOffice under /usr", errOfficeSandbox)
	}
	wrapped = append(wrapped, "--bind", job, "/job", "--chdir", "/job", "--setenv", "HOME", "/job/home", "--setenv", "TMPDIR", "/tmp", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "LANG", "C.UTF-8", "--setenv", "SAL_USE_VCLPLUGIN", "svp", "--", compiler)
	command := exec.CommandContext(ctx, limiter, append([]string{fmt.Sprintf("--fsize=%d", officePDFLimit), "--", wrapper}, append(wrapped, args...)...)...)
	command.Env = []string{"PATH=/usr/bin:/bin", "LANG=C.UTF-8"}
	command.SysProcAttr = nativeAgentProcessAttributes()
	command.Cancel = func() error { return stopNativeAgentProcess(command) }
	command.WaitDelay = 2 * time.Second
	return command, nil
}

func convertOfficePreview(ctx context.Context, compiler, ext string, data []byte) ([]byte, error) {
	job, err := os.MkdirTemp("", "yomux-office-*")
	if err != nil {
		return nil, err
	}
	defer func() {
		if err := os.RemoveAll(job); err != nil {
			slog.Warn("Office preview cleanup failed", "error", err)
		}
	}()
	for _, path := range []string{"home", "profile/user", "out"} {
		if err := os.MkdirAll(filepath.Join(job, path), 0o700); err != nil {
			return nil, err
		}
	}
	profile := `<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item><item oor:path="/org.openoffice.Office.Common/Load"><prop oor:name="UpdateDocMode" oor:op="fuse"><value>0</value></prop></item></oor:items>`
	if err := os.WriteFile(filepath.Join(job, "profile/user/registrymodifications.xcu"), []byte(profile), 0o600); err != nil {
		return nil, err
	}
	if err := os.WriteFile(filepath.Join(job, "document"+ext), data, 0o600); err != nil {
		return nil, err
	}
	command, err := officeSandboxCommand(ctx, compiler, job, []string{"-env:UserInstallation=file:///job/profile", "--headless", "--nologo", "--nodefault", "--norestore", "--nolockcheck", "--convert-to", "pdf", "--outdir", "/job/out", "/job/document" + ext})
	if err != nil {
		return nil, err
	}
	var log texPreviewLog
	command.Stdout, command.Stderr = &log, &log
	if err := command.Run(); err != nil {
		if _, outputErr := readOfficePDF(ctx, job); errors.Is(outputErr, errOfficePDFLimit) {
			return nil, outputErr
		}
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		if strings.Contains(string(log), "bwrap:") {
			return nil, fmt.Errorf("%w: %s", errOfficeSandbox, string(log))
		}
		return nil, fmt.Errorf("LibreOffice conversion failed: %w: %s", err, string(log))
	}
	return readOfficePDF(ctx, job)
}

func readOfficePDF(ctx context.Context, job string) ([]byte, error) {
	root, err := os.OpenRoot(job)
	if err != nil {
		return nil, err
	}
	defer root.Close()
	// The converter controls every descendant of job, including intermediate symlinks.
	// Root confinement plus a nonblocking no-follow leaf prevents host reads and FIFO stalls.
	file, err := root.OpenFile("out/document.pdf", os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, fmt.Errorf("LibreOffice produced no readable PDF: %w", err)
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, errors.New("converter output is not a regular file")
	}
	if info.Size() >= officePDFLimit {
		return nil, errOfficePDFLimit
	}
	var pdf bytes.Buffer
	buffer := make([]byte, 64<<10)
	for {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		n, err := file.Read(buffer)
		if pdf.Len()+n >= officePDFLimit {
			return nil, errOfficePDFLimit
		}
		pdf.Write(buffer[:n])
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, err
		}
	}
	if !bytes.HasPrefix(pdf.Bytes(), []byte("%PDF-")) {
		return nil, errors.New("converter output is not a PDF")
	}
	return pdf.Bytes(), nil
}
