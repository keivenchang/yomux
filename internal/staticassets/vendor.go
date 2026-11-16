// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package staticassets

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/keivenchang/yomux/internal/atomicfile"
)

const vendorArchiveLimit = 64 << 20

type vendorManifest struct {
	Version       string            `json:"version"`
	ArchiveSHA256 string            `json:"archive_sha256"`
	URLs          []string          `json:"urls"`
	ReleaseAPI    string            `json:"release_api"`
	Files         map[string]string `json:"files"`
}

func vendorDigest(data []byte) string { return fmt.Sprintf("%x", sha256.Sum256(data)) }

func validVendorDirectory(directory string, files map[string]string) bool {
	if err := ownedOutputPath(directory); err != nil {
		return false
	}
	for name, digest := range files {
		if err := ownedOutputPath(filepath.Join(directory, filepath.FromSlash(name))); err != nil {
			return false
		}
		info, err := os.Lstat(filepath.Join(directory, filepath.FromSlash(name)))
		if err != nil || !info.Mode().IsRegular() {
			return false
		}
		data, err := os.ReadFile(filepath.Join(directory, filepath.FromSlash(name)))
		if err != nil || vendorDigest(data) != digest {
			return false
		}
	}
	return true
}

func vendorGET(ctx context.Context, client *http.Client, address, accept string, limit int64) ([]byte, error) {
	parsed, err := url.Parse(address)
	if err != nil || parsed.Scheme != "https" && !(parsed.Scheme == "http" && (parsed.Hostname() == "127.0.0.1" || parsed.Hostname() == "localhost")) {
		return nil, fmt.Errorf("vendor download requires HTTPS: %s", address)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, address, nil)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Accept", accept)
	request.Header.Set("User-Agent", "yomux-asset-setup")
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("%s: HTTP %d", address, response.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > limit {
		return nil, fmt.Errorf("vendor response exceeds %d bytes", limit)
	}
	return data, nil
}

func vendorReleaseURLs(ctx context.Context, client *http.Client, manifest vendorManifest) ([]string, error) {
	// Discover asset URLs rather than assuming release names and paths remain
	// stable. The archive digest, not the release name, authorizes installation.
	data, err := vendorGET(ctx, client, manifest.ReleaseAPI+"?per_page=100", "application/vnd.github+json", 8<<20)
	if err != nil {
		return nil, err
	}
	var releases []struct {
		Tag    string `json:"tag_name"`
		Name   string `json:"name"`
		Assets []struct {
			Name       string `json:"name"`
			URL        string `json:"url"`
			BrowserURL string `json:"browser_download_url"`
		} `json:"assets"`
	}
	if err := json.Unmarshal(data, &releases); err != nil {
		return nil, err
	}
	var addresses []string
	for _, release := range releases {
		if !strings.Contains(release.Tag, manifest.Version) && !strings.Contains(release.Name, manifest.Version) {
			continue
		}
		for _, asset := range release.Assets {
			if !strings.HasSuffix(strings.ToLower(asset.Name), ".zip") {
				continue
			}
			for _, address := range []string{asset.BrowserURL, asset.URL} {
				if address != "" {
					addresses = append(addresses, address)
				}
			}
		}
	}
	if len(addresses) == 0 {
		return nil, fmt.Errorf("release %s has no downloadable ZIP assets", manifest.Version)
	}
	return addresses, nil
}

func fetchVendorArchive(ctx context.Context, client *http.Client, manifest vendorManifest, cache string) ([]byte, error) {
	var failures []error
	if data, err := os.ReadFile(cache); err == nil && vendorDigest(data) == manifest.ArchiveSHA256 {
		return data, nil
	}
	seen := map[string]bool{}
	attempt := func(addresses []string) ([]byte, bool) {
		for _, address := range addresses {
			if seen[address] {
				continue
			}
			seen[address] = true
			for retry := 0; retry < 2; retry++ {
				data, err := vendorGET(ctx, client, address, "application/octet-stream", vendorArchiveLimit)
				if err == nil && vendorDigest(data) != manifest.ArchiveSHA256 {
					err = fmt.Errorf("%s: archive SHA-256 mismatch", address)
				}
				if err == nil {
					return data, true
				}
				failures = append(failures, err)
				if ctx.Err() != nil {
					return nil, false
				}
			}
		}
		return nil, false
	}
	data, ok := attempt(manifest.URLs)
	if !ok && ctx.Err() == nil && manifest.ReleaseAPI != "" {
		addresses, err := vendorReleaseURLs(ctx, client, manifest)
		if err != nil {
			failures = append(failures, err)
		} else {
			data, ok = attempt(addresses)
		}
	}
	if !ok {
		return nil, fmt.Errorf("cannot obtain verified dependency %s; existing assets preserved; restore network access or review the pinned asset manifest: %w", manifest.Version, errors.Join(failures...))
	}
	if err := atomicfile.Write(cache, data); err != nil {
		return nil, fmt.Errorf("cache verified PDF.js archive: %w", err)
	}
	return data, nil
}

func extractVendorArchive(data []byte, destination string, files map[string]string) error {
	archive, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return err
	}
	seen := map[string]bool{}
	var total int64
	for _, entry := range archive.File {
		digest, wanted := files[entry.Name]
		if !wanted {
			continue
		}
		if seen[entry.Name] || !entry.Mode().IsRegular() {
			return fmt.Errorf("invalid vendor archive entry %q", entry.Name)
		}
		seen[entry.Name] = true
		source, err := entry.Open()
		if err != nil {
			return err
		}
		body, readErr := io.ReadAll(io.LimitReader(source, (32<<20)+1))
		closeErr := source.Close()
		if err := errors.Join(readErr, closeErr); err != nil {
			return err
		}
		total += int64(len(body))
		if len(body) > 32<<20 || total > 128<<20 || vendorDigest(body) != digest {
			return fmt.Errorf("invalid vendor content %q", entry.Name)
		}
		path := filepath.Join(destination, filepath.FromSlash(entry.Name))
		if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
			return err
		}
		if err := os.WriteFile(path, body, 0644); err != nil {
			return err
		}
	}
	if len(seen) != len(files) {
		return fmt.Errorf("vendor archive is missing required files: got %d, need %d", len(seen), len(files))
	}
	return nil
}

