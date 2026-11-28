// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// A compiled TeX preview is reused only while everything the compile read is unchanged: the
// compiler binary, the source the browser sent, the document directory, and every document file
// the compiler reported reading. Files from the shared TeX package cache are not tracked; their
// content is pinned by the package bundle the compiler version uses.

const texPreviewCacheSchema = "yomux-tex-preview-v1"

// texInputHashLimit bounds the bytes hashed for one recorded input; a larger input is not cached.
const texInputHashLimit = 64 << 20

type texPreviewKey struct {
	id string
	// project is the resolved document directory, or "" for a compile without one.
	project string
}

type texPreviewInput struct {
	path      string
	info      os.FileInfo
	directory bool
	digest    [32]byte
}

type texPreviewEntry struct {
	syncPages []texSyncPage
	inputs    []texPreviewInput
}

func fileIdentity(info os.FileInfo) string {
	return fmt.Sprintf("%d:%d:%d", info.Size(), info.ModTime().UnixNano(), fileChangeTimeNS(info))
}

// texPreviewCacheKey names a compile of content at path with compiler. It fails when the compiler
// or document directory cannot be identified, and then nothing is cached.
func texPreviewCacheKey(compiler, kind, path, content string) (texPreviewKey, error) {
	digest := sha256.New()
	compilerInfo, err := os.Stat(compiler)
	if err != nil {
		return texPreviewKey{}, err
	}
	fmt.Fprintf(digest, "%s\x00%s\x00%s\x00%s\x00", texPreviewCacheSchema, kind, compiler, fileIdentity(compilerInfo))
	key := texPreviewKey{}
	if path != "" {
		key.project, err = filepath.EvalSymlinks(filepath.Dir(path))
		if err != nil {
			return texPreviewKey{}, err
		}
		projectInfo, err := os.Stat(key.project)
		if err != nil {
			return texPreviewKey{}, err
		}
		if fileChangeTimeNS(projectInfo) == 0 {
			return texPreviewKey{}, errors.New("document directory has no change time")
		}
		fmt.Fprintf(digest, "%s\x00%s\x00", key.project, fileIdentity(projectInfo))
	}
	fmt.Fprintf(digest, "%d\x00", len(content))
	if _, err := io.WriteString(digest, content); err != nil {
		return texPreviewKey{}, err
	}
	key.id = memoryCacheTeX + ":" + hex.EncodeToString(digest.Sum(nil))
	return key, nil
}

// lookupTexPreview returns a cached compile whose recorded inputs are all unchanged. A changed
// input drops the entry. count is false for a second look after waiting for the compiler.
func (s *server) lookupTexPreview(key texPreviewKey, count bool) (texCompileOutput, bool) {
	output, ok := s.validTexPreview(key)
	if count {
		s.memoryCache.count(memoryCacheTeX, ok)
	}
	return output, ok
}

func (s *server) validTexPreview(key texPreviewKey) (texCompileOutput, bool) {
	pdf, meta, identity, ok := s.memoryCache.get(key.id)
	if !ok {
		return texCompileOutput{}, false
	}
	entry, ok := meta.(texPreviewEntry)
	if !ok {
		s.memoryCache.removeIfSame(key.id, identity)
		return texCompileOutput{}, false
	}
	entry.inputs = append([]texPreviewInput(nil), entry.inputs...)
	for index := range entry.inputs {
		if !entry.inputs[index].unchanged() {
			s.memoryCache.removeIfSame(key.id, identity)
			return texCompileOutput{}, false
		}
	}
	s.memoryCache.mu.Lock()
	if s.memoryCache.entries[key.id] == identity {
		identity.meta = entry
	}
	s.memoryCache.mu.Unlock()
	return texCompileOutput{pdf: pdf, syncPages: entry.syncPages, inputsKnown: true}, true
}

// unchanged compares the input's stat fields and, for a file whose fields moved, its content.
func (input *texPreviewInput) unchanged() bool {
	current, err := os.Stat(input.path)
	if err != nil || current.IsDir() != input.directory {
		return false
	}
	if sameFileVersion(input.info, current) {
		return true
	}
	if input.directory {
		return false
	}
	digest, err := hashTexInput(input.path)
	if err != nil || digest != input.digest {
		return false
	}
	after, err := os.Stat(input.path)
	if err != nil || !sameFileVersion(current, after) {
		return false
	}
	input.info = after
	return true
}

func hashTexInput(path string) ([32]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return [32]byte{}, err
	}
	digest := sha256.New()
	copied, copyErr := io.Copy(digest, io.LimitReader(file, texInputHashLimit+1))
	if err := errors.Join(copyErr, file.Close()); err != nil {
		return [32]byte{}, err
	}
	if copied > texInputHashLimit {
		return [32]byte{}, errors.New("TeX input exceeds the hash limit")
	}
	return [32]byte(digest.Sum(nil)), nil
}

