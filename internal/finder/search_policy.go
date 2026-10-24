// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/keivenchang/yomux/internal/cache"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"syscall"
)

type searchPathRule struct {
	path    string
	matcher *regexp.Regexp
	glob    bool
}
type compiledSearchPolicy struct {
	directoryLimit int
	root           string
	canonicalRoot  string
	names          map[string]struct{}
	rules          []searchPathRule
}

var searchPolicyCache = struct {
	sync.Mutex
	values *cache.LRU[[32]byte, *compiledSearchPolicy]
}{values: cache.NewLRU[[32]byte, *compiledSearchPolicy](64)}

func compileSearchPolicy(root string, names map[string]struct{}, rules []string) (*compiledSearchPolicy, error) {
	canonicalRoot, err := CanonicalIndexRoot(root)
	if err != nil {
		canonicalRoot = filepath.Clean(root)
	}
	return compileSearchPolicyResolved(root, canonicalRoot, names, rules)
}

func compileSearchPolicyResolved(root, canonicalRoot string, names map[string]struct{}, rules []string) (*compiledSearchPolicy, error) {
	normalizedNames := make([]string, 0, len(names))
	for name := range names {
		normalizedNames = append(normalizedNames, name)
	}
	sort.Strings(normalizedNames)
	canonicalRules := make([]string, 0, len(rules))
	for _, rule := range rules {
		canonicalRules = append(canonicalRules, canonicalSearchPolicyRule(root, canonicalRoot, rule))
	}
	encoded, err := json.Marshal(struct {
		Root          string
		CanonicalRoot string
		Names, Rules  []string
	}{filepath.Clean(root), canonicalRoot, normalizedNames, canonicalRules})
	if err != nil {
		return nil, err
	}
	key := sha256.Sum256(encoded)
	searchPolicyCache.Lock()
	defer searchPolicyCache.Unlock()
	if policy, ok := searchPolicyCache.values.Get(key); ok {
		return policy, nil
	}
	policy, err := buildSearchPolicy(root, canonicalRoot, normalizedNames, canonicalRules)
	if err != nil {
		return nil, err
	}
	searchPolicyCache.values.Set(key, policy)
	return policy, nil
}

func canonicalSearchPolicyRule(root, canonicalRoot, raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" || strings.HasPrefix(raw, "glob:") || strings.HasPrefix(raw, "regex:") {
		return raw
	}
	home, _ := os.UserHomeDir()
	if raw == "~" {
		raw = home
	} else if strings.HasPrefix(raw, "~/") {
		raw = filepath.Join(home, raw[2:])
	}
	if !filepath.IsAbs(raw) {
		raw = filepath.Join(root, raw)
	}
	raw = filepath.Clean(raw)
	if relative, err := filepath.Rel(root, raw); err == nil && indexRootWithin(root, raw) {
		raw = filepath.Join(canonicalRoot, relative)
	}
	if canonical, err := CanonicalIndexRoot(raw); err == nil {
		return "path:" + canonical
	}
	return "path:" + raw
}

func buildSearchPolicy(root, canonicalRoot string, names, canonicalRules []string) (*compiledSearchPolicy, error) {
	policyRoot := filepath.Clean(root)
	policy := &compiledSearchPolicy{root: policyRoot, canonicalRoot: filepath.Clean(canonicalRoot), names: make(map[string]struct{}), directoryLimit: MaxDirectoryEntries}
	for name := range searchExcludedDirectoryNames {
		policy.names[name] = struct{}{}
	}
	for _, name := range names {
		policy.names[name] = struct{}{}
	}
	for _, raw := range canonicalRules {
		raw = strings.TrimSpace(raw)
		if raw == "" {
			continue
		}
		rule := searchPathRule{}
		if strings.HasPrefix(raw, "glob:") || strings.HasPrefix(raw, "regex:") {
			rule.glob = strings.HasPrefix(raw, "glob:")
			pattern := strings.TrimSpace(raw[strings.IndexByte(raw, ':')+1:])
			if rule.glob {
				pattern = globPatternRegex(strings.TrimLeft(strings.ReplaceAll(pattern, "\\", "/"), "/"))
			}
			matcher, err := regexp.Compile(pattern)
			if err != nil {
				return nil, fmt.Errorf("invalid search exclusion: %w", err)
			}
			rule.matcher = matcher
		} else {
			// Re-resolving here could cache a retargeted symlink under the previous generation's key.
			rule.path = strings.TrimPrefix(raw, "path:")
		}
		policy.rules = append(policy.rules, rule)
	}
	return policy, nil
}