// preparePDFAssets acquires and validates privately; its caller owns publication.
func preparePDFAssets(ctx context.Context, root, cache string, outputs map[string][]byte) error {
	var manifest vendorManifest
	if err := readJSON(filepath.Join(root, "assets/runtime/pdfjs-assets.json"), &manifest); err != nil {
		return err
	}
	if manifest.Version == "" || !validDigest(manifest.ArchiveSHA256) || len(manifest.Files) == 0 {
		return errors.New("incomplete PDF.js asset manifest")
	}
	for name, digest := range manifest.Files {
		if !filepath.IsLocal(name) || filepath.ToSlash(filepath.Clean(name)) != name || !validDigest(digest) {
			return fmt.Errorf("invalid PDF.js manifest entry %q", name)
		}
		if _, err := assetOutput(root, "static/vendor/pdfjs/"+name); err != nil {
			return err
		}
	}
	if validVendorDirectory(filepath.Join(root, "static/vendor/pdfjs"), manifest.Files) {
		return nil
	}
	// Reuse the original PDF archive cache so existing prepared installs stay offline.
	pdfCache := filepath.Join(filepath.Dir(cache), "pdfjs")
	if err := ownedOutputPath(pdfCache); err != nil {
		return err
	}
	if err := os.MkdirAll(pdfCache, 0700); err != nil {
		return err
	}
	archive, err := fetchVendorArchive(ctx, &http.Client{Timeout: 20 * time.Second}, manifest, filepath.Join(pdfCache, manifest.ArchiveSHA256+".zip"))
	if err != nil {
		return err
	}
	stage, err := os.MkdirTemp(cache, ".pdfjs-stage-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stage)
	if err := extractVendorArchive(archive, stage, manifest.Files); err != nil {
		return err
	}
	for name := range manifest.Files {
		data, err := os.ReadFile(filepath.Join(stage, filepath.FromSlash(name)))
		if err != nil {
			return err
		}
		outputs["static/vendor/pdfjs/"+name] = data
	}
	return nil
}

// browserAssetManifest describes exact acquisition inputs and output bytes.
// Build and setup are the only callers; normal startup remains offline.
type browserAssetManifest struct {
	EmojiSHA256 string                 `json:"emoji_sha256"`
	Archives    []browserArchive       `json:"archives"`
	Files       map[string]browserFile `json:"files"`
	Bundles     []browserBundle        `json:"bundles"`
}
type browserFile struct {
	SHA256 string   `json:"sha256"`
	URLs   []string `json:"urls"`
}
type browserArchive struct {
	vendorManifest
	Members       map[string]string `json:"members"`
	NormalizeText []string          `json:"normalize_text"`
}
type browserBundle struct {
	Directory   string `json:"directory"`
	Output      string `json:"output"`
	SHA256      string `json:"sha256"`
	InputSHA256 string `json:"input_sha256"`
}

func validDigest(digest string) bool {
	decoded, err := hex.DecodeString(digest)
	return err == nil && len(decoded) == sha256.Size
}

func assetOutput(root, name string) (string, error) {
	if !filepath.IsLocal(name) || filepath.ToSlash(filepath.Clean(name)) != name ||
		(!strings.HasPrefix(name, "static/") && !strings.HasPrefix(name, "node_modules/.yomux/")) {
		return "", fmt.Errorf("unsafe browser asset path %q", name)
	}
	path := filepath.Join(root, filepath.FromSlash(name))
	return path, ownedOutputPath(path)
}

func extractBrowserArchive(data []byte, archive browserArchive) (map[string][]byte, error) {
	reader, err := gzip.NewReader(bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	defer reader.Close()
	input := tar.NewReader(reader)
	wanted := map[string]string{}
	for output, member := range archive.Members {
		if _, exists := wanted[member]; exists {
			return nil, fmt.Errorf("duplicate archive member %q", member)
		}
		wanted[member] = output
	}
	outputs := map[string][]byte{}
	var total int64
	for {
		entry, err := input.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, err
		}
		name, ok := wanted[entry.Name]
		if !ok {
			continue
		}
		if _, seen := outputs[name]; seen || entry.Typeflag != tar.TypeReg || entry.Size > 32<<20 {
			return nil, fmt.Errorf("invalid archive member %q", entry.Name)
		}
		body, err := io.ReadAll(io.LimitReader(input, (32<<20)+1))
		if err != nil {
			return nil, err
		}
		total += int64(len(body))
		if total > 128<<20 {
			return nil, errors.New("browser archive expands beyond limit")
		}
		for _, normalized := range archive.NormalizeText {
			if name == normalized {
				lines := strings.Split(strings.ReplaceAll(string(body), "\r\n", "\n"), "\n")
				for i := range lines {
					lines[i] = strings.TrimRight(lines[i], " \t")
				}
				body = []byte(strings.Join(lines, "\n"))
			}
		}
		if vendorDigest(body) != archive.Files[name] {
			return nil, fmt.Errorf("browser asset SHA-256 mismatch: %s", name)
		}
		outputs[name] = body
	}
	if len(outputs) != len(archive.Files) || len(wanted) != len(archive.Files) {
		return nil, errors.New("browser archive missing declared members")
	}
	return outputs, nil
}

func prepareBrowserAssets(ctx context.Context, root, cache string, outputs map[string][]byte) error {
	var manifest browserAssetManifest
	if err := readJSON(filepath.Join(root, "assets/runtime/browser-assets.json"), &manifest); err != nil {
		return err
	}
	expected := map[string]string{}
	add := func(name, digest string) error {
		if !validDigest(digest) {
			return fmt.Errorf("invalid asset SHA-256 for %s", name)
		}
		if _, exists := expected[name]; exists {
			return fmt.Errorf("duplicate browser output %s", name)
		}
		if _, err := assetOutput(root, name); err != nil {
			return err
		}
		expected[name] = digest
		return nil
	}
	for name, file := range manifest.Files {
		if err := add(name, file.SHA256); err != nil {
			return err
		}
	}
	for _, archive := range manifest.Archives {
		if !validDigest(archive.ArchiveSHA256) {
			return errors.New("invalid browser archive SHA-256")
		}
		for name, digest := range archive.Files {
			if err := add(name, digest); err != nil {
				return err
			}
		}
	}
	for _, bundle := range manifest.Bundles {
		if err := add(bundle.Output, bundle.SHA256); err != nil {
			return err
		}
	}
	client := &http.Client{Timeout: 30 * time.Second}
	existing := func(name, digest string) bool {
		body, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(name)))
		return err == nil && vendorDigest(body) == digest
	}
	for name, file := range manifest.Files {
		if existing(name, file.SHA256) {
			continue
		}
		data, err := fetchVendorArchive(ctx, client, vendorManifest{Version: name, ArchiveSHA256: file.SHA256, URLs: file.URLs}, filepath.Join(cache, file.SHA256))
		if err != nil {
			return err
		}
		outputs[name] = data
	}
	for _, archive := range manifest.Archives {
		complete := true
		for name, digest := range archive.Files {
			if !existing(name, digest) {
				complete = false
				break
			}
		}
		if complete {
			continue
		}
		data, err := fetchVendorArchive(ctx, client, archive.vendorManifest, filepath.Join(cache, archive.ArchiveSHA256))
		if err != nil {
			return err
		}
		files, err := extractBrowserArchive(data, archive)
		if err != nil {
			return err
		}
		for name, body := range files {
			outputs[name] = body
		}
	}
	for _, bundle := range manifest.Bundles {
		if err := verifyEditorInputs(root, bundle); err != nil {
			return err
		}
		if existing(bundle.Output, bundle.SHA256) {
			continue
		}
		cached := filepath.Join(cache, bundle.SHA256)
		body, err := os.ReadFile(cached)
		if err != nil || vendorDigest(body) != bundle.SHA256 {
			body, err = buildEditorBundle(ctx, root, cache, bundle)
			if err != nil {
				return err
			}
			if err := atomicfile.Write(cached, body); err != nil {
				return err
			}
		}
		outputs[bundle.Output] = body
	}
	if manifest.EmojiSHA256 != "" {
		name := "node_modules/.yomux/emoji_data.js"
		if err := add(name, manifest.EmojiSHA256); err != nil {
			return err
		}
		if !existing(name, manifest.EmojiSHA256) {
			body, err := buildEmojiCatalog(root, outputs)
			if err != nil {
				return err
			}
			if vendorDigest(body) != manifest.EmojiSHA256 {
				return errors.New("generated emoji catalog differs from reviewed checksum")
			}
			outputs[name] = body
		}
	}
	return nil
}

