// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"cmp"
	"compress/gzip"
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"
)

// Text assets are large (the app bundle is about 5 MB) and gzip to about a quarter of that, which
// matters on a slow link. Each file version is compressed once and kept in the shared memory cache,
// keyed by its path, so it competes for performance.static_cache_mb with compiled previews.

// The page asks for each asset as ?v=<mtime>-<size>, so the browser keys its copy on the same
// version the cache does. The inode also changes when the build replaces a file by rename.
func sameStaticVersion(a, b os.FileInfo) bool {
	return os.SameFile(a, b) && a.ModTime().Equal(b.ModTime()) && a.Size() == b.Size()
}

// sameFileVersion also compares the change time, which catches same-size edits that restore the
// modification time (cp -p, rsync -t). It never matches where the platform lacks a change time.
func sameFileVersion(recorded, current os.FileInfo) bool {
	changed := fileChangeTimeNS(recorded)
	return changed != 0 && changed == fileChangeTimeNS(current) && sameStaticVersion(recorded, current)
}

// A recorded file version is trusted only once its last change is older than any filesystem
// timestamp granularity, so a later write cannot share every stat field with it.
const fileSettleWindow = 2 * time.Second

// fileSettleClock is the clock settled versions are judged by; tests move it so fresh fixture
// files count as settled.
var fileSettleClock = time.Now

// fileVersionSettled reports whether info last changed at least fileSettleWindow before now.
func fileVersionSettled(info os.FileInfo, now time.Time) bool {
	changed := fileChangeTimeNS(info)
	return changed != 0 && now.Sub(time.Unix(0, changed)) > fileSettleWindow
}

func staticAssetCompressible(path string) bool {
	switch filepath.Ext(path) {
	case ".js", ".css", ".json", ".svg", ".map":
		return true
	}
	return false
}

// acceptsGzip reports whether an Accept-Encoding header allows gzip with a nonzero quality.
func acceptsGzip(header string) bool {
	for _, part := range strings.Split(header, ",") {
		name, params, _ := strings.Cut(part, ";")
		if !strings.EqualFold(strings.TrimSpace(name), "gzip") {
			continue
		}
		for _, param := range strings.Split(params, ";") {
			key, value, found := strings.Cut(strings.TrimSpace(param), "=")
			if found && strings.EqualFold(strings.TrimSpace(key), "q") {
				quality, err := strconv.ParseFloat(strings.TrimSpace(value), 64)
				return err == nil && quality > 0
			}
		}
		return true
	}
	return false
}

// staticGzipWriter completes ServeContent's headers for a precompressed body. ServeContent leaves
// out Content-Length once Content-Encoding is set and always advertises byte ranges, but ranges
// are served only from the plain file.
type staticGzipWriter struct {
	http.ResponseWriter
	size int
}

func (w staticGzipWriter) WriteHeader(status int) {
	if status == http.StatusOK {
		w.Header().Del("Accept-Ranges")
		w.Header().Set("Content-Length", strconv.Itoa(w.size))
	}
	w.ResponseWriter.WriteHeader(status)
}

// staticContentType names the asset's own type, so the gzip bytes are never sniffed instead.
func staticContentType(path string) string {
	if contentType := mime.TypeByExtension(filepath.Ext(path)); contentType != "" {
		return contentType
	}
	if filepath.Ext(path) == ".map" {
		return "application/json"
	}
	return "application/octet-stream"
}

// serveStaticGzip writes the compressed file and reports whether it did. Range requests keep the
// plain file, because a byte range of compressed data is not a range of the asset.
func (s *server) serveStaticGzip(w http.ResponseWriter, r *http.Request, path string) bool {
	if r.Header.Get("Range") != "" || !acceptsGzip(r.Header.Get("Accept-Encoding")) {
		return false
	}
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		return false
	}
	data, err := s.memoryCache.loadGzip(path, info, false)
	if err != nil {
		slog.Warn("static asset sent uncompressed", "path", path, "error", err)
		return false
	}
	w.Header().Set("Content-Encoding", "gzip")
	w.Header().Set("Content-Type", staticContentType(path))
	// The compressed representation has its own validator, distinct from the plain file's.
	w.Header().Set("ETag", fmt.Sprintf(`"%x-%x-gzip"`, info.ModTime().UnixNano(), info.Size()))
	http.ServeContent(staticGzipWriter{ResponseWriter: w, size: len(data)}, r, "", info.ModTime(), bytes.NewReader(data))
	return true
}

