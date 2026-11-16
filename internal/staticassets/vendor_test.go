// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package staticassets

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func vendorFixture(t *testing.T) ([]byte, vendorManifest) {
	t.Helper()
	var archive bytes.Buffer
	writer := zip.NewWriter(&archive)
	files := map[string]string{}
	for name, content := range map[string]string{"web/viewer.html": "fixture-viewer", "build/pdf.mjs": "fixture-module"} {
		file, err := writer.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = file.Write([]byte(content)); err != nil {
			t.Fatal(err)
		}
		files[name] = vendorDigest([]byte(content))
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return archive.Bytes(), vendorManifest{Version: "fixture-1", ArchiveSHA256: vendorDigest(archive.Bytes()), Files: files}
}

func writeVendorManifest(t *testing.T, root string, manifest vendorManifest) {
	t.Helper()
	directory := filepath.Join(root, "assets/runtime")
	if err := os.MkdirAll(directory, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "browser-assets.json"), []byte(`{}`), 0600); err != nil {
		t.Fatal(err)
	}
	data, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "pdfjs-assets.json"), data, 0600); err != nil {
		t.Fatal(err)
	}
}

func TestVendorDownloadRetriesAndFindsRenamedReleaseAsset(t *testing.T) {
	archive, manifest := vendorFixture(t)
	requests := map[string]int{}
	var service *httptest.Server
	service = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests[r.URL.Path]++
		switch r.URL.Path {
		case "/old-name.zip":
			http.NotFound(w, r)
		case "/releases":
			_ = json.NewEncoder(w).Encode([]any{map[string]any{"tag_name": "renamed-tag", "name": "PDF.js fixture-1", "assets": []any{map[string]string{"name": "renamed-dist.zip", "browser_download_url": service.URL + "/new-path.zip"}}}})
		case "/new-path.zip":
			if requests[r.URL.Path] == 1 {
				http.Error(w, "temporarily unavailable", http.StatusServiceUnavailable)
				return
			}
			_, _ = w.Write(archive)
		default:
			t.Errorf("unexpected request %s", r.URL.Path)
			http.NotFound(w, r)
		}
	}))
	defer service.Close()
	manifest.URLs = []string{service.URL + "/old-name.zip"}
	manifest.ReleaseAPI = service.URL + "/releases"
	cache := filepath.Join(t.TempDir(), "archive.zip")
	data, err := fetchVendorArchive(context.Background(), service.Client(), manifest, cache)
	if err != nil || !bytes.Equal(data, archive) {
		t.Fatalf("fallback download: %v", err)
	}
	if requests["/old-name.zip"] != 2 || requests["/new-path.zip"] != 2 {
		t.Fatalf("retry counts: %v", requests)
	}
	service.Close()
	if _, err := fetchVendorArchive(context.Background(), service.Client(), manifest, cache); err != nil {
		t.Fatalf("verified offline cache: %v", err)
	}
}

func TestVendorDownloadRejectsChangedBytesAndTriesAlternate(t *testing.T) {
	archive, manifest := vendorFixture(t)
	service := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/corrupt" {
			_, _ = w.Write([]byte("unreviewed upstream rebuild"))
			return
		}
		_, _ = w.Write(archive)
	}))
	defer service.Close()
	manifest.URLs = []string{service.URL + "/corrupt", service.URL + "/mirror"}
	data, err := fetchVendorArchive(context.Background(), service.Client(), manifest, filepath.Join(t.TempDir(), "cache.zip"))
	if err != nil || !bytes.Equal(data, archive) {
		t.Fatalf("alternate: %v", err)
	}
}

func TestVendorSetupIsOfflineAfterInstallationAndRepairsFromCache(t *testing.T) {
	archive, manifest := vendorFixture(t)
	service := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write(archive) }))
	manifest.URLs = []string{service.URL + "/dist.zip"}
	manifest.ReleaseAPI = service.URL + "/releases"
	t.Setenv("XDG_CACHE_HOME", t.TempDir())
	root := t.TempDir()
	writeVendorManifest(t, root, manifest)
	if err := SetupVendorAssets(context.Background(), root); err != nil {
		t.Fatal(err)
	}
	service.Close()
	if err := SetupVendorAssets(context.Background(), root); err != nil {
		t.Fatalf("offline installed copy: %v", err)
	}
	if err := os.WriteFile(filepath.Join(root, "static/vendor/pdfjs/web/viewer.html"), []byte("corrupt"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := SetupVendorAssets(context.Background(), root); err != nil {
		t.Fatalf("offline repair from cache: %v", err)
	}
	if !validVendorDirectory(filepath.Join(root, "static/vendor/pdfjs"), manifest.Files) {
		t.Fatal("repaired tree is invalid")
	}
}

func TestVendorFailurePreservesExistingAssets(t *testing.T) {
	_, manifest := vendorFixture(t)
	service := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Error(w, "gone", http.StatusGone) }))
	defer service.Close()
	manifest.URLs = []string{service.URL + "/gone.zip"}
	manifest.ReleaseAPI = service.URL + "/releases"
	t.Setenv("XDG_CACHE_HOME", t.TempDir())
	root := t.TempDir()
	writeVendorManifest(t, root, manifest)
	prior := filepath.Join(root, "static/vendor/pdfjs/web/viewer.html")
	if err := os.MkdirAll(filepath.Dir(prior), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(prior, []byte("prior working viewer"), 0600); err != nil {
		t.Fatal(err)
	}
	err := SetupVendorAssets(context.Background(), root)
	if err == nil || !strings.Contains(err.Error(), "existing assets preserved") {
		t.Fatalf("error=%v", err)
	}
	data, err := os.ReadFile(prior)
	if err != nil || string(data) != "prior working viewer" {
		t.Fatalf("prior tree changed: %q %v", data, err)
	}
}