func verifyEditorInputs(root string, bundle browserBundle) error {
	if bundle.Directory != "codemirror" && bundle.Directory != "prosemirror" {
		return errors.New("unknown editor bundle")
	}
	var inputs []byte
	for _, name := range []string{"package.json", "package-lock.json", bundle.Directory + "-entry.js"} {
		data, err := os.ReadFile(filepath.Join(root, "tools", bundle.Directory+"-bundle", name))
		if err != nil {
			return err
		}
		inputs = append(inputs, data...)
	}
	if !validDigest(bundle.InputSHA256) || vendorDigest(inputs) != bundle.InputSHA256 {
		return fmt.Errorf("%s build inputs differ from reviewed manifest", bundle.Directory)
	}
	return nil
}

func buildEditorBundle(ctx context.Context, root, cache string, bundle browserBundle) ([]byte, error) {
	if bundle.Directory != "codemirror" && bundle.Directory != "prosemirror" {
		return nil, errors.New("unknown editor bundle")
	}
	stage, err := os.MkdirTemp(cache, ".editor-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(stage)
	source := filepath.Join(root, "tools", bundle.Directory+"-bundle")
	directory := filepath.Join(stage, "tools", bundle.Directory+"-bundle")
	if err := os.MkdirAll(directory, 0700); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Join(stage, "static"), 0700); err != nil {
		return nil, err
	}
	var inputs []byte
	for _, name := range []string{"package.json", "package-lock.json", bundle.Directory + "-entry.js"} {
		data, err := os.ReadFile(filepath.Join(source, name))
		if err != nil {
			return nil, err
		}
		inputs = append(inputs, data...)
		if err := os.WriteFile(filepath.Join(directory, name), data, 0600); err != nil {
			return nil, err
		}
	}
	if vendorDigest(inputs) != bundle.InputSHA256 {
		return nil, fmt.Errorf("%s build inputs differ from reviewed manifest", bundle.Directory)
	}
	for _, args := range [][]string{{"ci", "--ignore-scripts", "--no-audit", "--no-fund", "--prefer-offline"}, {"run", "build"}} {
		cmd := exec.CommandContext(ctx, "npm", args...)
		cmd.Dir = directory
		output, err := cmd.CombinedOutput()
		if err != nil {
			return nil, fmt.Errorf("build %s requires Node.js/npm and pinned packages (verified cache works offline): %w: %s", bundle.Directory, err, output)
		}
	}
	body, err := os.ReadFile(filepath.Join(stage, filepath.FromSlash(bundle.Output)))
	if err != nil {
		return nil, err
	}
	if vendorDigest(body) != bundle.SHA256 {
		return nil, fmt.Errorf("%s generated bytes differ from reviewed checksum", bundle.Output)
	}
	return body, nil
}

