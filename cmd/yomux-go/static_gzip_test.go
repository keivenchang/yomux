// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/testsupport"
)

func TestStaticTextAssetsAreGzippedOncePerVersion(t *testing.T) {
	root := t.TempDir()
	asset := filepath.Join(root, "static", "app.js")
	if err := os.MkdirAll(filepath.Dir(asset), 0o700); err != nil {
		t.Fatal(err)
	}
	first := strings.Repeat("console.log('first');\n", 500)
	if err := os.WriteFile(asset, []byte(first), 0o600); err != nil {
		t.Fatal(err)
	}
	s := &server{root: root}
	get := func(headers map[string]string) *httptest.ResponseRecorder {
		request := httptest.NewRequest(http.MethodGet, "/static/app.js", nil)
		for name, value := range headers {
			request.Header.Set(name, value)
		}
		recorder := httptest.NewRecorder()
		s.static(recorder, request)
		return recorder
	}
	gunzip := func(response *httptest.ResponseRecorder) string {
		t.Helper()
		reader, err := gzip.NewReader(response.Body)
		if err != nil {
			t.Fatal(err)
		}
		body, err := io.ReadAll(reader)
		if err != nil {
			t.Fatal(err)
		}
		return string(body)
	}

	compressed := get(map[string]string{"Accept-Encoding": "br, gzip"})
	if compressed.Code != http.StatusOK || compressed.Header().Get("Content-Encoding") != "gzip" ||
		compressed.Header().Get("Vary") != "Accept-Encoding" || !strings.HasPrefix(compressed.Header().Get("Content-Type"), "text/javascript") {
		t.Fatalf("gzip response = %d %v", compressed.Code, compressed.Header())
	}
	if compressed.Body.Len() >= len(first)/4 {
		t.Fatalf("compressed size %d is not below a quarter of %d", compressed.Body.Len(), len(first))
	}
	if got := gunzip(compressed); got != first {
		t.Fatalf("decompressed body differs: %d bytes", len(got))
	}

	for name, headers := range map[string]map[string]string{
		"no gzip": {},
		"refused": {"Accept-Encoding": "gzip;q=0, identity"},
		"range":   {"Accept-Encoding": "gzip", "Range": "bytes=0-6"},
	} {
		plain := get(headers)
		if plain.Header().Get("Content-Encoding") != "" || plain.Header().Get("Vary") != "Accept-Encoding" {
			t.Fatalf("%s: headers = %v", name, plain.Header())
		}
		if want := first; name == "range" {
			if plain.Body.String() != want[:7] {
				t.Fatalf("range body = %q", plain.Body.String())
			}
		} else if plain.Body.String() != want {
			t.Fatalf("%s: plain body differs", name)
		}
	}

	notModified := get(map[string]string{"Accept-Encoding": "gzip", "If-Modified-Since": compressed.Header().Get("Last-Modified")})
	if notModified.Code != http.StatusNotModified || notModified.Body.Len() != 0 {
		t.Fatalf("conditional request = %d with %d bytes", notModified.Code, notModified.Body.Len())
	}

	// A rebuild changes the version, so the next request compresses the new bytes.
	second := strings.Repeat("console.log('second');\n", 500)
	if err := os.WriteFile(asset, []byte(second), 0o600); err != nil {
		t.Fatal(err)
	}
	later := time.Now().Add(time.Minute)
	if err := os.Chtimes(asset, later, later); err != nil {
		t.Fatal(err)
	}
	if got := gunzip(get(map[string]string{"Accept-Encoding": "gzip"})); got != second {
		t.Fatalf("rebuilt asset served stale bytes: %d bytes", len(got))
	}
}