func TestVendorManifestRejectsTraversalAndSymlink(t *testing.T) {
	for _, mode := range []string{"traversal", "symlink"} {
		t.Run(mode, func(t *testing.T) {
			root := t.TempDir()
			_, manifest := vendorFixture(t)
			if mode == "traversal" {
				manifest.Files = map[string]string{"../escape": strings.Repeat("a", 64)}
			} else {
				parent := filepath.Join(root, "static/vendor")
				if err := os.MkdirAll(parent, 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(t.TempDir(), filepath.Join(parent, "pdfjs")); err != nil {
					t.Fatal(err)
				}
			}
			writeVendorManifest(t, root, manifest)
			if err := SetupVendorAssets(context.Background(), root); err == nil {
				t.Fatal("unsafe manifest/path accepted")
			}
		})
	}
}

func writeBrowserManifest(t *testing.T, root string, manifest browserAssetManifest) {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(root, "assets/runtime"), 0700); err != nil {
		t.Fatal(err)
	}
	data, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "assets/runtime/browser-assets.json"), data, 0600); err != nil {
		t.Fatal(err)
	}
}

func TestBrowserAssetsFailedAcquisitionPreservesEveryOutput(t *testing.T) {
	root, cache := t.TempDir(), t.TempDir()
	service := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/good" {
			_, _ = w.Write([]byte("new"))
			return
		}
		_, _ = w.Write([]byte("untrusted partial bytes"))
	}))
	defer service.Close()
	manifest := browserAssetManifest{Files: map[string]browserFile{}}
	for i, url := range []string{"/good", "/bad"} {
		name := fmt.Sprintf("static/vendor/%d.js", i)
		wanted := "new"
		if i == 1 {
			wanted = "different expected bytes"
		}
		manifest.Files[name] = browserFile{vendorDigest([]byte(wanted)), []string{service.URL + url}}
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("working"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	writeBrowserManifest(t, root, manifest)
	if err := setupBrowserAssets(context.Background(), root, cache); err == nil {
		t.Fatal("accepted checksum mismatch")
	}
	for name := range manifest.Files {
		body, err := os.ReadFile(filepath.Join(root, name))
		if err != nil || string(body) != "working" {
			t.Fatalf("changed %s after failed acquisition: %q %v", name, body, err)
		}
	}
}

func TestBrowserAssetsConcurrentSetupAndOfflineRepair(t *testing.T) {
	root := t.TempDir()
	t.Setenv("XDG_CACHE_HOME", t.TempDir())
	archive, pdf := vendorFixture(t)
	service := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/pdf" {
			_, _ = w.Write(archive)
			return
		}
		_, _ = w.Write([]byte("browser fixture"))
	}))
	defer service.Close()
	pdf.URLs = []string{service.URL + "/pdf"}
	writeVendorManifest(t, root, pdf)
	manifest := browserAssetManifest{Files: map[string]browserFile{"static/vendor/library.js": {vendorDigest([]byte("browser fixture")), []string{service.URL + "/library"}}}}
	writeBrowserManifest(t, root, manifest)
	var wg sync.WaitGroup
	failures := make(chan error, 4)
	for range 4 {
		wg.Add(1)
		go func() { defer wg.Done(); failures <- SetupVendorAssets(context.Background(), root) }()
	}
	wg.Wait()
	close(failures)
	for err := range failures {
		if err != nil {
			t.Fatal(err)
		}
	}
	service.Close()
	for _, name := range []string{"static/vendor/library.js", "static/vendor/pdfjs/web/viewer.html"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte("corrupt"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	if err := SetupVendorAssets(context.Background(), root); err != nil {
		t.Fatalf("offline repair: %v", err)
	}
	if err := VerifyVendorAssets(root); err != nil {
		t.Fatal(err)
	}
}

func TestBrowserAssetsInterruptedDownloadPreservesInstallation(t *testing.T) {
	root, cache := t.TempDir(), t.TempDir()
	admitted := make(chan struct{})
	var once sync.Once
	service := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		once.Do(func() { close(admitted) })
		<-r.Context().Done()
	}))
	defer service.Close()
	writeBrowserManifest(t, root, browserAssetManifest{Files: map[string]browserFile{"static/vendor/library.js": {vendorDigest([]byte("new")), []string{service.URL}}}})
	path := filepath.Join(root, "static/vendor/library.js")
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("working"), 0600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- setupBrowserAssets(ctx, root, cache) }()
	select {
	case <-admitted:
	case <-time.After(5 * time.Second):
		t.Fatal("download not admitted")
	}
	cancel()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("interrupted download succeeded")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("cancellation did not stop acquisition")
	}
	body, err := os.ReadFile(path)
	if err != nil || string(body) != "working" {
		t.Fatalf("installation changed: %q %v", body, err)
	}
}