func SetupVendorAssets(ctx context.Context, checkout string) error {
	return SetupVendorAssetsWithPublisher(ctx, checkout, func(outputs map[string][]byte) error { _, err := Publish(checkout, outputs, false); return err })
}

// SetupVendorAssetsWithPublisher keeps verified cache ownership through publication.
// Checkout callers supply the same writer declaration used by other asset builds.
func SetupVendorAssetsWithPublisher(ctx context.Context, checkout string, publish func(map[string][]byte) error) error {
	cache, err := os.UserCacheDir()
	if err != nil {
		return err
	}
	cache = filepath.Join(cache, "yomux/vendor/browser")
	if err := ownedOutputPath(cache); err != nil {
		return err
	}
	if err := os.MkdirAll(cache, 0700); err != nil {
		return err
	}
	lock, err := os.OpenFile(filepath.Join(cache, "setup.lock"), os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return err
	}
	defer lock.Close()
	for {
		err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			break
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
			return err
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(127 * time.Millisecond):
		}
	}
	outputs := map[string][]byte{}
	if err := prepareBrowserAssets(ctx, checkout, cache, outputs); err != nil {
		return err
	}
	if err := preparePDFAssets(ctx, checkout, cache, outputs); err != nil {
		return err
	}
	// Every acquisition and checksum completes before the shared publisher writes.
	return publish(outputs)
}