// storeTexPreview caches a successful compile whose inputs the compiler reported. started is when
// the compile began; an input that changed around then may not match what the compiler read, so
// the PDF is not cached.
func (s *server) storeTexPreview(key texPreviewKey, output texCompileOutput, started time.Time) {
	entry, err := recordTexPreviewInputs(key.project, output.inputs, output.inputsKnown, started)
	if err != nil {
		s.memoryCache.skip(memoryCacheTeX)
		return
	}
	entry.syncPages = output.syncPages
	size := int64(len(output.pdf)) + int64(len(output.syncPages))*16
	for _, input := range entry.inputs {
		size += int64(len(input.path)) + 128
	}
	s.memoryCache.put(memoryCacheTeX, key.id, output.pdf, entry, size)
}

func recordTexPreviewInputs(project string, paths []string, known bool, started time.Time) (texPreviewEntry, error) {
	if !known {
		return texPreviewEntry{}, errors.New("compiler did not report its inputs")
	}
	if project == "" {
		if len(paths) > 0 {
			return texPreviewEntry{}, errors.New("inputs without a document directory")
		}
		return texPreviewEntry{}, nil
	}
	entry := texPreviewEntry{}
	directories := map[string]bool{}
	record := func(path string, directory bool) error {
		info, err := os.Stat(path)
		if err != nil {
			return err
		}
		if info.IsDir() != directory {
			return fmt.Errorf("TeX input changed type: %s", path)
		}
		if !fileVersionSettled(info, started) {
			return fmt.Errorf("TeX input changed while compiling: %s", path)
		}
		input := texPreviewInput{path: path, info: info, directory: directory}
		if !directory {
			if input.digest, err = hashTexInput(path); err != nil {
				return err
			}
			// A change during hashing would pair one version's digest with another's stat.
			after, err := os.Stat(path)
			if err != nil || !sameFileVersion(info, after) {
				return fmt.Errorf("TeX input changed while recording: %s", path)
			}
		}
		entry.inputs = append(entry.inputs, input)
		return nil
	}
	// The document directory and every input's directory are recorded too, so a file the
	// document only probed for (\IfFileExists) and that appears later forces a compile.
	addDirectory := func(path string) error {
		if directories[path] {
			return nil
		}
		directories[path] = true
		return record(path, true)
	}
	if err := addDirectory(project); err != nil {
		return texPreviewEntry{}, err
	}
	for _, path := range paths {
		if err := addDirectory(filepath.Dir(path)); err != nil {
			return texPreviewEntry{}, err
		}
		if err := record(path, false); err != nil {
			return texPreviewEntry{}, err
		}
	}
	return entry, nil
}

func pathWithin(root, path string) bool {
	relative, err := filepath.Rel(root, path)
	return err == nil && filepath.IsLocal(relative)
}

// texMakefileInputs reads the rule Tectonic writes with --makefile-rules. Tectonic names each
// document file as the output directory joined with the name TeX opened, relative to project.
func texMakefileInputs(rules, outdir, project string) ([]string, error) {
	data, err := os.ReadFile(rules)
	if err != nil {
		return nil, err
	}
	text := strings.ReplaceAll(string(data), "\\\n", " ")
	separator := strings.Index(text, " :")
	if separator < 0 {
		return nil, errors.New("Tectonic dependency rule has no separator")
	}
	var inputs []string
	seen := map[string]bool{}
	for _, token := range makefileWords(text[separator+2:]) {
		name, found := strings.CutPrefix(token, outdir+string(filepath.Separator))
		if !found || filepath.IsAbs(name) || !filepath.IsLocal(name) {
			return nil, fmt.Errorf("Tectonic input outside the document directory: %s", token)
		}
		if project == outdir {
			return nil, errors.New("Tectonic read files without a document directory")
		}
		path := filepath.Join(project, name)
		if !seen[path] {
			seen[path] = true
			inputs = append(inputs, path)
		}
	}
	return inputs, nil
}

// makefileWords splits Makefile prerequisites, undoing backslash and dollar escapes.
func makefileWords(text string) []string {
	var words []string
	var word strings.Builder
	flush := func() {
		if word.Len() > 0 {
			words = append(words, word.String())
			word.Reset()
		}
	}
	for index := 0; index < len(text); index++ {
		switch character := text[index]; {
		case character == '\\' && index+1 < len(text):
			index++
			word.WriteByte(text[index])
		case character == '$' && index+1 < len(text) && text[index+1] == '$':
			index++
			word.WriteByte('$')
		case character == ' ' || character == '\t' || character == '\n' || character == '\r':
			flush()
		default:
			word.WriteByte(character)
		}
	}
	flush()
	return words
}

// texRecorderInputs reads the pdfLaTeX -recorder file list. Files under project are document
// inputs; the job's own files and the TeX installation are not tracked.
func texRecorderInputs(recorder, job, project string) ([]string, error) {
	file, err := os.Open(recorder)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	working := job
	var inputs []string
	seen := map[string]bool{}
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64<<10), 1<<20)
	for scanner.Scan() {
		line := scanner.Text()
		if value, found := strings.CutPrefix(line, "PWD "); found {
			working = value
			continue
		}
		value, found := strings.CutPrefix(line, "INPUT ")
		if !found {
			continue
		}
		path := value
		if !filepath.IsAbs(path) {
			path = filepath.Join(working, path)
		}
		path = filepath.Clean(path)
		if pathWithin(job, path) || project == job || !pathWithin(project, path) || seen[path] {
			continue
		}
		seen[path] = true
		inputs = append(inputs, path)
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	return inputs, nil
}