func TestBrowserAssetsRejectUnsafeManifestBeforeAcquisition(t *testing.T) {
	for _, name := range []string{"../escape", "static/../escape", "tools/entry.js", "/absolute", "static/vendor/link/file.js"} {
		t.Run(name, func(t *testing.T) {
			root, cache := t.TempDir(), t.TempDir()
			if strings.Contains(name, "link") {
				parent := filepath.Join(root, "static/vendor")
				if err := os.MkdirAll(parent, 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(t.TempDir(), filepath.Join(parent, "link")); err != nil {
					t.Fatal(err)
				}
			}
			writeBrowserManifest(t, root, browserAssetManifest{Files: map[string]browserFile{name: {strings.Repeat("a", 64), []string{"https://unreachable.invalid"}}}})
			if err := setupBrowserAssets(context.Background(), root, cache); err == nil {
				t.Fatal("unsafe output accepted")
			}
		})
	}
}

func setupBrowserAssets(ctx context.Context, root, cache string) error {
	outputs := map[string][]byte{}
	if err := prepareBrowserAssets(ctx, root, cache, outputs); err != nil {
		return err
	}
	_, err := Publish(root, outputs, false)
	return err
}

func TestVendorPDFErrorPreservesPreparedBrowserOutputs(t *testing.T) {
	root := t.TempDir()
	t.Setenv("XDG_CACHE_HOME", t.TempDir())
	cache := filepath.Join(os.Getenv("XDG_CACHE_HOME"), "yomux/vendor/browser")
	if err := os.MkdirAll(cache, 0700); err != nil {
		t.Fatal(err)
	}
	data := []byte("new browser bytes")
	digest := vendorDigest(data)
	if err := os.WriteFile(filepath.Join(cache, digest), data, 0600); err != nil {
		t.Fatal(err)
	}
	writeBrowserManifest(t, root, browserAssetManifest{Files: map[string]browserFile{"static/vendor/library.js": {digest, nil}}})
	if err := os.WriteFile(filepath.Join(root, "assets/runtime/pdfjs-assets.json"), []byte(`{}`), 0600); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "static/vendor/library.js")
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("working"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := SetupVendorAssets(context.Background(), root); err == nil {
		t.Fatal("invalid PDF manifest accepted")
	}
	body, err := os.ReadFile(path)
	if err != nil || string(body) != "working" {
		t.Fatalf("browser output changed on PDF failure: %q %v", body, err)
	}
}

func TestBrowserAssetsValidateEditorSourcesBeforeOutputOrCacheReuse(t *testing.T) {
	for _, useCache := range []bool{false, true} {
		t.Run(fmt.Sprint(useCache), func(t *testing.T) {
			root, cache := t.TempDir(), t.TempDir()
			body := []byte("existing editor")
			digest := vendorDigest(body)
			bundle := browserBundle{Directory: "codemirror", Output: "static/codemirror.js", SHA256: digest, InputSHA256: strings.Repeat("a", 64)}
			writeBrowserManifest(t, root, browserAssetManifest{Bundles: []browserBundle{bundle}})
			if useCache {
				if err := os.WriteFile(filepath.Join(cache, digest), body, 0600); err != nil {
					t.Fatal(err)
				}
			} else {
				if err := os.Mkdir(filepath.Join(root, "static"), 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(filepath.Join(root, bundle.Output), body, 0600); err != nil {
					t.Fatal(err)
				}
			}
			if err := setupBrowserAssets(context.Background(), root, cache); err == nil {
				t.Fatal("reused editor with missing custom sources")
			}
			source := filepath.Join(root, "tools/codemirror-bundle")
			if err := os.MkdirAll(source, 0700); err != nil {
				t.Fatal(err)
			}
			for _, name := range []string{"package.json", "package-lock.json", "codemirror-entry.js"} {
				if err := os.WriteFile(filepath.Join(source, name), []byte("edited"), 0600); err != nil {
					t.Fatal(err)
				}
			}
			if err := setupBrowserAssets(context.Background(), root, cache); err == nil || !strings.Contains(err.Error(), "inputs differ") {
				t.Fatalf("edited sources reused: %v", err)
			}
		})
	}
}