func TestStaticGzipResponseDescribesTheCompressedRepresentation(t *testing.T) {
	root := t.TempDir()
	sourceMap := filepath.Join(root, "static", "yomux.js.map")
	if err := os.MkdirAll(filepath.Dir(sourceMap), 0o700); err != nil {
		t.Fatal(err)
	}
	body := `{"version":3,"sources":["a.js"],"mappings":"` + strings.Repeat("AAAA;", 2000) + `"}`
	if err := os.WriteFile(sourceMap, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	s := &server{root: root}
	request := func(method string, headers map[string]string) *httptest.ResponseRecorder {
		t.Helper()
		r := httptest.NewRequest(method, "/static/yomux.js.map", nil)
		for name, value := range headers {
			r.Header.Set(name, value)
		}
		recorder := httptest.NewRecorder()
		s.static(recorder, r)
		return recorder
	}
	compressed := request(http.MethodGet, map[string]string{"Accept-Encoding": "gzip"})
	header := compressed.Header()
	if compressed.Code != http.StatusOK || header.Get("Content-Encoding") != "gzip" || header.Get("Content-Type") != "application/json" {
		t.Fatalf("source map response = %d %v", compressed.Code, header)
	}
	if header.Get("Content-Length") != strconv.Itoa(compressed.Body.Len()) {
		t.Fatalf("Content-Length %q for %d compressed bytes", header.Get("Content-Length"), compressed.Body.Len())
	}
	if header.Get("Accept-Ranges") != "" {
		t.Fatalf("compressed body advertises ranges: %q", header.Get("Accept-Ranges"))
	}
	etag := header.Get("ETag")
	if !strings.HasSuffix(etag, `-gzip"`) || header.Get("Vary") != "Accept-Encoding" {
		t.Fatalf("ETag %q Vary %q", etag, header.Get("Vary"))
	}
	if got := gunzipBytes(t, compressed.Body.Bytes()); got != body {
		t.Fatalf("decompressed source map differs: %d bytes", len(got))
	}

	head := request(http.MethodHead, map[string]string{"Accept-Encoding": "gzip"})
	if head.Code != http.StatusOK || head.Body.Len() != 0 || head.Header().Get("Content-Length") != header.Get("Content-Length") {
		t.Fatalf("HEAD = %d %v with %d bytes", head.Code, head.Header(), head.Body.Len())
	}
	cached := request(http.MethodGet, map[string]string{"Accept-Encoding": "gzip", "If-None-Match": etag})
	if cached.Code != http.StatusNotModified || cached.Body.Len() != 0 || cached.Header().Get("Content-Length") != "" {
		t.Fatalf("matching ETag = %d %v", cached.Code, cached.Header())
	}
	stale := request(http.MethodGet, map[string]string{"Accept-Encoding": "gzip", "If-None-Match": `"other-gzip"`})
	if stale.Code != http.StatusOK || stale.Body.Len() != compressed.Body.Len() {
		t.Fatalf("different ETag = %d with %d bytes", stale.Code, stale.Body.Len())
	}
	plain := request(http.MethodGet, nil)
	if plain.Header().Get("ETag") == etag || plain.Body.String() != body {
		t.Fatalf("plain response reused the gzip ETag or body: %v", plain.Header())
	}
	ranged := request(http.MethodGet, map[string]string{"Accept-Encoding": "gzip", "Range": "bytes=0-9"})
	if ranged.Code != http.StatusPartialContent || ranged.Header().Get("Content-Encoding") != "" || ranged.Body.String() != body[:10] {
		t.Fatalf("range = %d %v %q", ranged.Code, ranged.Header(), ranged.Body.String())
	}
}

func TestStaticBinaryAssetsAreNotGzipped(t *testing.T) {
	root := t.TempDir()
	font := filepath.Join(root, "static", "fonts", "ui.woff2")
	if err := os.MkdirAll(filepath.Dir(font), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(font, []byte("already compressed"), 0o600); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodGet, "/static/fonts/ui.woff2", nil)
	request.Header.Set("Accept-Encoding", "gzip")
	recorder := httptest.NewRecorder()
	(&server{root: root}).static(recorder, request)
	if recorder.Header().Get("Content-Encoding") != "" || recorder.Body.String() != "already compressed" {
		t.Fatalf("font response = %v %q", recorder.Header(), recorder.Body.String())
	}
}

func gunzipBytes(t *testing.T, data []byte) string {
	t.Helper()
	reader, err := gzip.NewReader(bytes.NewReader(data))
	if err != nil {
		t.Fatal(err)
	}
	body, err := io.ReadAll(reader)
	if err != nil {
		t.Fatal(err)
	}
	return string(body)
}

// A rebuild that keeps the size and even the modification time must still never serve the old
// compressed bytes. The build replaces files by rename, which gives the path a new inode.
func TestStaticGzipCacheFollowsSameSizeRebuilds(t *testing.T) {
	dir := t.TempDir()
	asset := filepath.Join(dir, "app.js")
	versions := []string{strings.Repeat("let a = 1;\n", 400), strings.Repeat("let b = 2;\n", 400), strings.Repeat("let c = 3;\n", 400)}
	if err := os.WriteFile(asset, []byte(versions[0]), 0o600); err != nil {
		t.Fatal(err)
	}
	var cache memoryCache
	load := func() string {
		t.Helper()
		info, err := os.Stat(asset)
		if err != nil {
			t.Fatal(err)
		}
		data, err := cache.loadGzip(asset, info, false)
		if err != nil {
			t.Fatal(err)
		}
		return gunzipBytes(t, data)
	}
	if got := load(); got != versions[0] {
		t.Fatal("first version differs")
	}
	original, err := os.Stat(asset)
	if err != nil {
		t.Fatal(err)
	}

	replacement := filepath.Join(dir, ".app.js.tmp")
	if err := os.WriteFile(replacement, []byte(versions[1]), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(replacement, original.ModTime(), original.ModTime()); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(replacement, asset); err != nil {
		t.Fatal(err)
	}
	if got := load(); got != versions[1] {
		t.Fatal("rename with the same size and time served the old version")
	}

	// An in-place rewrite keeps the inode but moves the modification time.
	if err := os.WriteFile(asset, []byte(versions[2]), 0o600); err != nil {
		t.Fatal(err)
	}
	later := original.ModTime().Add(time.Second)
	if err := os.Chtimes(asset, later, later); err != nil {
		t.Fatal(err)
	}
	if got := load(); got != versions[2] {
		t.Fatal("in-place rewrite served the old version")
	}
	cache.mu.Lock()
	defer cache.mu.Unlock()
	if len(cache.entries) != 1 || cache.bytes != int64(len(cache.entries[asset].data)) {
		t.Fatalf("cache holds %d entries and %d bytes; want only the current version", len(cache.entries), cache.bytes)
	}
}

func TestStaticGzipCompressionRefusesAFileThatChangedMeanwhile(t *testing.T) {
	asset := filepath.Join(t.TempDir(), "app.js")
	if err := os.WriteFile(asset, []byte("old"), 0o600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(asset)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(asset, []byte("new"), 0o600); err != nil {
		t.Fatal(err)
	}
	later := info.ModTime().Add(time.Second)
	if err := os.Chtimes(asset, later, later); err != nil {
		t.Fatal(err)
	}
	var cache memoryCache
	if _, err := cache.loadGzip(asset, info, false); err == nil {
		t.Fatal("compressed new bytes under the old version")
	}
	if len(cache.entries) != 0 {
		t.Fatal("a failed compression stayed cached")
	}
}

func TestStaticGzipCacheKeepsItsBudget(t *testing.T) {
	dir := t.TempDir()
	write := func(name string, seed int) (string, os.FileInfo) {
		t.Helper()
		// Varied text so each file compresses to a few kilobytes.
		var text strings.Builder
		for index := 0; index < 4000; index++ {
			text.WriteString(strconv.Itoa(index*7919 + seed))
		}
		path := filepath.Join(dir, name)
		if err := os.WriteFile(path, []byte(text.String()), 0o600); err != nil {
			t.Fatal(err)
		}
		info, err := os.Stat(path)
		if err != nil {
			t.Fatal(err)
		}
		return path, info
	}
	paths, infos := make([]string, 3), make([]os.FileInfo, 3)
	for index := range paths {
		paths[index], infos[index] = write(fmt.Sprintf("%d.js", index), index)
	}
	var cache memoryCache
	first, err := cache.loadGzip(paths[0], infos[0], false)
	if err != nil {
		t.Fatal(err)
	}
	size := int64(len(first))
	cache.setLimit(2*size + size/2)
	for _, index := range []int{1, 0, 2} {
		if _, err := cache.loadGzip(paths[index], infos[index], false); err != nil {
			t.Fatal(err)
		}
	}
	cache.mu.Lock()
	_, kept0 := cache.entries[paths[0]]
	_, kept1 := cache.entries[paths[1]]
	held := cache.bytes
	cache.mu.Unlock()
	if !kept0 || kept1 || held > 2*size+size/2 {
		t.Fatalf("after using 1, 0, 2: kept 0=%v 1=%v with %d bytes; want the least recent (1) dropped", kept0, kept1, held)
	}

	// Warm-up never pushes out a file a browser already asked for.
	if _, err := cache.loadGzip(paths[1], infos[1], true); err != nil {
		t.Fatal(err)
	}
	cache.mu.Lock()
	_, warmed := cache.entries[paths[1]]
	_, still0 := cache.entries[paths[0]]
	cache.mu.Unlock()
	if warmed || !still0 {
		t.Fatalf("warm-up admitted=%v and kept 0=%v; want no eviction", warmed, still0)
	}

	cache.setLimit(0)
	data, err := cache.loadGzip(paths[2], infos[2], false)
	if err != nil || len(data) == 0 {
		t.Fatalf("a zero budget must still compress: %d bytes, %v", len(data), err)
	}
	if len(cache.entries) != 0 || cache.bytes != 0 {
		t.Fatalf("a zero budget holds %d entries and %d bytes", len(cache.entries), cache.bytes)
	}
}

func TestStaticGzipWarmUpFillsTheCacheBeforeAnyRequest(t *testing.T) {
	s := nativeSettingsFixtureServer(t)
	s.root = t.TempDir()
	schema, err := os.ReadFile(filepath.Join(testsupport.RepoRoot(t), "assets", "runtime", "settings-schema.json"))
	if err != nil {
		t.Fatal(err)
	}
	for name, body := range map[string]string{
		"assets/runtime/settings-schema.json": string(schema),
		"static/yomux.js":                     strings.Repeat("app();\n", 1000),
		"static/vendor/lib.css":               strings.Repeat("a{b:c}\n", 1000),
		"static/.yomux.js.partial":            "half a build",
		"static/fonts/ui.woff2":               "binary",
	} {
		path := filepath.Join(s.root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	s.warmStaticGzip(context.Background())
	s.memoryCache.mu.Lock()
	cached := []string{}
	for path, entry := range s.memoryCache.entries {
		if entry.stored {
			relative, err := filepath.Rel(filepath.Join(s.root, "static"), path)
			if err != nil {
				t.Fatal(err)
			}
			cached = append(cached, relative)
		}
	}
	limit := s.memoryCache.limitLocked()
	s.memoryCache.mu.Unlock()
	sort.Strings(cached)
	if strings.Join(cached, ",") != "vendor/lib.css,yomux.js" {
		t.Fatalf("warm-up cached %v; want only the text assets", cached)
	}
	if limit != 64<<20 {
		t.Fatalf("default budget = %d; want 64 MB from Preferences", limit)
	}
}

func TestStaticGzipBudgetFollowsPreferences(t *testing.T) {
	s := nativeSettingsFixtureServer(t)
	payload, err := s.nativeSettingsPayload(context.Background(), map[string]any{"performance": map[string]any{"static_cache_mb": 5000}}, true)
	if err != nil {
		t.Fatal(err)
	}
	if limit, ok := staticGzipLimitFromSettings(payload); !ok || limit != 1024<<20 {
		t.Fatalf("limit = %d %v; want the 1024 MB ceiling", limit, ok)
	}
	payload, err = s.nativeSettingsPayload(context.Background(), map[string]any{"performance": map[string]any{"static_cache_mb": 0}}, true)
	if err != nil {
		t.Fatal(err)
	}
	s.applyStaticGzipSettings(payload)
	if s.memoryCache.limitLocked() != 0 {
		t.Fatalf("limit = %d; want 0", s.memoryCache.limitLocked())
	}
}

// Compressed app files and compiled previews share one budget and one least-recently-used order.
func TestMemoryCacheSharesOneBudgetAcrossKinds(t *testing.T) {
	dir := t.TempDir()
	files := make([]string, 2)
	infos := make([]os.FileInfo, 2)
	for index := range files {
		var text strings.Builder
		for line := 0; line < 4000; line++ {
			text.WriteString(strconv.Itoa(line*7919 + index))
		}
		files[index] = filepath.Join(dir, fmt.Sprintf("%d.js", index))
		if err := os.WriteFile(files[index], []byte(text.String()), 0o600); err != nil {
			t.Fatal(err)
		}
		info, err := os.Stat(files[index])
		if err != nil {
			t.Fatal(err)
		}
		infos[index] = info
	}
	var cache memoryCache
	first, err := cache.loadGzip(files[0], infos[0], false)
	if err != nil {
		t.Fatal(err)
	}
	size := int64(len(first))
	cache.setLimit(2*size + size/2)
	cache.put(memoryCacheTeX, "tex:a", make([]byte, size), nil, size)
	// The gzip file was used before the PDF, so the next file pushes out the gzip file.
	if _, err := cache.loadGzip(files[1], infos[1], false); err != nil {
		t.Fatal(err)
	}
	if _, _, _, ok := cache.get("tex:a"); !ok {
		t.Fatal("the more recently used PDF was evicted before an older app file")
	}
	if _, kept := cache.entries[files[0]]; kept {
		t.Fatal("the least recently used app file was kept")
	}
	// get refreshed the PDF, so an Office PDF now pushes out the remaining app file.
	cache.put(memoryCacheOffice, "office:a", make([]byte, size), nil, size)
	if _, kept := cache.entries[files[1]]; kept {
		t.Fatal("an older app file outlived a newer preview")
	}
	if _, _, _, ok := cache.get("tex:a"); !ok {
		t.Fatal("a recently used PDF was evicted")
	}
	// An entry larger than the whole budget is not kept and evicts nothing.
	cache.put(memoryCacheTeX, "tex:huge", make([]byte, 3*size), nil, 3*size)
	if _, _, _, ok := cache.get("tex:huge"); ok {
		t.Fatal("an entry larger than the budget was cached")
	}
	kinds := cache.snapshot()["kinds"].(map[string]memoryCacheStats)
	if kinds[memoryCacheGzip].Evictions != 2 || kinds[memoryCacheTeX].Evictions != 0 || kinds[memoryCacheOffice].Evictions != 0 ||
		kinds[memoryCacheTeX].Entries != 1 || kinds[memoryCacheOffice].Entries != 1 || kinds[memoryCacheGzip].Entries != 0 ||
		cache.bytes != 2*size || kinds[memoryCacheTeX].Bytes != size {
		t.Fatalf("counters %+v bytes=%d", kinds, cache.bytes)
	}
}