func buildEmojiCatalog(root string, pending map[string][]byte) ([]byte, error) {
	read := func(name string) ([]byte, error) {
		name = "node_modules/.yomux/emoji/" + name
		if body, ok := pending[name]; ok {
			return body, nil
		}
		return os.ReadFile(filepath.Join(root, filepath.FromSlash(name)))
	}
	locales := []string{"ar", "de", "en", "es", "fr", "he", "hi", "it", "ja", "ko", "nl", "pl", "pt-BR", "ru", "th", "tr", "vi", "zh-Hans", "zh-Hant"}
	type annotation struct {
		Default []string `json:"default"`
		TTS     []string `json:"tts"`
	}
	catalogs := map[string]map[string]annotation{}
	for _, locale := range locales {
		merged := map[string]annotation{}
		for _, kind := range []string{"annotations", "annotationsDerived"} {
			body, err := read(locale + "-" + kind + ".json")
			if err != nil {
				return nil, err
			}
			var document map[string]struct {
				Annotations map[string]annotation `json:"annotations"`
			}
			if err := json.Unmarshal(body, &document); err != nil {
				return nil, err
			}
			catalog, ok := document[kind]
			if !ok {
				return nil, fmt.Errorf("missing %s for %s", kind, locale)
			}
			for glyph, entry := range catalog.Annotations {
				merged[glyph] = entry
			}
		}
		catalogs[locale] = merged
	}
	type record struct {
		Emoji    string              `json:"emoji"`
		Category string              `json:"category"`
		Names    map[string]string   `json:"names"`
		Keywords map[string][]string `json:"keywords"`
	}
	records := []record{}
	sequence, err := read("emoji-test.data")
	if err != nil {
		return nil, err
	}
	category := "symbols"
	seen := map[string]bool{}
	for _, line := range strings.Split(string(sequence), "\n") {
		if strings.HasPrefix(line, "# group:") {
			category = strings.ReplaceAll(strings.ReplaceAll(strings.ToLower(strings.TrimSpace(strings.TrimPrefix(line, "# group:"))), " & ", "-"), " ", "-")
			continue
		}
		data, description, ok := strings.Cut(line, "#")
		if !ok || !strings.Contains(data, "; fully-qualified") {
			continue
		}
		fields := strings.Fields(description)
		if len(fields) == 0 {
			continue
		}
		glyph := fields[0]
		if seen[glyph] {
			continue
		}
		seen[glyph] = true
		item := record{glyph, category, map[string]string{}, map[string][]string{}}
		for _, locale := range locales {
			entry, ok := catalogs[locale][glyph]
			if !ok {
				entry = catalogs[locale][strings.ReplaceAll(glyph, "\ufe0f", "")]
			}
			if len(entry.TTS) > 0 {
				item.Names[locale] = entry.TTS[0]
			}
			if entry.Default != nil {
				item.Keywords[locale] = entry.Default
			}
		}
		if _, ok := item.Names["en"]; ok {
			records = append(records, item)
		}
	}
	var encoded bytes.Buffer
	encoder := json.NewEncoder(&encoded)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(records); err != nil {
		return nil, err
	}
	prefix := "// Generated by tools/build_emoji_catalog.py from Unicode Emoji 16.0 and CLDR 47.0.0.\n// Unicode data license: https://www.unicode.org/license.txt\nglobalThis.YOMUX_EMOJI_DATA = "
	return []byte(prefix + strings.TrimSuffix(encoded.String(), "\n") + ";\nglobalThis.YOLOMUX_EMOJI_DATA = globalThis.YOMUX_EMOJI_DATA;\n"), nil
}