func (policy *compiledSearchPolicy) excludedSearchRoot() bool {
	for _, name := range strings.Split(filepath.ToSlash(policy.root), "/") {
		if hasName(policy.names, name) {
			return true
		}
	}
	return policy.excludedPath(policy.root)
}

func (policy *compiledSearchPolicy) excludedPath(path string) bool {
	relative, err := filepath.Rel(policy.root, path)
	if err != nil || !indexRootWithin(policy.root, path) {
		return true
	}
	canonicalPath := filepath.Join(policy.canonicalRoot, relative)
	relative = filepath.ToSlash(relative)
	for _, rule := range policy.rules {
		if rule.matcher == nil {
			if indexRootWithin(rule.path, canonicalPath) {
				return true
			}
			continue
		}
		if rule.matcher.MatchString(relative) {
			return true
		}
		if rule.glob {
			for _, value := range []string{"_/" + relative, relative + "/", "_/" + relative + "/"} {
				if rule.matcher.MatchString(value) {
					return true
				}
			}
		}
	}
	return false
}

func boundedSearchHeader(directory *os.File, name string, limit int64) ([]byte, error) {
	file, err := openAt(directory, name, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, errors.New("search header is not a regular file")
	}
	return io.ReadAll(io.LimitReader(file, limit))
}