// staticGzipLimitFromSettings reads the sanitized performance.static_cache_mb value.
func staticGzipLimitFromSettings(payload map[string]any) (int64, bool) {
	value, ok := settingsNumber(settingsObject(settingsObject(payload["settings"])["performance"])["static_cache_mb"])
	if !ok {
		return 0, false
	}
	return int64(value) << 20, true
}

func (s *server) applyStaticGzipSettings(payload map[string]any) {
	if limit, ok := staticGzipLimitFromSettings(payload); ok {
		s.memoryCache.setLimit(limit)
	}
}

// warmStaticGzip compresses the text assets at startup so the first page load after a restart
// does not wait for them. The bundles every page loads go first, in case the budget runs out.
func (s *server) warmStaticGzip(ctx context.Context) {
	started := time.Now()
	payload, err := s.nativeSettingsPayload(ctx, nil, false)
	if err != nil {
		slog.Warn("static asset cache uses the default size", "error", err)
	} else {
		s.applyStaticGzipSettings(payload)
	}
	root := filepath.Join(s.root, "static")
	var paths []string
	walkErr := filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		// The build writes each bundle to a hidden temporary file before renaming it into place.
		if strings.HasPrefix(entry.Name(), ".") && path != root {
			if entry.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if entry.Type().IsRegular() && staticAssetCompressible(path) {
			paths = append(paths, path)
		}
		return nil
	})
	if walkErr != nil {
		slog.Warn("static asset warm-up listed part of the files", "error", walkErr)
	}
	first := []string{"yomux.js", "yomux.css", "codemirror.js", "prosemirror.js"}
	rank := func(path string) int {
		if index := slices.Index(first, filepath.Base(path)); index >= 0 {
			return index
		}
		return len(first)
	}
	slices.SortStableFunc(paths, func(a, b string) int { return cmp.Compare(rank(a), rank(b)) })
	compressed := 0
	for _, path := range paths {
		if ctx.Err() != nil {
			return
		}
		info, err := os.Stat(path)
		if err != nil {
			continue
		}
		if _, err := s.memoryCache.loadGzip(path, info, true); err != nil {
			slog.Warn("static asset warm-up skipped a file", "path", path, "error", err)
			continue
		}
		compressed++
	}
	s.memoryCache.mu.Lock()
	cached := s.memoryCache.bytes
	s.memoryCache.mu.Unlock()
	slog.Info("static assets compressed", "files", compressed, "cached_bytes", cached, "elapsed", time.Since(started).Round(time.Millisecond))
}

// The file is checked before and after reading, by its open handle and by its path, so a rebuild
// during compression can never pair one version's bytes with another version's identity.
func gzipStaticFile(path string, info os.FileInfo) ([]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	compressed, compressErr := gzipOpenStaticFile(file, path, info)
	if err := errors.Join(compressErr, file.Close()); err != nil {
		return nil, err
	}
	return compressed, nil
}

func gzipOpenStaticFile(file *os.File, path string, info os.FileInfo) ([]byte, error) {
	unchanged := func() error {
		opened, err := file.Stat()
		if err != nil {
			return err
		}
		current, err := os.Stat(path)
		if err != nil {
			return err
		}
		if !sameStaticVersion(info, opened) || !sameStaticVersion(info, current) {
			return fmt.Errorf("asset changed while compressing")
		}
		return nil
	}
	if err := unchanged(); err != nil {
		return nil, err
	}
	var compressed bytes.Buffer
	writer := gzip.NewWriter(&compressed)
	if _, err := io.Copy(writer, file); err != nil {
		return nil, errors.Join(err, writer.Close())
	}
	if err := writer.Close(); err != nil {
		return nil, err
	}
	if err := unchanged(); err != nil {
		return nil, err
	}
	return compressed.Bytes(), nil
}