// VerifyVendorSourceAssets also admits the authored editor inputs before a
// source checkout can reuse prepared bundles. Installed runtimes omit these inputs.
func VerifyVendorSourceAssets(root string) error {
	if err := VerifyVendorAssets(root); err != nil {
		return err
	}
	var browser browserAssetManifest
	if err := readJSON(filepath.Join(root, "assets/runtime/browser-assets.json"), &browser); err != nil {
		return err
	}
	for _, bundle := range browser.Bundles {
		if err := verifyEditorInputs(root, bundle); err != nil {
			return err
		}
	}
	return nil
}

// VerifyVendorAssets checks a prepared runtime without upstream access or writes.
// Generation inputs are deliberately absent from installed runtime trees.
func VerifyVendorAssets(root string) error {
	var browser browserAssetManifest
	if err := readJSON(filepath.Join(root, "assets/runtime/browser-assets.json"), &browser); err != nil {
		return err
	}
	files := map[string]string{}
	for name, file := range browser.Files {
		files[name] = file.SHA256
	}
	for _, archive := range browser.Archives {
		for name, digest := range archive.Files {
			files[name] = digest
		}
	}
	for _, bundle := range browser.Bundles {
		files[bundle.Output] = bundle.SHA256
	}
	for name, digest := range files {
		if !strings.HasPrefix(name, "static/") {
			continue
		}
		path, err := assetOutput(root, name)
		if err != nil {
			return err
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return fmt.Errorf("missing prepared asset %s; run make build: %w", name, err)
		}
		if !validDigest(digest) || vendorDigest(data) != digest {
			return fmt.Errorf("invalid prepared asset %s; run make build", name)
		}
	}
	if browser.EmojiSHA256 != "" {
		emoji, err := os.ReadFile(filepath.Join(root, "static/emoji-data.js"))
		prefix := []byte(fmt.Sprintf(header, "emoji-data.js"))
		if err != nil || !bytes.HasPrefix(emoji, prefix) || vendorDigest(bytes.TrimPrefix(emoji, prefix)) != browser.EmojiSHA256 {
			return errors.New("invalid prepared emoji catalog; run make build")
		}
		for _, name := range []string{"yomux.js", "yomux.css"} {
			path, err := assetOutput(root, "static/"+name)
			if err != nil {
				return err
			}
			body, err := os.ReadFile(path)
			if err != nil || len(body) == 0 {
				return fmt.Errorf("missing generated %s; run make build", name)
			}
		}
		var registry []struct {
			Code string `json:"code"`
		}
		if err := readJSON(filepath.Join(root, "assets/runtime/locale-registry.json"), &registry); err != nil {
			return err
		}
		codes := []string{"en-XA"}
		for _, locale := range registry {
			codes = append(codes, locale.Code)
		}
		for _, code := range codes {
			if filepath.Base(code) != code {
				return errors.New("invalid locale code")
			}
			path, err := assetOutput(root, "static/locales/"+code+".json")
			if err != nil {
				return err
			}
			var catalog map[string]string
			if err := readJSON(path, &catalog); err != nil {
				return err
			}
			if len(catalog) == 0 {
				return fmt.Errorf("empty prepared locale %s", code)
			}
		}
	}
	var pdf vendorManifest
	if err := readJSON(filepath.Join(root, "assets/runtime/pdfjs-assets.json"), &pdf); err != nil {
		return err
	}
	if len(pdf.Files) == 0 || !validVendorDirectory(filepath.Join(root, "static/vendor/pdfjs"), pdf.Files) {
		return errors.New("invalid prepared PDF.js assets; run make build")
	}
	return nil
}