func bareSearchRepository(directory *os.File) bool {
	head, err := boundedSearchHeader(directory, "HEAD", 1024)
	if err != nil {
		return false
	}
	text := strings.TrimSpace(string(head))
	validHead := strings.HasPrefix(text, "ref: refs/")
	if len(text) == 40 || len(text) == 64 {
		validHead = true
		for _, r := range text {
			if !strings.ContainsRune("0123456789abcdefABCDEF", r) {
				validHead = false
				break
			}
		}
	}
	if !validHead {
		return false
	}
	config, err := boundedSearchHeader(directory, "config", 8192)
	if err != nil {
		return false
	}
	inCore, bare := false, false
	for _, line := range strings.Split(strings.ToLower(string(config)), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "[") {
			inCore = line == "[core]"
			continue
		}
		if inCore {
			key, value, ok := strings.Cut(line, "=")
			if ok && strings.TrimSpace(key) == "bare" && strings.TrimSpace(value) == "true" {
				bare = true
			}
		}
	}
	if !bare {
		return false
	}
	for _, name := range []string{"objects", "refs"} {
		child, err := openAt(directory, name, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
		if err != nil {
			return false
		}
		child.Close()
	}
	// Real Git repositories have at least one object namespace, even when empty.
	objects, err := openAt(directory, "objects", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return false
	}
	defer objects.Close()
	for _, name := range []string{"info", "pack"} {
		child, err := openAt(objects, name, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
		if err == nil {
			child.Close()
			return true
		}
	}
	return false
}

func excludedSearchFile(name string, file *os.File) (bool, error) {
	lower := strings.ToLower(name)
	if strings.HasPrefix(lower, "search-index.sqlite3") || strings.Contains(lower, ".sqlite3.candidate-") || strings.Contains(lower, ".sqlite3.rollback-") {
		return true, nil
	}
	switch strings.ToLower(filepath.Ext(name)) {
	case ".o", ".obj", ".a", ".lib", ".so", ".dylib", ".dll", ".exe", ".pyc", ".pyo", ".class", ".wasm", ".elf", ".node", ".bc", ".cubin", ".fatbin":
		return true, nil
	}
	if index := strings.LastIndex(lower, ".so."); index >= 0 {
		version := lower[index+4:]
		valid := version != ""
		for _, value := range version {
			if value != '.' && (value < '0' || value > '9') {
				valid = false
				break
			}
		}
		if valid {
			return true, nil
		}
	}
	if filepath.Ext(name) != "" {
		return false, nil
	}
	var header [64]byte
	count, err := file.ReadAt(header[:], 0)
	if err != nil && !errors.Is(err, io.EOF) {
		return false, err
	}
	if count >= 4 {
		if string(header[:4]) == "\x7fELF" {
			return true, nil
		}
		magic := binary.BigEndian.Uint32(header[:4])
		switch magic {
		case 0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe, 0xcafebabe, 0xbebafeca, 0xcafebabf, 0xbfbafeca:
			return true, nil
		}
	}
	if count >= 64 && string(header[:2]) == "MZ" {
		offset := int64(binary.LittleEndian.Uint32(header[60:64]))
		// Keep every binary probe bounded; enormous offsets are not useful headers.
		if offset <= 4096 {
			var signature [4]byte
			n, err := file.ReadAt(signature[:], offset)
			if err != nil && !errors.Is(err, io.EOF) {
				return false, err
			}
			if n == 4 && string(signature[:]) == "PE\x00\x00" {
				return true, nil
			}
		}
	}
	return false, nil
}

type searchDirectoryNode struct {
	parent         *searchDirectoryNode
	name, relative string
	identity       descriptorIdentity
}

func openQueuedSearchDirectory(root *os.File, node *searchDirectoryNode) (*os.File, error) {
	var ancestry []*searchDirectoryNode
	for current := node; current.parent != nil; current = current.parent {
		ancestry = append(ancestry, current)
	}
	directory, err := openAt(root, ".", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, err
	}
	for index := len(ancestry) - 1; index >= 0; index-- {
		current := ancestry[index]
		child, err := openAt(directory, current.name, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
		directory.Close()
		if err != nil {
			return nil, err
		}
		info, err := child.Stat()
		if err != nil || !sameDescriptorIdentity(identityFor(info), current.identity) {
			child.Close()
			return nil, errChangedOnDisk
		}
		directory = child
	}
	return directory, nil
}

// Discovery and recursive fallback search share this descriptor-pinned bounded BFS.
func walkSearchDescriptors(ctx context.Context, root *os.File, requested, resolved string, policy *compiledSearchPolicy, callback func(walkedEntry) error) error {
	if policy.excludedSearchRoot() || bareSearchRepository(root) {
		return nil
	}
	info, err := root.Stat()
	if err != nil {
		return err
	}
	queue := []*searchDirectoryNode{{identity: identityFor(info)}}
	queuedBytes := int64(0)
	directories := 0
	for len(queue) > 0 {
		if err := ctx.Err(); err != nil {
			return err
		}
		node := queue[0]
		queue[0] = nil
		queue = queue[1:]
		queuedBytes -= int64(len(node.relative))
		directories++
		if directories > indexQueueLimit {
			return errors.New("search total directory limit reached")
		}
		directory, err := openQueuedSearchDirectory(root, node)
		if err != nil {
			return fmt.Errorf("search directory %s: %w", node.relative, err)
		}
		entries, readErr := directory.ReadDir(policy.directoryLimit + 1)
		if readErr != nil && !errors.Is(readErr, io.EOF) {
			directory.Close()
			return readErr
		}
		limited := len(entries) > policy.directoryLimit
		if limited {
			entries = entries[:policy.directoryLimit]
		}
		sort.Slice(entries, func(a, b int) bool { return entries[a].Name() < entries[b].Name() })
		for _, entry := range entries {
			if err := ctx.Err(); err != nil {
				directory.Close()
				return err
			}
			name := entry.Name()
			relative := filepath.Join(node.relative, name)
			path := filepath.Join(requested, relative)
			if policy.excludedPath(path) {
				continue
			}
			linkInfo, err := lstatAt(directory, name)
			if err != nil {
				directory.Close()
				return err
			}
			if linkInfo.Mode()&os.ModeSymlink != 0 || (!linkInfo.Mode().IsRegular() && !linkInfo.IsDir()) {
				continue
			}
			if linkInfo.IsDir() && hasName(policy.names, name) {
				continue
			}
			flags := syscall.O_RDONLY | syscall.O_NOFOLLOW | syscall.O_NONBLOCK
			if linkInfo.IsDir() {
				flags |= syscall.O_DIRECTORY
			}
			child, err := openAt(directory, name, flags, 0)
			if err != nil {
				directory.Close()
				return err
			}
			actual, err := child.Stat()
			if err != nil || !sameDescriptorIdentity(identityFor(linkInfo), identityFor(actual)) {
				child.Close()
				directory.Close()
				return errChangedOnDisk
			}
			excluded := false
			if actual.IsDir() {
				excluded = bareSearchRepository(child)
			} else {
				excluded, err = excludedSearchFile(name, child)
			}
			if err != nil {
				child.Close()
				directory.Close()
				return err
			}
			if excluded {
				child.Close()
				continue
			}
			callbackErr := callback(walkedEntry{Name: name, Relative: filepath.ToSlash(relative), Path: path, Resolved: filepath.Join(resolved, relative), File: child, Info: actual, IsDir: actual.IsDir()})
			child.Close()
			if errors.Is(callbackErr, errWalkSkipDirectory) {
				continue
			}
			if callbackErr != nil {
				directory.Close()
				return callbackErr
			}
			if actual.IsDir() {
				if !indexDirectoryQueueFits(len(queue)+1, queuedBytes, path, relative) {
					directory.Close()
					return errors.New("search directory queue limit reached")
				}
				queue = append(queue, &searchDirectoryNode{parent: node, name: name, relative: relative, identity: identityFor(actual)})
				queuedBytes += int64(len(relative))
			}
		}
		directory.Close()
		if limited {
			return errWalkDirectoryLimit
		}
	}
	return nil
}

func openIndexedSearchFile(root *os.File, relative string, policy *compiledSearchPolicy, bareCache map[string]bool) (*os.File, error) {
	return openIndexedSearchPath(root, relative, policy, bareCache, false)
}

func openIndexedSearchDirectory(root *os.File, relative string, policy *compiledSearchPolicy, bareCache map[string]bool) (*os.File, error) {
	return openIndexedSearchPath(root, relative, policy, bareCache, true)
}

func openIndexedSearchPath(root *os.File, relative string, policy *compiledSearchPolicy, bareCache map[string]bool, directory bool) (*os.File, error) {
	path := filepath.Join(policy.root, relative)
	if policy.excludedSearchRoot() || !indexRootWithin(policy.root, path) || policy.excludedPath(path) {
		return nil, os.ErrNotExist
	}
	parts := strings.Split(filepath.ToSlash(relative), "/")
	current, err := openAt(root, ".", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, err
	}
	for index, name := range parts {
		info, err := current.Stat()
		if err != nil {
			current.Close()
			return nil, err
		}
		identity := fileID(info)
		bare, known := bareCache[identity]
		if !known {
			bare = bareSearchRepository(current)
			bareCache[identity] = bare
		}
		if bare {
			current.Close()
			return nil, os.ErrNotExist
		}
		final := index == len(parts)-1
		if name == "" || name == "." || name == ".." || (!final || directory) && hasName(policy.names, name) {
			current.Close()
			return nil, os.ErrNotExist
		}
		flags := syscall.O_RDONLY | syscall.O_NOFOLLOW | syscall.O_NONBLOCK
		if !final || directory {
			flags |= syscall.O_DIRECTORY
		}
		child, err := openAt(current, name, flags, 0)
		current.Close()
		if err != nil {
			return nil, err
		}
		current = child
	}
	info, err := current.Stat()
	if err != nil || directory && !info.IsDir() || !directory && !info.Mode().IsRegular() {
		current.Close()
		return nil, os.ErrNotExist
	}
	if directory {
		if bareSearchRepository(current) {
			current.Close()
			return nil, os.ErrNotExist
		}
	} else {
		excluded, err := excludedSearchFile(filepath.Base(relative), current)
		if err != nil || excluded {
			current.Close()
			return nil, os.ErrNotExist
		}
	}
	return current, nil
}
